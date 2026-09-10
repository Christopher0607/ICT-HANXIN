"""Everything that can fail on the way to a live order, checked without one.

The failure this exists to prevent is the expensive kind: a contract id left
on last quarter's month, an account id that belongs to a different account, a
bracket distance computed in dollars instead of ticks. All of them produce a
request the API accepts and a position nobody intended.

Nothing here sends an order, and nothing here prints a credential.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from ict import data as D
from strategies.ltf_sweep import LTFSweepConfig, generate_orders

from .config import PRESETS, Config
from .guards import scaling_cap
from .live import day_start
from .topstepx import Order, TopstepXBroker, order_payload


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _mask(value: str) -> str:
    """Enough to tell two keys apart, not enough to use one."""
    return f"{len(value)} chars ending {value[-4:]}" if len(value) > 8 else "(too short)"


def run(cfg: Config, reach_broker: bool = True, need_webhook: bool = True) -> list[Check]:
    """Every check, in the order they would bite. Never raises.

    ``need_webhook`` is false for the local signal engine, which has no inbound
    surface: demanding a secret it never reads would only teach people to set a
    dummy one, and a dummy is what you get on the day it matters.
    """
    out: list[Check] = []

    out.append(Check("api key present", bool(cfg.api_key),
                     _mask(cfg.api_key) if cfg.api_key else "TOPSTEPX_API_KEY is empty"))
    out.append(Check("username present", bool(cfg.username), cfg.username or "empty"))

    if need_webhook:
        weak = {"", "changeme", "secret", "password"}
        out.append(Check("webhook secret set", cfg.webhook_secret.lower() not in weak
                         and len(cfg.webhook_secret) >= 12,
                         f"{len(cfg.webhook_secret)} chars"
                         if cfg.webhook_secret else "BRIDGE_WEBHOOK_SECRET is empty"))

    out.append(Check("preset known", cfg.preset in PRESETS,
                     f"{cfg.preset}: target ${cfg.profit_target:,.0f}, "
                     f"loss limit ${cfg.max_loss_limit:,.0f}, "
                     f"daily ${cfg.daily_loss_limit:,.0f}, "
                     f"{cfg.max_contracts} micros"))

    cap = scaling_cap(0.0, cfg)
    out.append(Check("contract ceiling", cap >= 1,
                     f"{cap} micros at $0 balance"
                     + (" (Express Funded scaling ON)" if cfg.scaling_plan
                        else " (flat evaluation cap)")))

    out.append(Check("session window", 0 < cfg.cutoff_minute <= 16 * 60,
                     f"places orders until {cfg.cutoff_minute // 60:02d}:"
                     f"{cfg.cutoff_minute % 60:02d} ET, then cancels what has not "
                     "filled"))
    out.append(Check("data subscription", True,
                     ("live -- correct for a funded account" if cfg.live_data
                      else "sim -- correct for a Practice Account. A funded account "
                           "on this setting gets an empty bar array, not an error")))

    out.append(Check("guard posture", True,
                     ("ON -- blocks near the loss limit" if cfg.use_guard
                      else "OFF -- will trade into the loss limit; correct only for "
                           "an evaluation you would re-buy")))

    # The bracket arithmetic, shown rather than trusted. A stop 20 points from
    # a 100.00 entry on a 0.25 tick is 80 ticks; anything else here is wrong.
    try:
        sample = Order(side="sell", size=2, entry=100.0, stop=120.0, target=80.0)
        payload = order_payload(sample, cfg) if cfg.account_id else None
        if payload:
            sl = payload["stopLossBracket"]["ticks"]
            tp = payload["takeProfitBracket"]["ticks"]
            expected = int(round(20.0 / cfg.tick_size))
            out.append(Check("bracket ticks", sl == expected and tp == expected,
                             f"20.00 points at tick {cfg.tick_size} -> {sl} ticks "
                             f"(expected {expected})"))
            out.append(Check("sample order payload", True,
                             json.dumps(payload, sort_keys=True)))
        else:
            out.append(Check("bracket ticks", False,
                             "cannot build a payload without TOPSTEPX_ACCOUNT_ID"))
    except Exception as exc:                       # noqa: BLE001 - report, never raise
        out.append(Check("bracket ticks", False, f"{type(exc).__name__}: {exc}"))

    if not reach_broker:
        return out

    broker = TopstepXBroker(cfg)
    try:
        broker.authenticate()
        out.append(Check("authenticate", True, "session token obtained"))
    except Exception as exc:                       # noqa: BLE001
        out.append(Check("authenticate", False, f"{type(exc).__name__}: {exc}"))
        return out

    try:
        accounts = broker.accounts()
        ids = {int(a.get("id")): a for a in accounts if a.get("id") is not None}
        if cfg.account_id is None:
            out.append(Check("account id", False,
                             "TOPSTEPX_ACCOUNT_ID is not set. Visible accounts: "
                             + ", ".join(str(i) for i in ids) or "none"))
        elif cfg.account_id in ids:
            a = ids[cfg.account_id]
            out.append(Check("account id", True,
                             f"{a.get('name', cfg.account_id)} "
                             f"balance ${float(a.get('balance', 0)):,.2f} "
                             f"canTrade={a.get('canTrade')}"))
        else:
            out.append(Check("account id", False,
                             f"{cfg.account_id} is not among {sorted(ids)}"))
    except Exception as exc:                       # noqa: BLE001
        # Account/search was never verified against the published docs, so a
        # failure here is as likely to be this client as the configuration.
        out.append(Check("account id", False,
                         f"could not list accounts ({type(exc).__name__}: {exc}). "
                         "This endpoint is unverified -- confirm the id by hand."))

    out.extend(bar_feed_checks(cfg, broker))
    return out


def bar_feed_checks(cfg: Config, broker) -> list[Check]:
    """Prove the bar half of the round trip before an order depends on it.

    A wrong ``contractId`` and a wrong ``live`` flag both come back as an empty
    bar array rather than an error, and a feed stamped at the close rather than
    the open shifts every signal by one bar while looking perfectly healthy. All
    three are invisible until they have cost money, and all three are visible
    here in one request.
    """
    out: list[Check] = []
    now = pd.Timestamp.now(tz="UTC")
    try:
        bars = broker.retrieve_bars(day_start(now), now)
    except Exception as exc:                       # noqa: BLE001
        return [Check("bar feed", False, f"{type(exc).__name__}: {exc}")]

    if bars.empty:
        return [Check("bar feed", False,
                      f"no bars for {cfg.contract_id} on the "
                      f"{'live' if cfg.live_data else 'sim'} feed. An empty array is "
                      "what a wrong contract id or a wrong BRIDGE_LIVE_DATA looks "
                      "like -- neither returns an error")]

    out.append(Check("bar feed", True,
                     f"{len(bars)} 1-minute bars for {cfg.contract_id}"))

    age = (now - bars["ts"].iloc[-1]).total_seconds()
    if age < 60:
        out.append(Check("bar timestamps", False,
                         f"newest closed bar is {age:.0f}s old. A bar stamped at its "
                         "open cannot be under a minute old, so this feed stamps at "
                         "the close -- every signal would sit one bar out of place"))
    else:
        out.append(Check("bar timestamps", age <= cfg.max_bar_age_s,
                         f"newest closed bar is {age / 60:.1f} min old "
                         f"(limit {cfg.max_bar_age_s / 60:.1f}); stamped at the open, "
                         "as the engine expects"))

    try:
        frame = D.add_time_columns(bars)
        orders = generate_orders(frame, LTFSweepConfig(min_session_bars=0))
        out.append(Check("signals so far today", True,
                         f"{len(orders)} setup(s) confirmed on this session's bars"))
    except Exception as exc:                       # noqa: BLE001
        out.append(Check("signals so far today", False, f"{type(exc).__name__}: {exc}"))
    return out


def report(checks: list[Check]) -> bool:
    """Print the result; return True when everything passed."""
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"  [{'PASS' if c.ok else 'FAIL'}] {c.name:<{width}}  {c.detail}")
    passed = all(c.ok for c in checks)
    print(f"\n{'PREFLIGHT PASSED' if passed else 'PREFLIGHT FAILED'} -- "
          f"{sum(c.ok for c in checks)}/{len(checks)} checks")
    return passed
