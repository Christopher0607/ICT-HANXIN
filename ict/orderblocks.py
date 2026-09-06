"""Order blocks and their derivatives (Lectures 002, 005, 011, 014-016, 028, 030, 033).

An **order block** is the last opposing candle before a displacement move: the
last down-close bar before price expands upward is where the buying that caused
the expansion was absorbed.  Price frequently returns there to fill the rest of
that order flow, which is what makes it tradeable.

The definition only means something when the displacement is real, so this
module requires the move away from the candle to both cover a minimum distance
*and* break structure or leave a fair value gap.  Without that filter "last
down candle" matches almost every bar in the series and the concept degenerates.

Derivatives implemented here:

* **Breaker block** (L005/L011) -- an order block that failed.  Price traded
  through it, so on the retest it flips polarity: a failed bullish OB becomes
  resistance.
* **Mitigation block** (L016) -- the same shape, but formed where price returns
  to mitigate a prior loss-making position rather than after a liquidity grab.
* **Super order block** (L033/L028) -- an OB that carries an FVG within its
  displacement leg, i.e. the strongest tier of the same idea.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import bar_duration
from .events import BEARISH, BULLISH, make_events
from .fvg import find_fvgs
from .structure import find_msb

OB_COLUMNS = ["top", "bottom", "midpoint", "displacement", "has_fvg", "bar_index"]


def find_order_blocks(
    df: pd.DataFrame,
    n: int = 2,
    min_displacement: float = 10.0,
    lookahead: int = 10,
    msb: pd.DataFrame | None = None,
    fvgs: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Detect order blocks anchored to structure-breaking displacement.

    For each market structure break, walks back to the last candle closing
    against the break direction and emits it as the order block.  The zone is
    that candle's full range (high to low), the conservative choice — a
    body-only zone fills more often and flatters the backtest.

    ``confirmed_at`` is the structure break, never the candle itself: the block
    is only identifiable once the displacement has happened.
    """
    if msb is None:
        msb = find_msb(df, n=n)
    if fvgs is None:
        fvgs = find_fvgs(df)
    if msb.empty or df.empty:
        return make_events([], OB_COLUMNS)

    ts = df["ts"].reset_index(drop=True)
    step = bar_duration(df)
    open_ = df["open"].to_numpy()
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()

    fvg_bars = {
        int(d): set(g["bar_index"].astype(int))
        for d, g in fvgs.groupby("direction")
    } if not fvgs.empty else {}

    rows = []
    for _, brk in msb.iterrows():
        break_index = int(brk["bar_index"])
        direction = int(brk["direction"])

        # Walk back to the last candle that closed against the break.
        origin = None
        for j in range(break_index - 1, max(-1, break_index - lookahead - 1), -1):
            opposing = close[j] < open_[j] if direction == BULLISH else close[j] > open_[j]
            if opposing:
                origin = j
                break
        if origin is None:
            continue

        if direction == BULLISH:
            displacement = float(high[break_index] - low[origin])
        else:
            displacement = float(high[origin] - low[break_index])
        if displacement < min_displacement:
            continue

        leg = range(origin, break_index + 1)
        has_fvg = bool(fvg_bars.get(direction, set()) & set(leg))

        rows.append({
            "ts": ts.iloc[origin], "confirmed_at": ts.iloc[break_index] + step,
            "kind": "super_order_block" if has_fvg else "order_block",
            "direction": direction,
            "top": float(high[origin]), "bottom": float(low[origin]),
            "midpoint": float((high[origin] + low[origin]) / 2.0),
            "displacement": displacement, "has_fvg": has_fvg,
            "bar_index": origin,
        })

    return make_events(rows, OB_COLUMNS)


def find_breakers(
    df: pd.DataFrame, order_blocks: pd.DataFrame | None = None, max_bars: int = 100, **kwargs
) -> pd.DataFrame:
    """Order blocks that price traded through — they flip polarity on retest."""
    if order_blocks is None:
        order_blocks = find_order_blocks(df, **kwargs)
    if order_blocks.empty:
        return make_events([], OB_COLUMNS)

    ts = df["ts"].reset_index(drop=True)
    step = bar_duration(df)
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()

    rows = []
    for _, ob in order_blocks.iterrows():
        start = int(ob["bar_index"]) + 1
        stop = min(len(df), start + max_bars)
        direction = int(ob["direction"])
        for j in range(start, stop):
            violated = (
                close[j] < ob["bottom"] if direction == BULLISH else close[j] > ob["top"]
            )
            if violated:
                rows.append({
                    "ts": ob["ts"], "confirmed_at": ts.iloc[j] + step,
                    "kind": "breaker",
                    "direction": -direction,  # polarity flips
                    "top": float(ob["top"]), "bottom": float(ob["bottom"]),
                    "midpoint": float(ob["midpoint"]),
                    "displacement": float(ob["displacement"]),
                    "has_fvg": bool(ob["has_fvg"]), "bar_index": int(ob["bar_index"]),
                })
                break
    return make_events(rows, OB_COLUMNS)


def find_mitigation_blocks(df: pd.DataFrame, **kwargs) -> pd.DataFrame:
    """Order blocks whose displacement did not first sweep liquidity (L016).

    Distinguished from a classic OB by the absence of a preceding raid: price
    turns from the block without having taken out a prior extreme.
    """
    from .liquidity import find_sweeps

    obs = find_order_blocks(df, **kwargs)
    if obs.empty:
        return obs
    sweeps = find_sweeps(df, n=kwargs.get("n", 2))
    if sweeps.empty:
        out = obs.copy()
        out["kind"] = "mitigation_block"
        return out

    swept_bars = sweeps["bar_index"].astype(int).to_numpy()
    keep = []
    for _, ob in obs.iterrows():
        origin = int(ob["bar_index"])
        # A sweep in the 10 bars before the block makes it a classic OB instead.
        if not ((swept_bars >= origin - 10) & (swept_bars <= origin)).any():
            keep.append(ob)
    if not keep:
        return make_events([], OB_COLUMNS)
    out = pd.DataFrame(keep).reset_index(drop=True)
    out["kind"] = "mitigation_block"
    return make_events(out.to_dict("records"), OB_COLUMNS)
