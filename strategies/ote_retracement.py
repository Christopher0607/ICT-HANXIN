"""Optimal trade entry retracement (Lectures 006 / 007).

After structure breaks, ICT does not chase.  It waits for price to retrace into
the 0.62-0.79 band of the expansion leg — deep enough to be in discount for a
long, shallow enough that the trend is presumably intact — and enters at the
0.705 "sweet spot".

The dealing range is built from the swing that was broken to the extreme reached
after the break, using ``find_swings`` and ``find_msb``.  ``DealingRange`` then
supplies the retracement levels, so the fibonacci arithmetic lives in one tested
place rather than being re-derived here.

This is the only strategy in the set whose entry is defined purely by geometry
rather than by a pattern, which makes it a useful contrast: if it performs like
the pattern-based models, the patterns are not adding much.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict.events import BULLISH
from ict.levels import DealingRange
from ict.data import bar_duration
from ict.sessions import in_window
from ict.structure import find_msb

from .base import BaseConfig, build_order, day_groups, finalize, fixed_r_target


@dataclass(frozen=True)
class OTEConfig(BaseConfig):
    window_start: int = 7 * 60        # 07:00 ET
    window_end: int = 14 * 60         # 14:00 ET
    min_leg_points: float = 20.0      # a range smaller than this is noise
    max_leg_points: float = 400.0
    extension_bars: int = 6           # bars after the break used to set the extreme


def generate_orders(df5m: pd.DataFrame, config: OTEConfig | None = None) -> pd.DataFrame:
    config = config or OTEConfig()

    msb = find_msb(df5m, n=config.swing_n)
    if msb.empty:
        return finalize([])

    in_kz = set(df5m.index[in_window(df5m, config.window_start, config.window_end)])
    step = bar_duration(df5m)

    days = day_groups(df5m)

    rows = []
    for _, brk in msb.iterrows():
        bar_index = int(brk["bar_index"])
        if bar_index not in in_kz:
            continue

        direction = int(brk["direction"])
        day = days.get(df5m["trading_date"].iloc[bar_index])
        if day is None or day.empty:
            continue

        # The expansion leg runs from the broken level to the extreme reached
        # in the bars immediately after the break. Those bars have closed by
        # the time the order is placed, so this uses no future information.
        end = min(len(df5m), bar_index + config.extension_bars + 1)
        leg = df5m.iloc[bar_index:end]
        if leg.empty:
            continue
        # The leg's extreme is not known until its final bar has closed, so the
        # order cannot become valid at that bar's open.
        valid_from = leg["ts"].iloc[-1] + step

        level = float(brk["level"])
        if direction == BULLISH:
            rng = DealingRange(low=level, high=float(leg["high"].max()))
        else:
            rng = DealingRange(low=float(leg["low"].min()), high=level)

        if not (config.min_leg_points <= rng.size <= config.max_leg_points):
            continue

        entry = rng.sweet_spot(direction)
        # Stop beyond the range extreme that started the leg: a retracement
        # deeper than 100% means the leg was not an expansion at all.
        anchor = rng.low if direction == BULLISH else rng.high
        stop = anchor - direction * config.stop_buffer_points

        order = build_order(
            strategy="ote_retracement", signal_ts=brk["ts"],
            valid_from=valid_from, day=day, direction=direction,
            entry=entry, stop=stop,
            target=fixed_r_target(entry, stop, direction, config.target_r),
            config=config,
            setup_note=f"OTE 0.705 of {rng.size:.0f}pt leg after {brk['structure']}",
            range_low=rng.low, range_high=rng.high,
        )
        if order is not None:
            rows.append(order)

    return finalize(rows)
