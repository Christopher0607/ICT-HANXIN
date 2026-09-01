"""Power of Three and the Judas Swing (Lecture 012).

PO3 splits a trading day into three phases:

1. **Accumulation** — a quiet range where positions are built.  In the classic
   index-futures version this is the Asian session.
2. **Manipulation** — the *Judas swing*.  Shortly after the session opens price
   pushes through one side of the accumulation range, triggering the stops
   resting there, then rejects.  The direction of this move is deliberately
   the wrong one.
3. **Distribution** — the real move, in the opposite direction, expanding away
   from the range toward the liquidity on the far side.

The tradeable inference is the inversion: **sweeping the range high is bearish**
and sweeping the low is bullish.  A model that reads a new high as strength has
the sign backwards and will lose on precisely the days the pattern works.

This module detects phases 1 and 2 and states the expected distribution
direction.  It deliberately stops there: confirmation (a structure break) and
entry (a retrace into the displacement's fair value gap) belong to the strategy
in ``strategies/po3_judas.py``, so the same detection can feed different entry
models.
"""

from __future__ import annotations

import pandas as pd

from .events import BEARISH, BULLISH, make_events
from .sessions import in_window, session_ranges

PO3_COLUMNS = [
    "trading_date", "range_high", "range_low", "range_size",
    "swept_level", "sweep_extreme", "penetration", "bar_index",
]


def find_po3(
    df: pd.DataFrame,
    accumulation_session: str = "asian",
    manipulation_start: int = 9 * 60 + 30,
    manipulation_end: int = 11 * 60,
    min_penetration: float = 0.25,
    min_range: float = 10.0,
    max_range: float = 400.0,
    require_unswept: bool = True,
) -> pd.DataFrame:
    """Detect the accumulation range and its Judas sweep, one event per day.

    Args:
        df: 5-minute bars carrying the columns added by ``data.add_time_columns``.
        accumulation_session: killzone forming the accumulation range.
        manipulation_start / manipulation_end: ET minute-of-day bounds of the
            window in which a Judas swing is accepted.  Defaults span the New
            York open through 11:00, where the classic index Judas occurs.
        min_penetration: how far the wick must exceed the range, in points.
        min_range / max_range: sanity bounds on accumulation size.  A range of
            two points is noise; a 500-point "range" means the session trended
            and there was no accumulation to speak of.
        require_unswept: when True, discard days where price had already taken
            the level out between the end of accumulation and the start of the
            manipulation window.  Liquidity taken during London is gone by the
            time New York opens, so a later poke through the same level is not
            a fresh raid and the PO3 inference does not apply.

    Returns:
        Event frame with ``direction`` set to the **expected distribution
        direction** (the opposite of the sweep), not the direction of the sweep.
    """
    if df.empty:
        return make_events([], PO3_COLUMNS)

    ranges = session_ranges(df, accumulation_session)
    if ranges.empty:
        return make_events([], PO3_COLUMNS)
    ranges = ranges.set_index("trading_date")

    window = df[in_window(df, manipulation_start, manipulation_end)]
    if window.empty:
        return make_events([], PO3_COLUMNS)

    rows = []
    for trading_date, day in window.groupby("trading_date", sort=True):
        if trading_date not in ranges.index:
            continue
        acc = ranges.loc[trading_date]
        range_high, range_low = float(acc["high"]), float(acc["low"])
        size = range_high - range_low
        if not (min_range <= size <= max_range):
            continue

        # The accumulation range is only usable once its final bar has closed.
        day = day[day["ts"] >= acc["available_at"]]
        if day.empty:
            continue

        high_intact = low_intact = True
        if require_unswept:
            gap = df[(df["trading_date"] == trading_date)
                     & (df["ts"] >= acc["available_at"])
                     & (df["ts"] < day["ts"].iloc[0])]
            if not gap.empty:
                high_intact = bool(gap["high"].max() <= range_high)
                low_intact = bool(gap["low"].min() >= range_low)
        if not (high_intact or low_intact):
            continue

        for idx, bar in day.iterrows():
            high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])

            # Sweep of the range high -> Judas up -> expect distribution DOWN.
            if high_intact and high > range_high + min_penetration and close < range_high:
                rows.append(_po3_row(bar, idx, trading_date, range_high, range_low,
                                     BEARISH, range_high, high, high - range_high))
                break
            # Sweep of the range low -> Judas down -> expect distribution UP.
            if low_intact and low < range_low - min_penetration and close > range_low:
                rows.append(_po3_row(bar, idx, trading_date, range_high, range_low,
                                     BULLISH, range_low, low, range_low - low))
                break

    return make_events(rows, PO3_COLUMNS)


def _po3_row(bar, idx, trading_date, range_high, range_low, direction,
             swept_level, extreme, penetration) -> dict:
    return {
        "ts": bar["ts"], "confirmed_at": bar["ts"],
        "kind": "po3_judas", "direction": direction,
        "trading_date": trading_date,
        "range_high": range_high, "range_low": range_low,
        "range_size": range_high - range_low,
        "swept_level": float(swept_level), "sweep_extreme": float(extreme),
        "penetration": float(penetration), "bar_index": int(idx),
    }
