"""Bridge configuration. Secrets come from the environment, never from a file.

An API key in a config file ends up in git, in a backup, or in a screenshot.
The one in this repo's history already had to be rotated twice. Everything
secret is read from the environment and nothing here ever writes one back out.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Account rules, mirroring the preset table in the Pine scripts.
#: (profit target, maximum loss limit, daily loss limit, max micro contracts)
#: tests/test_bridge.py asserts these stay identical to the Pine arrays: two
#: implementations of one rule are only safe while they agree.
PRESETS: dict[str, tuple[float, float, float, int]] = {
    "Topstep 50K":   (3000.0, 2000.0, 1000.0, 50),
    "Topstep 100K":  (6000.0, 3000.0, 2000.0, 100),
    "Topstep 150K":  (9000.0, 4500.0, 3000.0, 150),
    # Apex has no daily loss limit; the cell is a number too large to trigger
    # rather than zero, which would block every trade.
    "Apex 50K EOD":  (3000.0, 2500.0, 1e9, 20),
    "Apex 100K EOD": (6000.0, 3000.0, 1e9, 40),
}

MISSING = object()


def _env(name: str, default=MISSING, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        if default is MISSING:
            raise RuntimeError(
                f"{name} is not set. The bridge reads all configuration from the "
                "environment; see bridge/README.md."
            )
        return default
    return cast(raw)


@dataclass(frozen=True)
class Config:
    """Everything the bridge needs. Built once, at startup, from the environment."""

    username: str
    api_key: str
    base_url: str
    account_id: int | None
    contract_id: str
    tick_size: float
    point_value: float
    webhook_secret: str
    live: bool

    live_data: bool
    cutoff_minute: int
    max_bar_age_s: float

    preset: str
    account_start: float
    profit_target: float
    max_loss_limit: float
    daily_loss_limit: float
    max_contracts: int
    safety_mult: float
    use_guard: bool
    scaling_plan: bool

    @classmethod
    def from_env(cls, live: bool = False, need_webhook: bool = False) -> "Config":
        preset = _env("BRIDGE_PRESET", "Topstep 50K")
        if preset not in PRESETS:
            raise RuntimeError(f"BRIDGE_PRESET={preset!r} is not one of {sorted(PRESETS)}")
        target, mll, daily, max_ct = PRESETS[preset]
        return cls(
            username=_env("TOPSTEPX_USERNAME"),
            api_key=_env("TOPSTEPX_API_KEY"),
            base_url=_env("TOPSTEPX_BASE_URL", "https://api.topstepx.com"),
            account_id=_env("TOPSTEPX_ACCOUNT_ID", None, int),
            contract_id=_env("BRIDGE_CONTRACT_ID", "CON.F.US.MNQ.Z26"),
            tick_size=_env("BRIDGE_TICK_SIZE", 0.25, float),
            point_value=_env("BRIDGE_POINT_VALUE", 2.0, float),
            # The local signal engine has no inbound surface to protect, so the
            # secret is only required by the webhook server. Demanding one for
            # bridge.live would teach people to set a dummy, which is how a real
            # secret ends up being a dummy on the day it matters.
            webhook_secret=_env("BRIDGE_WEBHOOK_SECRET", MISSING if need_webhook else ""),
            live=live,
            # Which market-data subscription retrieveBars reads. A practice
            # account is on the sim feed; asking it for live data returns an
            # empty bar array rather than an error.
            live_data=_env("BRIDGE_LIVE_DATA", "0") not in ("0", "false", "False"),
            # Minutes from ET midnight after which no new order is placed and
            # any unfilled entry is cancelled. 11:00 ET by default: measured on
            # 2024-2026, stopping there keeps 74.7% of the P&L for 63.6% of the
            # trades, and running on to 11:30 is worse, not better.
            cutoff_minute=_env("BRIDGE_CUTOFF_MINUTE", 11 * 60, int),
            max_bar_age_s=_env("BRIDGE_MAX_BAR_AGE_S", 150.0, float),
            preset=preset,
            account_start=_env("BRIDGE_ACCOUNT_START", 50000.0, float),
            profit_target=target,
            max_loss_limit=mll,
            daily_loss_limit=daily,
            max_contracts=max_ct,
            safety_mult=_env("BRIDGE_SAFETY_MULT", 1.5, float),
            # Both default to the funded-account posture, which is the one
            # where being wrong is expensive. An evaluation you would re-buy
            # is the deliberate exception, not the default.
            use_guard=_env("BRIDGE_USE_GUARD", "1") not in ("0", "false", "False"),
            scaling_plan=_env("BRIDGE_SCALING_PLAN", "0") not in ("0", "false", "False"),
        )
