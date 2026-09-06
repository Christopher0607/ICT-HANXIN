"""Breaker block retest (Lectures 005 / 011).

A breaker is a failed order block.  Price traded through the zone that was
supposed to hold it, so on the next approach the zone is expected to act with
the opposite polarity: a failed bullish block becomes resistance.

The appeal is that a breaker carries information an order block does not — it
has already been tested and failed once, so the traders positioned there are
now offside and their exits fuel the move away.

``find_breakers`` handles the polarity flip, so ``direction`` here is already
the trade direction, not the original block's.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict.events import BULLISH
from ict.orderblocks import find_breakers, find_order_blocks
from ict.sessions import in_window

from .base import (BaseConfig, bar_index_map, build_order, day_groups,
                   finalize, fixed_r_target)


@dataclass(frozen=True)
class BreakerConfig(BaseConfig):
    window_start: int = 7 * 60          # 07:00 ET
    window_end: int = 14 * 60           # 14:00 ET
    min_displacement: float = 15.0
    max_bars_to_break: int = 60         # how long a block may take to fail


def generate_orders(df5m: pd.DataFrame, config: BreakerConfig | None = None) -> pd.DataFrame:
    config = config or BreakerConfig()

    blocks = find_order_blocks(
        df5m, n=config.swing_n, min_displacement=config.min_displacement
    )
    if blocks.empty:
        return finalize([])
    breakers = find_breakers(df5m, order_blocks=blocks, max_bars=config.max_bars_to_break)
    if breakers.empty:
        return finalize([])

    in_kz = set(df5m.index[in_window(df5m, config.window_start, config.window_end)])
    positions = bar_index_map(df5m)
    days = day_groups(df5m)

    rows = []
    for _, br in breakers.iterrows():
        confirm_index = positions.get(br["confirmed_at"])
        if confirm_index is None or int(confirm_index) not in in_kz:
            continue

        direction = int(br["direction"])   # already flipped by find_breakers
        day = days.get(df5m["trading_date"].iloc[int(confirm_index)])
        if day is None or day.empty:
            continue

        top, bottom = float(br["top"]), float(br["bottom"])
        entry = float(br["midpoint"])
        # Stop beyond the zone's far side in the new direction: if price closes
        # back through it, the flip did not hold.
        far_edge = bottom if direction == BULLISH else top
        stop = far_edge - direction * config.stop_buffer_points

        order = build_order(
            strategy="breaker_retest", signal_ts=br["ts"],
            valid_from=br["confirmed_at"], day=day, direction=direction,
            entry=entry, stop=stop,
            target=fixed_r_target(entry, stop, direction, config.target_r),
            config=config, setup_note="failed OB retested as breaker",
            ob_top=top, ob_bottom=bottom,
        )
        if order is not None:
            rows.append(order)

    return finalize(rows)
