"""Minimal ProjectX Gateway client (TopstepX).

Endpoints and payload shapes are from the ProjectX Gateway docs:

    POST /api/Auth/loginKey   {userName, apiKey} -> {token, success, errorCode}
    POST /api/Order/place     {accountId, contractId, type, side, size, ...}
                              -> {orderId, success, errorCode, errorMessage}

Two things about that order payload are easy to get wrong and expensive:

* **Brackets are in TICKS, not prices.** ``stopLossBracket.ticks`` is a
  distance from the entry, so an absolute stop price has to be converted with
  the instrument's tick size. Passing a price straight through would place a
  stop thousands of ticks away and it would look like it worked.
* ``side`` is 0 for buy and 1 for sell, and ``type`` is 1 for limit -- both are
  bare integers, so a wrong one is a valid request for the wrong trade.

``DryRunBroker`` implements the same surface and records what it would have
sent. It is the default; sending real orders takes an explicit --live.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import Config

ORDER_TYPE_LIMIT = 1
SIDE_BUY = 0
SIDE_SELL = 1


class BrokerError(RuntimeError):
    pass


@dataclass
class Order:
    """A bracket order, in the terms this bridge speaks."""

    side: str          # "buy" or "sell"
    size: int
    entry: float
    stop: float
    target: float
    tag: str = ""


def bracket_ticks(entry: float, level: float, tick_size: float) -> int:
    """Distance from entry to a bracket level, in ticks, always positive."""
    ticks = round(abs(entry - level) / tick_size)
    if ticks < 1:
        raise BrokerError(
            f"bracket level {level} is less than one tick ({tick_size}) from the "
            f"entry {entry}; refusing to send a zero-distance stop or target"
        )
    return int(ticks)


def order_payload(order: Order, cfg: Config) -> dict[str, Any]:
    """Build the /api/Order/place body. Pure, so the tests can read it."""
    if order.size < 1:
        raise BrokerError(f"refusing to send an order for {order.size} contracts")
    if cfg.account_id is None:
        raise BrokerError("no account id; set TOPSTEPX_ACCOUNT_ID")
    return {
        "accountId": cfg.account_id,
        "contractId": cfg.contract_id,
        "type": ORDER_TYPE_LIMIT,
        "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
        "size": int(order.size),
        "limitPrice": round(order.entry, 4),
        "stopLossBracket": {"ticks": bracket_ticks(order.entry, order.stop, cfg.tick_size),
                            "type": ORDER_TYPE_LIMIT},
        "takeProfitBracket": {"ticks": bracket_ticks(order.entry, order.target, cfg.tick_size),
                              "type": ORDER_TYPE_LIMIT},
        "customTag": order.tag[:50],
    }


@dataclass
class DryRunBroker:
    """Records orders instead of sending them. The default."""

    cfg: Config
    sent: list[dict[str, Any]] = field(default_factory=list)

    def authenticate(self) -> None:
        return None

    def place(self, order: Order) -> dict[str, Any]:
        payload = order_payload(order, self.cfg)
        self.sent.append(payload)
        return {"orderId": -len(self.sent), "success": True, "errorCode": 0,
                "errorMessage": None, "dryRun": True}


@dataclass
class TopstepXBroker:
    """Live client. Only constructed when --live is passed."""

    cfg: Config
    token: str | None = None

    def _post(self, path: str, body: dict[str, Any], auth: bool = True) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.cfg.base_url}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "accept": "application/json"},
            method="POST",
        )
        if auth:
            if not self.token:
                raise BrokerError("not authenticated")
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                out = json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            # 429 is rate limiting; the caller decides whether to back off. The
            # body is echoed because ProjectX puts the real reason in it.
            raise BrokerError(f"{path} -> HTTP {exc.code}: {exc.read()[:400]!r}") from exc
        except urllib.error.URLError as exc:
            raise BrokerError(f"{path} -> {exc.reason}") from exc
        if not out.get("success", False):
            raise BrokerError(
                f"{path} -> errorCode {out.get('errorCode')}: {out.get('errorMessage')}"
            )
        return out

    def authenticate(self) -> None:
        out = self._post("/api/Auth/loginKey",
                         {"userName": self.cfg.username, "apiKey": self.cfg.api_key},
                         auth=False)
        self.token = out["token"]

    def place(self, order: Order) -> dict[str, Any]:
        return self._post("/api/Order/place", order_payload(order, self.cfg))

    def accounts(self) -> list[dict[str, Any]]:
        """List tradable accounts, to find the account id.

        NOTE: this endpoint's exact path and body could not be verified against
        the published docs, unlike loginKey and Order/place above. Prefer
        setting TOPSTEPX_ACCOUNT_ID explicitly and treat this as a convenience
        that may need correcting.
        """
        out = self._post("/api/Account/search", {"onlyActiveAccounts": True})
        return out.get("accounts", [])
