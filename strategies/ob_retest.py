"""Order block retest (Lecture 002).

The most-taught setup in all of Smart Money Concepts: after displacement breaks
structure, price returns to the last opposing candle before that displacement,
and continues from there.

``find_order_blocks`` already does the hard part — it only emits blocks whose
displacement genuinely broke structure and covered a minimum distance, which is
what separates an order block from "any candle".  The strategy adds only the
trade around it.

The stop goes beyond the block rather than beyond a swing: if price closes
through the block, the order flow that created it has been absorbed and the
premise is gone.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict.events import BULLISH
from ict.orderblocks import find_order_blocks
from ict.sessions import in_window

from .base import (BaseConfig, bar_index_map, build_order, day_groups,
                   finalize, fixed_r_target)


@dataclass(frozen=True)
class OBRetestConfig(BaseConfig):
    window_start: int = 7 * 60         # 07:00 ET
    window_end: int = 14 * 60          # 14:00 ET
    min_displacement: float = 15.0     # points; below this it is not displacement
    entry_level: str = "midpoint"      # "midpoint" or "far"
    require_super: bool = False        # only blocks whose leg contains an FVG


def generate_orders(df5m: pd.DataFrame, config: OBRetestConfig | None = None) -> pd.DataFrame:
    config = config or OBRetestConfig()

    blocks = find_order_blocks(
        df5m, n=config.swing_n, min_displacement=config.min_displacement
    )
    if blocks.empty:
        return finalize([])

    in_kz = set(df5m.index[in_window(df5m, config.window_start, config.window_end)])
    positions = bar_index_map(df5m)
    days = day_groups(df5m)

    rows = []
    for _, ob in blocks.iterrows():
        if config.require_super and not bool(ob["has_fvg"]):
            continue
        # Gate on the bar that *confirmed* the block, not the block's own bar:
        # that is when the setup became actionable.
        confirm_index = positions.get(ob["confirmed_at"])
        if confirm_index is None or int(confirm_index) not in in_kz:
            continue

        direction = int(ob["direction"])
        day = days.get(df5m["trading_date"].iloc[int(confirm_index)])
        if day is None or day.empty:
            continue

        top, bottom = float(ob["top"]), float(ob["bottom"])
        if config.entry_level == "midpoint":
            entry = float(ob["midpoint"])
        else:
            entry = top if direction == BULLISH else bottom

        far_edge = bottom if direction == BULLISH else top
        stop = far_edge - direction * config.stop_buffer_points

        order = build_order(
            strategy="ob_retest", signal_ts=ob["ts"],
            valid_from=ob["confirmed_at"], day=day, direction=direction,
            entry=entry, stop=stop,
            target=fixed_r_target(entry, stop, direction, config.target_r),
            config=config, setup_note=f"{ob['kind']} disp {ob['displacement']:.0f}pts",
            ob_top=top, ob_bottom=bottom,
        )
        if order is not None:
            rows.append(order)

    return finalize(rows)
