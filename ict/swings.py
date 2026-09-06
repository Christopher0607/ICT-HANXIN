"""Fractal swing highs and lows — the foundation every other concept sits on.

Market structure, liquidity pools, dealing ranges and order blocks are all
defined relative to swing points, so this module is deliberately the simplest
thing that can be correct.

A bar is an ``n``-bar swing high when its high strictly exceeds the highs of
the ``n`` bars either side of it.  The consequence that matters for backtesting:
a swing high at bar ``i`` is not knowable until bar ``i + n`` has closed.  Charts
draw the label at ``i`` and that is where hindsight creeps in — so ``ts`` is the
swing bar itself while ``confirmed_at`` is ``ts[i + n]``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import bar_duration
from .events import BEARISH, BULLISH, make_events, validate_events

SWING_COLUMNS = ["price", "bar_index"]


def find_swings(df: pd.DataFrame, n: int = 2) -> pd.DataFrame:
    """Locate ``n``-bar fractal swing highs and lows.

    Returns an event frame with ``kind`` in ``{"swing_high", "swing_low"}``,
    ``direction`` marking a high as bearish (supply) and a low as bullish
    (demand), and ``price`` holding the extreme.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    if len(df) < 2 * n + 1:
        return make_events([], SWING_COLUMNS)

    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    ts = df["ts"].to_numpy()
    size = len(df)

    is_high = np.ones(size, dtype=bool)
    is_low = np.ones(size, dtype=bool)
    is_high[:n] = is_high[-n:] = False
    is_low[:n] = is_low[-n:] = False

    for offset in range(1, n + 1):
        # Strict on both sides: a plateau of equal highs is not a swing.
        is_high[n:-n] &= high[n:-n] > high[n - offset : size - n - offset]
        is_high[n:-n] &= high[n:-n] > high[n + offset : size - n + offset]
        is_low[n:-n] &= low[n:-n] < low[n - offset : size - n - offset]
        is_low[n:-n] &= low[n:-n] < low[n + offset : size - n + offset]

    ts_series = df["ts"].reset_index(drop=True)
    step = bar_duration(df)
    frames = []
    for mask, kind, direction, prices in (
        (is_high, "swing_high", BEARISH, high),
        (is_low, "swing_low", BULLISH, low),
    ):
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            continue
        frames.append(pd.DataFrame({
            "ts": ts_series.iloc[idx].to_numpy(),
            "confirmed_at": ts_series.iloc[idx + n].to_numpy() + step,
            "kind": kind,
            "direction": direction,
            "price": prices[idx].astype("float64"),
            "bar_index": idx.astype("int64"),
        }))
    if not frames:
        return make_events([], SWING_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values("confirmed_at", kind="mergesort").reset_index(drop=True)
    validate_events(out)
    return out


def last_confirmed(swings: pd.DataFrame, kind: str, now: pd.Timestamp) -> pd.Series | None:
    """Most recent swing of ``kind`` that was confirmed at or before ``now``."""
    if swings.empty:
        return None
    sel = swings[(swings["kind"] == kind) & (swings["confirmed_at"] <= now)]
    if sel.empty:
        return None
    return sel.sort_values("ts").iloc[-1]
