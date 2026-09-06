"""Fair value gaps / imbalances (Lecture 003) and balanced price ranges (L032).

A fair value gap is the most mechanical concept in the entire ICT toolkit,
which is exactly why it makes a good first strategy component: three bars, one
inequality, no judgement.

A **bullish FVG** exists at bar ``i`` when ``low[i] > high[i-2]`` — price moved
up so fast that bars ``i-2`` and ``i`` never traded through the same prices.
The untraded band ``(high[i-2], low[i])`` is the gap.  Bearish is the mirror.

The gap is anchored to bar ``i-1`` (the displacement candle) but is not visible
until bar ``i`` closes, hence ``confirmed_at = ts[i]``.

**Consequent encroachment** is the gap's midpoint — ICT's preferred entry inside
the gap, and a strictly more conservative fill assumption than the far edge.

A **balanced price range** is where a bullish and a bearish FVG overlap: the
inefficiency has been delivered in both directions, and the overlap tends to act
as a stronger reaction zone than either gap alone.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import bar_duration
from .events import BEARISH, BULLISH, make_events

FVG_COLUMNS = ["top", "bottom", "midpoint", "size", "bar_index"]
BPR_COLUMNS = ["top", "bottom", "midpoint", "size"]


def find_fvgs(df: pd.DataFrame, min_size: float = 0.0) -> pd.DataFrame:
    """Detect three-bar fair value gaps.

    ``min_size`` filters out gaps narrower than the given number of points;
    on NQ a sub-tick gap is noise rather than displacement.
    """
    if len(df) < 3:
        return make_events([], FVG_COLUMNS)

    ts = df["ts"].reset_index(drop=True)
    step = bar_duration(df)
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()

    # Index i compares against i-2; align so position k corresponds to bar k+2.
    high_prev2, low_prev2 = high[:-2], low[:-2]
    low_cur, high_cur = low[2:], high[2:]

    bull = low_cur > high_prev2
    bear = high_cur < low_prev2

    frames = []
    for mask, kind, direction, top_arr, bottom_arr in (
        (bull, "fvg", BULLISH, low_cur, high_prev2),
        (bear, "fvg", BEARISH, low_prev2, high_cur),
    ):
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            continue
        bar_i = idx + 2                      # the confirming bar
        top = top_arr[idx].astype("float64")
        bottom = bottom_arr[idx].astype("float64")
        size = top - bottom
        keep = size >= min_size
        if not keep.any():
            continue
        bar_i, top, bottom, size = bar_i[keep], top[keep], bottom[keep], size[keep]
        frames.append(pd.DataFrame({
            "ts": ts.iloc[bar_i - 1].to_numpy(),        # displacement bar
            "confirmed_at": ts.iloc[bar_i].to_numpy() + step,  # visible at its close
            "kind": kind, "direction": direction,
            "top": top, "bottom": bottom,
            "midpoint": (top + bottom) / 2.0,
            "size": size, "bar_index": bar_i.astype("int64"),
        }))

    if not frames:
        return make_events([], FVG_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    return make_events(out.to_dict("records"), FVG_COLUMNS)


def find_bpr(fvgs: pd.DataFrame, max_bars_apart: int = 20) -> pd.DataFrame:
    """Find balanced price ranges: overlapping opposite-direction FVGs."""
    if fvgs.empty:
        return make_events([], BPR_COLUMNS)

    bulls = fvgs[fvgs["direction"] == BULLISH]
    bears = fvgs[fvgs["direction"] == BEARISH]
    rows = []
    for _, b in bulls.iterrows():
        near = bears[(bears["bar_index"] - b["bar_index"]).abs() <= max_bars_apart]
        for _, s in near.iterrows():
            top = min(b["top"], s["top"])
            bottom = max(b["bottom"], s["bottom"])
            if top <= bottom:
                continue
            # Knowable only once both gaps have printed.
            confirmed = max(b["confirmed_at"], s["confirmed_at"])
            rows.append({
                "ts": min(b["ts"], s["ts"]), "confirmed_at": confirmed,
                "kind": "bpr",
                "direction": BULLISH if b["confirmed_at"] > s["confirmed_at"] else BEARISH,
                "top": float(top), "bottom": float(bottom),
                "midpoint": float((top + bottom) / 2.0), "size": float(top - bottom),
            })
    return make_events(rows, BPR_COLUMNS)


def is_filled(gap: pd.Series, df: pd.DataFrame, upto_index: int) -> bool:
    """Whether price has traded back through ``gap`` by bar ``upto_index``."""
    start = int(gap["bar_index"]) + 1
    if start > upto_index:
        return False
    window = df.iloc[start : upto_index + 1]
    if window.empty:
        return False
    if gap["direction"] == BULLISH:
        return bool((window["low"] <= gap["bottom"]).any())
    return bool((window["high"] >= gap["top"]).any())
