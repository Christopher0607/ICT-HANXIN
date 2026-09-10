"""Receive TradingView webhooks and turn them into bracket orders.

    uv run python -m bridge.server                # dry run, the default
    uv run python -m bridge.server --live         # actually sends orders

Read tradingview/README.md before pointing this at a funded account: Topstep
prohibits API automation on the Live Funded Account and prohibits running it
from a VPS or remote server. Get that policy in writing from the firm first.
This exists so the plumbing can be built and proven while you wait.

The payload is whatever the Pine ``alertTemplate`` emits:

    {"strategy":"ltf_sweep","action":"buy","symbol":"MNQ1!","qty":2,
     "entry":29428.25,"stop":29480.0,"target":29376.5,
     "risk_points":51.75,"time":"2026-08-31T14:18:00Z"}

Three things this does that the chart cannot be trusted to do:

* re-checks every prop-firm rule (see guards.py)
* refuses a replayed alert -- TradingView resends, and a duplicate here is a
  duplicate position
* refuses anything whose shared secret does not match, compared in constant
  time
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from .config import Config
from .guards import REASONS, AccountState, block_reason, clamp_size, scaling_cap
from .journal import DEFAULT_PATH, Journal
from .topstepx import BrokerError, DryRunBroker, Order, TopstepXBroker

log = logging.getLogger("bridge")

MAX_BODY = 8192
ENTRY_ACTIONS = {"buy", "sell"}
EXIT_ACTIONS = {"exit", "close", "exit_stop", "exit_target"}


class Rejected(Exception):
    """The alert was understood and deliberately not acted on."""


def parse_alert(raw: bytes) -> dict:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Rejected(f"body is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise Rejected("body is not a JSON object")
    action = str(payload.get("action", "")).lower()
    if action not in ENTRY_ACTIONS | EXIT_ACTIONS:
        raise Rejected(f"unknown action {action!r}")
    payload["action"] = action
    return payload


def alert_key(payload: dict) -> str:
    """Identity of an alert, for refusing replays.

    Keyed on the signal's own timestamp and side rather than arrival time: the
    same alert redelivered an hour later is still the same alert, and a genuine
    second signal has a different bar time.
    """
    return f"{payload.get('time', '')}|{payload['action']}|{payload.get('symbol', '')}"


class Bridge:
    """Alert in, order out, with the rules applied in between."""

    def __init__(self, cfg: Config, broker, journal: Journal | None = None) -> None:
        self.cfg = cfg
        self.broker = broker
        self.journal = journal or Journal(None)
        self.state = AccountState()
        self.seen: set[str] = set()

    def handle(self, payload: dict) -> dict:
        key = alert_key(payload)
        if key in self.seen:
            self.journal.write("duplicate", key=key)
            raise Rejected(f"duplicate alert {key}")

        if payload["action"] in EXIT_ACTIONS:
            # Exits ride on the bracket sent with the entry, so there is
            # nothing to place. Recorded so the log shows the round trip.
            self.seen.add(key)
            self.journal.write("exit_alert", key=key, action=payload["action"])
            return {"status": "noted", "action": payload["action"]}

        try:
            qty = int(payload["qty"])
            entry = float(payload["entry"])
            stop = float(payload["stop"])
            target = float(payload["target"])
        except (KeyError, TypeError, ValueError) as exc:
            raise Rejected(f"missing or malformed order fields: {exc}") from exc

        day = str(payload.get("time", ""))[:10] or datetime.now(timezone.utc).date().isoformat()
        self.state.roll_day(day, self.cfg)

        sized = clamp_size(qty, self.cfg, realized=self.state.realized)
        if sized < 1:
            self.journal.write("skipped", key=key, why="size clamps to zero", qty=qty)
            raise Rejected(f"size {qty} clamps to {sized}")
        if sized != qty:
            log.warning("size %d over the %d ceiling, cut to %d",
                        qty, scaling_cap(self.state.realized, self.cfg), sized)

        planned_risk = abs(entry - stop) * self.cfg.point_value * sized
        reason = block_reason(self.state, planned_risk, self.cfg)
        if reason:
            self.journal.write("blocked", key=key, reason=REASONS[reason], code=reason,
                               qty=sized, entry=entry, stop=stop, target=target,
                               equity=round(self.state.equity(self.cfg), 2),
                               floor=round(self.state.mll_floor, 2),
                               planned_risk=round(planned_risk, 2))
            raise Rejected(
                f"{REASONS[reason]} (equity ${self.state.equity(self.cfg):,.0f}, "
                f"floor ${self.state.mll_floor:,.0f}, this trade risks ${planned_risk:,.0f})"
            )

        order = Order(side=payload["action"], size=sized, entry=entry, stop=stop,
                      target=target, tag=f"ltf_sweep {day}")
        try:
            result = self.broker.place(order)
        except BrokerError as exc:
            self.journal.write("broker_error", key=key, error=str(exc), qty=sized,
                               entry=entry, stop=stop, target=target)
            raise
        self.seen.add(key)
        self.state.trades_today += 1
        self.journal.write("placed", key=key, side=order.side, qty=sized,
                           requested_qty=qty, entry=entry, stop=stop, target=target,
                           planned_risk=round(planned_risk, 2), day=day,
                           dry_run=bool(result.get("dryRun")), broker=result)
        log.info("placed %s %d @ %.2f stop %.2f target %.2f -> %s",
                 order.side, order.size, entry, stop, target, result)
        return {"status": "placed", "order": result}


def make_handler(bridge: Bridge, secret: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _reply(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self, payload: dict) -> bool:
            # TradingView cannot set custom headers on every plan, so the secret
            # may also travel in the body. Compared in constant time either way.
            supplied = self.headers.get("X-Webhook-Secret") or str(payload.get("secret", ""))
            return hmac.compare_digest(supplied, secret)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._reply(413, {"error": "body too large"})
                return
            raw = self.rfile.read(length)
            try:
                payload = parse_alert(raw)
            except Rejected as exc:
                log.warning("rejected: %s", exc)
                self._reply(400, {"error": str(exc)})
                return
            if not self._authorised(payload):
                log.warning("rejected: bad or missing secret")
                self._reply(401, {"error": "bad secret"})
                return
            payload.pop("secret", None)
            try:
                self._reply(200, bridge.handle(payload))
            except Rejected as exc:
                log.warning("not acted on: %s", exc)
                self._reply(200, {"status": "skipped", "reason": str(exc)})
            except BrokerError as exc:
                log.error("broker error: %s", exc)
                self._reply(502, {"error": str(exc)})

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="send real orders; without it nothing leaves this process")
    ap.add_argument("--preflight", action="store_true",
                    help="check everything a live order depends on, send nothing, exit")
    ap.add_argument("--offline", action="store_true",
                    help="with --preflight, skip the checks that need the broker")
    ap.add_argument("--journal", default=str(DEFAULT_PATH),
                    help="JSONL record of every decision; empty string disables it")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = Config.from_env(live=args.live)
    except RuntimeError as exc:
        # Preflight's whole job is to report this kindly rather than traceback.
        print(f"  [FAIL] configuration  {exc}")
        print("\nPREFLIGHT FAILED -- configuration incomplete")
        return 1

    if args.preflight:
        from . import preflight
        print(f"preflight: {cfg.preset} on {cfg.base_url}\n")
        return 0 if preflight.report(
            preflight.run(cfg, reach_broker=not args.offline)) else 1

    broker = TopstepXBroker(cfg) if args.live else DryRunBroker(cfg)
    if args.live:
        log.warning("LIVE: orders will be sent to %s account %s",
                    cfg.base_url, cfg.account_id)
        broker.authenticate()
    else:
        log.info("dry run: orders are recorded, not sent. Pass --live to send.")
    log.info("%s | start $%s | target $%s | loss limit $%s | max %d contracts",
             cfg.preset, cfg.account_start, cfg.profit_target,
             cfg.max_loss_limit, cfg.max_contracts)

    journal = Journal(args.journal or None)
    if journal.path:
        log.info("journalling every decision to %s", journal.path)
    server = HTTPServer((args.host, args.port),
                        make_handler(Bridge(cfg, broker, journal), cfg.webhook_secret))
    log.info("listening on http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
