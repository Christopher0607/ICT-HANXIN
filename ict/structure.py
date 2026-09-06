"""Market structure: MSB / BOS / CHoCH (Lecture 001).

Mete Kaplan's "market structure break" and ICT's BOS/CHoCH describe the same
event with different emphasis: price trading decisively through a prior swing
point.  The distinction that matters for a strategy is *which* structure broke:

* **BOS** (break of structure) -- the trend's own extreme gives way.  An uptrend
  taking out its last swing high is continuation.
* **CHoCH** (change of character) -- the *opposing* extreme gives way first.  An
  uptrend taking out its last swing low is the earliest structural warning of a
  reversal, and it is the confirmation leg of the PO3 model.

Both are emitted as ``kind="msb"`` events with a ``structure`` column holding
``"bos"`` or ``"choch"``, so callers can filter on either vocabulary.

Two rules keep this honest:

1. A break is only counted against a swing that was **already confirmed** at the
   time of the break — a swing needs ``n`` bars to form, and those bars may be
   the very ones doing the breaking.
2. Breaks require a *close* beyond the level, not a wick.  A wick through a
   swing is a liquidity sweep (see ``ict.liquidity``), which is a different
   event with the opposite implication.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import bar_duration
from .events import BEARISH, BULLISH, make_events
from .swings import find_swings

MSB_COLUMNS = ["level", "structure", "broken_swing_ts", "bar_index"]


def find_msb(df: pd.DataFrame, n: int = 2, swings: pd.DataFrame | None = None) -> pd.DataFrame:
    """Detect market structure breaks.

    Walks bars forward carrying the most recently *confirmed* swing high and
    low plus the prevailing trend, and emits an event when a close breaks one.
    A level is consumed once broken, so a single swing cannot fire repeatedly.
    """
    if swings is None:
        swings = find_swings(df, n=n)
    if swings.empty or df.empty:
        return make_events([], MSB_COLUMNS)

    ts = df["ts"].reset_index(drop=True)
    close = df["close"].to_numpy()
    step = bar_duration(df)

    # Bucket swings by the bar index at which they become visible, so the walk
    # below can never see a swing before it was confirmed.
    by_confirm: dict[int, list[tuple[str, float, pd.Timestamp]]] = {}
    for _, s in swings.iterrows():
        bar = int(s["bar_index"]) + n
        if bar < len(df):
            by_confirm.setdefault(bar, []).append((s["kind"], float(s["price"]), s["ts"]))

    pending_high: tuple[float, pd.Timestamp] | None = None
    pending_low: tuple[float, pd.Timestamp] | None = None
    trend = 0  # +1 up, -1 down, 0 undetermined
    rows = []

    for i in range(len(df)):
        # Break checks run before absorbing this bar's newly confirmed swings:
        # a swing confirmed *by* bar i was not available to trade at bar i.
        if pending_high is not None and close[i] > pending_high[0]:
            structure = "bos" if trend >= 0 else "choch"
            rows.append({
                "ts": ts.iloc[i], "confirmed_at": ts.iloc[i] + step,
                "kind": "msb", "direction": BULLISH,
                "level": pending_high[0], "structure": structure,
                "broken_swing_ts": pending_high[1], "bar_index": i,
            })
            trend = 1
            pending_high = None

        elif pending_low is not None and close[i] < pending_low[0]:
            structure = "bos" if trend <= 0 else "choch"
            rows.append({
                "ts": ts.iloc[i], "confirmed_at": ts.iloc[i] + step,
                "kind": "msb", "direction": BEARISH,
                "level": pending_low[0], "structure": structure,
                "broken_swing_ts": pending_low[1], "bar_index": i,
            })
            trend = -1
            pending_low = None

        for kind, price, swing_ts in by_confirm.get(i, ()):
            if kind == "swing_high":
                pending_high = (price, swing_ts)
            else:
                pending_low = (price, swing_ts)

    return make_events(rows, MSB_COLUMNS)


def structure_state(df: pd.DataFrame, msb: pd.DataFrame) -> pd.Series:
    """Prevailing trend (+1/-1/0) per bar, as known at that bar's close."""
    state = pd.Series(0, index=df.index, dtype="int64")
    if msb.empty:
        return state
    marks = msb.set_index("bar_index")["direction"]
    for bar_index, direction in marks.items():
        state.iloc[int(bar_index):] = int(direction)
    return state
