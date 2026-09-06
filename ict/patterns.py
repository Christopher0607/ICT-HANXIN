"""Assorted structural patterns (Lectures 004, 013, 018, 019, 020).

These are the course's named chart formations.  Each reduces to a specific
arrangement of swing points, so they are all built on ``ict.swings`` and all
inherit its confirmation delay.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import bar_duration
from .events import BEARISH, BULLISH, make_events
from .swings import find_swings

QML_COLUMNS = ["level", "head", "shoulder", "bar_index"]
FLIP_COLUMNS = ["level", "broken_at", "bar_index"]
TAP_COLUMNS = ["level", "taps", "tolerance", "bar_index"]
DEVIATION_COLUMNS = ["range_high", "range_low", "level", "extreme", "bar_index"]


def find_qml(df: pd.DataFrame, n: int = 2, swings: pd.DataFrame | None = None) -> pd.DataFrame:
    """Quasimodo / over-and-under (Lecture 004).

    A bearish QML is a head-and-shoulders in swing terms: a higher high (the
    head) followed by a break below the low that preceded it.  The entry level
    is the *left shoulder* high — the swing high before the head — because that
    is the origin of the move that failed.
    """
    if swings is None:
        swings = find_swings(df, n=n)
    if swings.empty:
        return make_events([], QML_COLUMNS)

    ordered = swings.sort_values("ts").reset_index(drop=True)
    rows = []
    for i in range(3, len(ordered)):
        a, b, c, d = (ordered.iloc[i - 3], ordered.iloc[i - 2],
                      ordered.iloc[i - 1], ordered.iloc[i])
        kinds = (a["kind"], b["kind"], c["kind"], d["kind"])

        # Bearish: high, low, higher high (head), lower low breaking the first low.
        if kinds == ("swing_high", "swing_low", "swing_high", "swing_low") \
                and c["price"] > a["price"] and d["price"] < b["price"]:
            rows.append({
                "ts": c["ts"], "confirmed_at": d["confirmed_at"],
                "kind": "qml", "direction": BEARISH,
                "level": float(a["price"]), "head": float(c["price"]),
                "shoulder": float(a["price"]), "bar_index": int(d["bar_index"]),
            })

        # Bullish mirror: low, high, lower low (head), higher high breaking the first high.
        if kinds == ("swing_low", "swing_high", "swing_low", "swing_high") \
                and c["price"] < a["price"] and d["price"] > b["price"]:
            rows.append({
                "ts": c["ts"], "confirmed_at": d["confirmed_at"],
                "kind": "qml", "direction": BULLISH,
                "level": float(a["price"]), "head": float(c["price"]),
                "shoulder": float(a["price"]), "bar_index": int(d["bar_index"]),
            })

    return make_events(rows, QML_COLUMNS)


def find_sr_flips(
    df: pd.DataFrame, n: int = 2, swings: pd.DataFrame | None = None,
    tolerance: float = 2.0, max_bars: int = 60,
) -> pd.DataFrame:
    """Support/resistance flips (Lecture 013): a broken level retested from the far side."""
    if swings is None:
        swings = find_swings(df, n=n)
    if swings.empty:
        return make_events([], FLIP_COLUMNS)

    ts = df["ts"].reset_index(drop=True)
    step = bar_duration(df)
    high, low, close = (df[c].to_numpy() for c in ("high", "low", "close"))
    rows = []

    for _, s in swings.iterrows():
        level = float(s["price"])
        start = int(s["bar_index"]) + n
        broke_at = None
        for j in range(start, min(len(df), start + max_bars)):
            if broke_at is None:
                broken = (close[j] > level if s["kind"] == "swing_high"
                          else close[j] < level)
                if broken:
                    broke_at = j
                continue
            # Retest: price returns to the level from the other side.
            retested = (low[j] <= level + tolerance if s["kind"] == "swing_high"
                        else high[j] >= level - tolerance)
            if retested:
                rows.append({
                    "ts": s["ts"], "confirmed_at": ts.iloc[j] + step,
                    "kind": "sr_flip",
                    "direction": BULLISH if s["kind"] == "swing_high" else BEARISH,
                    "level": level, "broken_at": ts.iloc[broke_at], "bar_index": j,
                })
                break
    return make_events(rows, FLIP_COLUMNS)


def find_three_taps(
    df: pd.DataFrame, n: int = 2, swings: pd.DataFrame | None = None,
    tolerance: float = 5.0,
) -> pd.DataFrame:
    """Three-tap concept (Lecture 019): three consecutive swings into one level."""
    if swings is None:
        swings = find_swings(df, n=n)
    if swings.empty:
        return make_events([], TAP_COLUMNS)

    rows = []
    for kind, direction in (("swing_high", BEARISH), ("swing_low", BULLISH)):
        sel = swings[swings["kind"] == kind].sort_values("ts").reset_index(drop=True)
        for i in range(2, len(sel)):
            window = sel.iloc[i - 2 : i + 1]
            prices = window["price"].to_numpy()
            if prices.max() - prices.min() <= tolerance:
                rows.append({
                    "ts": window.iloc[0]["ts"],
                    "confirmed_at": window.iloc[-1]["confirmed_at"],
                    "kind": "three_tap", "direction": direction,
                    "level": float(prices.mean()), "taps": 3, "tolerance": tolerance,
                    "bar_index": int(window.iloc[-1]["bar_index"]),
                })
    return make_events(rows, TAP_COLUMNS)


def find_range_deviations(
    df: pd.DataFrame, window: int = 24, max_range: float = 40.0,
    min_deviation: float = 2.0,
) -> pd.DataFrame:
    """Range + deviation (Lecture 018): a false break out of a tight consolidation.

    Finds ``window``-bar stretches whose full range stays under ``max_range``,
    then flags the first bar that pokes outside and closes back within.
    """
    if len(df) < window + 2:
        return make_events([], DEVIATION_COLUMNS)

    ts = df["ts"].reset_index(drop=True)
    step = bar_duration(df)
    high, low, close = (df[c].to_numpy() for c in ("high", "low", "close"))
    roll_high = pd.Series(high).rolling(window).max().to_numpy()
    roll_low = pd.Series(low).rolling(window).min().to_numpy()

    rows = []
    for i in range(window, len(df)):
        rh, rl = roll_high[i - 1], roll_low[i - 1]
        if np.isnan(rh) or (rh - rl) > max_range:
            continue
        if high[i] > rh + min_deviation and close[i] < rh:
            rows.append({
                "ts": ts.iloc[i], "confirmed_at": ts.iloc[i] + step,
                "kind": "range_deviation", "direction": BEARISH,
                "range_high": float(rh), "range_low": float(rl),
                "level": float(rh), "extreme": float(high[i]), "bar_index": i,
            })
        elif low[i] < rl - min_deviation and close[i] > rl:
            rows.append({
                "ts": ts.iloc[i], "confirmed_at": ts.iloc[i] + step,
                "kind": "range_deviation", "direction": BULLISH,
                "range_high": float(rh), "range_low": float(rl),
                "level": float(rl), "extreme": float(low[i]), "bar_index": i,
            })
    return make_events(rows, DEVIATION_COLUMNS)


def find_ftr(
    df: pd.DataFrame, n: int = 2, msb: pd.DataFrame | None = None, max_bars: int = 40,
) -> pd.DataFrame:
    """Failed to return / failed to break (Lecture 020).

    A structure break whose origin zone price never came back to within
    ``max_bars``.  The absence of a retest signals genuine intent, so the
    unfilled zone becomes the reference for the next pullback.
    """
    from .structure import find_msb

    if msb is None:
        msb = find_msb(df, n=n)
    if msb.empty:
        return make_events([], FLIP_COLUMNS)

    ts = df["ts"].reset_index(drop=True)
    step = bar_duration(df)
    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    rows = []
    for _, brk in msb.iterrows():
        i = int(brk["bar_index"])
        level = float(brk["level"])
        stop = min(len(df), i + 1 + max_bars)
        if stop <= i + 1:
            continue
        window = slice(i + 1, stop)
        returned = ((low[window] <= level).any() if brk["direction"] == BULLISH
                    else (high[window] >= level).any())
        if not returned:
            rows.append({
                "ts": brk["ts"], "confirmed_at": ts.iloc[stop - 1] + step,
                "kind": "ftr", "direction": int(brk["direction"]),
                "level": level, "broken_at": brk["ts"], "bar_index": i,
            })
    return make_events(rows, FLIP_COLUMNS)
