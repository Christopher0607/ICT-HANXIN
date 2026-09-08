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

    preset: str
    account_start: float
    profit_target: float
    max_loss_limit: float
    daily_loss_limit: float
    max_contracts: int
    safety_mult: float

    @classmethod
    def from_env(cls, live: bool = False) -> "Config":
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
            webhook_secret=_env("BRIDGE_WEBHOOK_SECRET"),
            live=live,
            preset=preset,
            account_start=_env("BRIDGE_ACCOUNT_START", 50000.0, float),
            profit_target=target,
            max_loss_limit=mll,
            daily_loss_limit=daily,
            max_contracts=max_ct,
            safety_mult=_env("BRIDGE_SAFETY_MULT", 1.5, float),
        )
