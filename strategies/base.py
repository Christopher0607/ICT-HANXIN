"""Shared scaffolding for every strategy in this package.

All seven models differ only in *what* they consider a setup.  Once a setup is
found, the mechanics are identical: place a limit order, put the stop beyond
the level that would invalidate the idea, target either a liquidity pool or a
fixed multiple of risk, expire the order if it never fills, and be flat by the
close.

Keeping those mechanics here means a bug in them is fixed once, and — more
importantly — that differences between the strategies' results come from their
ideas rather than from accidental variation in their plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict.events import BULLISH
from ict.sessions import RTH_CLOSE_MINUTE

#: Columns every strategy must produce. The first eight are what the engine
#: consumes; the rest are diagnostics that let any trade be traced back to the
#: bars that produced it.
ORDER_SCHEMA = [
    "signal_ts", "valid_from", "expires_at", "direction",
    "entry_price", "stop_price", "target_price", "time_exit_ts",
    "trading_date", "strategy", "risk_points", "setup_note",
]


@dataclass(frozen=True)
class BaseConfig:
    """Parameters shared by every strategy.

    These values are ICT's own, not fitted: a 2R target, a two-point stop
    buffer, two-bar swings. They are fixed before any backtest runs and are not
    searched over — see the anti-overfitting section of the project README.
    """

    entry_deadline: int = 15 * 60           # limit orders expire 15:00 ET
    exit_minute: int = RTH_CLOSE_MINUTE     # flat at 16:00 ET
    stop_buffer_points: float = 2.0
    target_r: float = 2.0
    swing_n: int = 2
    min_fvg_size: float = 1.0
    min_risk_points: float = 2.0            # below this, costs dominate
    max_risk_points: float = 250.0          # beyond this, size rounds to zero


def minute_cutoff(day: pd.DataFrame, minute: int) -> pd.Timestamp | None:
    """Timestamp of the day's last bar at or before the given ET minute."""
    sel = day[day["minutes_from_midnight"] <= minute]
    return None if sel.empty else sel["ts"].max()


def build_order(
    *, strategy: str, signal_ts, valid_from, day: pd.DataFrame, direction: int,
    entry: float, stop: float, target: float, config: BaseConfig,
    setup_note: str = "", **extra,
) -> dict | None:
    """Assemble one order, or return None if the geometry is incoherent.

    Rejects rather than repairs: a stop on the wrong side of entry means the
    setup logic produced something that is not a trade, and silently flipping
    it would hide the bug. Returning None keeps the setup out of the results
    and out of the statistics.
    """
    if direction not in (1, -1):
        raise ValueError(f"direction must be +/-1, got {direction!r}")

    wrong_stop = stop >= entry if direction == BULLISH else stop <= entry
    wrong_target = target <= entry if direction == BULLISH else target >= entry
    if wrong_stop or wrong_target:
        return None

    risk = abs(entry - stop)
    if not (config.min_risk_points <= risk <= config.max_risk_points):
        return None

    expires_at = minute_cutoff(day, config.entry_deadline)
    time_exit = minute_cutoff(day, config.exit_minute)
    if expires_at is None or time_exit is None or valid_from > expires_at:
        return None

    order = {
        "signal_ts": signal_ts, "valid_from": valid_from, "expires_at": expires_at,
        "direction": int(direction), "entry_price": float(entry),
        "stop_price": float(stop), "target_price": float(target),
        "time_exit_ts": time_exit,
        "trading_date": day["trading_date"].iloc[0],
        "strategy": strategy, "risk_points": float(risk), "setup_note": setup_note,
    }
    order.update(extra)
    return order


def fixed_r_target(entry: float, stop: float, direction: int, r: float) -> float:
    """Target at ``r`` multiples of the entry-to-stop distance."""
    return entry + direction * r * abs(entry - stop)


def one_trade_per_day(orders: pd.DataFrame) -> pd.DataFrame:
    """Keep only the earliest-triggering order on each trading day.

    Selection is by ``valid_from`` — the moment the setup became actionable —
    so the choice uses only information available at the time. Ranking by
    outcome, or by anything confirmed later in the day, would be hindsight.
    """
    if orders.empty:
        return orders
    return (
        orders.sort_values(["trading_date", "valid_from"], kind="mergesort")
        .groupby("trading_date", as_index=False, sort=True)
        .first()
    )


def finalize(rows: list[dict]) -> pd.DataFrame:
    """Turn accumulated order dicts into the one-per-day order frame."""
    if not rows:
        return pd.DataFrame(columns=ORDER_SCHEMA)
    return one_trade_per_day(pd.DataFrame(rows)).reset_index(drop=True)


def bar_index_map(df: pd.DataFrame) -> pd.Series:
    """Map each bar timestamp to its positional index.

    Built once per run. Scanning ``df[df.ts == t]`` inside a per-setup loop is
    O(bars) per lookup and turns a few hundred setups into tens of millions of
    comparisons.
    """
    return pd.Series(range(len(df)), index=pd.DatetimeIndex(df["ts"]))


def day_groups(df: pd.DataFrame) -> dict:
    """Trading date -> that day's bars, built once instead of per setup."""
    return {date: group for date, group in df.groupby("trading_date", sort=False)}


def day_frame(df: pd.DataFrame, trading_date) -> pd.DataFrame:
    """All bars belonging to one CME trading day."""
    return df[df["trading_date"] == trading_date]


def first_entry_after(
    events: pd.DataFrame, direction: int, after, until,
) -> pd.Series | None:
    """First event of the given direction confirmed in ``(after, until]``."""
    if events.empty:
        return None
    sel = events[
        (events["direction"] == direction)
        & (events["confirmed_at"] > after)
        & (events["confirmed_at"] <= until)
    ]
    return None if sel.empty else sel.iloc[0]


def retarget(orders: pd.DataFrame, r: float) -> pd.DataFrame:
    """Re-price every order's target to ``r`` multiples of its own risk.

    ``target_r`` feeds nothing but the target price: the setup search, the
    entry, the stop and the order's timing are all decided before the target is
    computed, and none of ``build_order``'s rejection tests look at it. So
    re-targeting an existing order set is exactly equivalent to regenerating it
    with a different ``target_r`` — and it makes a reward-to-risk sweep a
    controlled experiment rather than a comparison of different trade sets.

    The same trades, the same fills, the same stops; only where the profit is
    taken changes. Any difference in the results is therefore attributable to
    the target and nothing else.
    """
    if r <= 0:
        raise ValueError(f"r must be positive, got {r}")
    if orders.empty:
        return orders.copy()

    out = orders.copy()
    risk = (out["entry_price"] - out["stop_price"]).abs()
    out["target_price"] = out["entry_price"] + out["direction"] * r * risk
    return out
