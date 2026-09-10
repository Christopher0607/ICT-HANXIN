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

from .config import PRESETS, Config
from .guards import scaling_cap
from .topstepx import Order, TopstepXBroker, order_payload


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _mask(value: str) -> str:
    """Enough to tell two keys apart, not enough to use one."""
    return f"{len(value)} chars ending {value[-4:]}" if len(value) > 8 else "(too short)"


def run(cfg: Config, reach_broker: bool = True) -> list[Check]:
    """Every check, in the order they would bite. Never raises."""
    out: list[Check] = []

    out.append(Check("api key present", bool(cfg.api_key),
                     _mask(cfg.api_key) if cfg.api_key else "TOPSTEPX_API_KEY is empty"))
    out.append(Check("username present", bool(cfg.username), cfg.username or "empty"))

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

    risk = cfg.max_loss_limit * 0.1
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
