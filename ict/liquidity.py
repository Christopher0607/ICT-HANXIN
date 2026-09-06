"""Liquidity: pools, sweeps, SFP and traps (Lectures 009, 010).

The premise underneath every ICT model is that price seeks liquidity.  Stops
cluster in predictable places — beyond swing highs and lows, above equal highs,
under the previous day's low — and the market reaches for them before making
its real move.  That reach is the *manipulation* leg of PO3.

The critical distinction from ``ict.structure``:

* A **sweep** is a *wick* through a level that closes back inside.  Liquidity
  was taken and rejected.
* A **break** is a *close* beyond the level.  The level genuinely gave way.

Same level, opposite conclusions.  Conflating them is the single most common
way an ICT backtest ends up trading the wrong direction, so the two live in
separate modules and both require the level to have been confirmed beforehand.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import bar_duration
from .events import BEARISH, BULLISH, make_events
from .swings import find_swings

SWEEP_COLUMNS = ["level", "extreme", "penetration", "swept_ts", "bar_index"]
POOL_COLUMNS = ["level", "count", "tolerance", "member_ts"]


def find_sweeps(
    df: pd.DataFrame,
    n: int = 2,
    swings: pd.DataFrame | None = None,
    min_penetration: float = 0.0,
) -> pd.DataFrame:
    """Detect liquidity sweeps of confirmed swing points.

    A bearish sweep (``direction = BEARISH``) takes out a swing *high*: the bar
    trades above it but closes back below.  That is bearish for price even
    though it printed a new high — which is the whole point of the pattern.

    ``min_penetration`` requires the wick to exceed the level by at least this
    many points, filtering out ties that merely touch the level.
    """
    if swings is None:
        swings = find_swings(df, n=n)
    if swings.empty or df.empty:
        return make_events([], SWEEP_COLUMNS)

    ts = df["ts"].reset_index(drop=True)
    step = bar_duration(df)
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()

    by_confirm: dict[int, list[tuple[str, float, pd.Timestamp]]] = {}
    for _, s in swings.iterrows():
        bar = int(s["bar_index"]) + n
        if bar < len(df):
            by_confirm.setdefault(bar, []).append((s["kind"], float(s["price"]), s["ts"]))

    pending_high: tuple[float, pd.Timestamp] | None = None
    pending_low: tuple[float, pd.Timestamp] | None = None
    rows = []

    for i in range(len(df)):
        if pending_high is not None:
            level, swept_ts = pending_high
            if high[i] > level + min_penetration and close[i] < level:
                rows.append({
                    "ts": ts.iloc[i], "confirmed_at": ts.iloc[i] + step,
                    "kind": "sweep", "direction": BEARISH,
                    "level": level, "extreme": float(high[i]),
                    "penetration": float(high[i] - level),
                    "swept_ts": swept_ts, "bar_index": i,
                })
                pending_high = None
            elif close[i] > level:
                pending_high = None  # genuinely broken, not swept

        if pending_low is not None:
            level, swept_ts = pending_low
            if low[i] < level - min_penetration and close[i] > level:
                rows.append({
                    "ts": ts.iloc[i], "confirmed_at": ts.iloc[i] + step,
                    "kind": "sweep", "direction": BULLISH,
                    "level": level, "extreme": float(low[i]),
                    "penetration": float(level - low[i]),
                    "swept_ts": swept_ts, "bar_index": i,
                })
                pending_low = None
            elif close[i] < level:
                pending_low = None

        for kind, price, swing_ts in by_confirm.get(i, ()):
            if kind == "swing_high":
                pending_high = (price, swing_ts)
            else:
                pending_low = (price, swing_ts)

    return make_events(rows, SWEEP_COLUMNS)


def find_level_sweeps(
    df: pd.DataFrame, levels: pd.DataFrame, level_col: str, direction: int
) -> pd.DataFrame:
    """Sweeps of externally supplied levels (prior day high/low, session ranges).

    ``levels`` must carry ``trading_date`` and ``level_col``.  ``direction`` is
    ``BEARISH`` for sweeping a high, ``BULLISH`` for sweeping a low.
    """
    if df.empty or levels.empty:
        return make_events([], SWEEP_COLUMNS)

    merged = df.merge(
        levels[["trading_date", level_col]], on="trading_date", how="left"
    )
    level = merged[level_col].to_numpy(dtype="float64")
    valid = ~np.isnan(level)
    if direction == BEARISH:
        hit = valid & (merged["high"].to_numpy() > level) & (merged["close"].to_numpy() < level)
        extreme = merged["high"].to_numpy()
        penetration = extreme - level
    else:
        hit = valid & (merged["low"].to_numpy() < level) & (merged["close"].to_numpy() > level)
        extreme = merged["low"].to_numpy()
        penetration = level - extreme

    idx = np.flatnonzero(hit)
    if idx.size == 0:
        return make_events([], SWEEP_COLUMNS)
    ts = df["ts"].reset_index(drop=True)
    out = pd.DataFrame({
        "ts": ts.iloc[idx].to_numpy(),
        "confirmed_at": ts.iloc[idx].to_numpy() + bar_duration(df),
        "kind": f"sweep_{level_col}", "direction": direction,
        "level": level[idx], "extreme": extreme[idx],
        "penetration": penetration[idx], "swept_ts": pd.NaT,
        "bar_index": idx.astype("int64"),
    })
    return make_events(out.to_dict("records"), SWEEP_COLUMNS)


def find_equal_levels(
    swings: pd.DataFrame, tolerance: float = 2.0, min_count: int = 2
) -> pd.DataFrame:
    """Cluster swings into equal-high / equal-low liquidity pools.

    Equal highs are a magnet: retail stops sit just above them.  ``tolerance``
    is in points (NQ trades in 0.25 increments, so 2.0 is eight ticks).
    """
    if swings.empty:
        return make_events([], POOL_COLUMNS)

    rows = []
    for kind, direction in (("swing_high", BEARISH), ("swing_low", BULLISH)):
        sel = swings[swings["kind"] == kind].sort_values("ts")
        if sel.empty:
            continue
        prices = sel["price"].to_numpy()
        times = sel["ts"].to_list()
        confirms = sel["confirmed_at"].to_list()

        cluster = [0]
        for j in range(1, len(sel)):
            if abs(prices[j] - prices[cluster[0]]) <= tolerance:
                cluster.append(j)
                continue
            if len(cluster) >= min_count:
                rows.append(_pool_row(cluster, prices, times, confirms, kind, direction, tolerance))
            cluster = [j]
        if len(cluster) >= min_count:
            rows.append(_pool_row(cluster, prices, times, confirms, kind, direction, tolerance))

    return make_events(rows, POOL_COLUMNS)


def _pool_row(cluster, prices, times, confirms, kind, direction, tolerance) -> dict:
    members = [times[j] for j in cluster]
    return {
        "ts": members[0],
        # The pool exists only once its last member is confirmed.
        "confirmed_at": max(confirms[j] for j in cluster),
        "kind": "equal_highs" if kind == "swing_high" else "equal_lows",
        "direction": direction,
        "level": float(np.mean([prices[j] for j in cluster])),
        "count": len(cluster), "tolerance": tolerance, "member_ts": members,
    }


def find_sfp(df: pd.DataFrame, n: int = 2, swings: pd.DataFrame | None = None) -> pd.DataFrame:
    """Swing failure patterns (Lecture 010) — a sweep that closes back inside.

    Structurally identical to a sweep; kept as a named entry point because the
    course treats it as its own setup and because Turtle Soup (Lecture 009)
    is the same pattern applied to the previous day's extremes.
    """
    out = find_sweeps(df, n=n, swings=swings)
    if out.empty:
        return out
    out = out.copy()
    out["kind"] = "sfp"
    return out
