"""Silver Bullet — the first fair value gap in a fixed one-hour window.

ICT's most mechanical model, and the reason it is worth testing first among the
new set: there is almost nothing to get wrong, so the result is close to a pure
measurement of whether "FVG in a killzone" carries any edge at all.

The rules in full:

1. Wait for the 10:00-11:00 ET window.  No setups exist outside it.
2. Take the first fair value gap that forms inside the window, in the direction
   the 5-minute structure is already pointing.  Structure alignment is what
   keeps this from being a coin flip on every gap.
3. Enter with a limit at the gap's consequent encroachment (its midpoint).
4. Stop beyond the far edge of the gap plus a buffer.
5. Target two times risk.
6. Flat by the close.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict.events import BULLISH
from ict.fvg import find_fvgs
from ict.sessions import KILLZONES, in_window
from ict.structure import find_msb, structure_state

from .base import BaseConfig, build_order, day_groups, finalize, fixed_r_target


@dataclass(frozen=True)
class SilverBulletConfig(BaseConfig):
    window_start: int = KILLZONES["silver_bullet"][0]   # 10:00 ET
    window_end: int = KILLZONES["silver_bullet"][1]     # 11:00 ET
    require_structure_alignment: bool = True


def generate_orders(df5m: pd.DataFrame, config: SilverBulletConfig | None = None) -> pd.DataFrame:
    config = config or SilverBulletConfig()

    gaps = find_fvgs(df5m, min_size=config.min_fvg_size)
    if gaps.empty:
        return finalize([])

    trend = structure_state(df5m, find_msb(df5m, n=config.swing_n))
    in_kz = in_window(df5m, config.window_start, config.window_end)
    # A gap is in the window if its *confirming* bar is — that is when it
    # becomes visible and therefore tradeable.
    kz_bars = set(df5m.index[in_kz])

    days = day_groups(df5m)

    rows = []
    for _, gap in gaps.iterrows():
        bar_index = int(gap["bar_index"])
        if bar_index not in kz_bars:
            continue

        direction = int(gap["direction"])
        if config.require_structure_alignment and trend.iloc[bar_index] != direction:
            continue

        day = days.get(df5m["trading_date"].iloc[bar_index])
        if day is None or day.empty:
            continue

        entry = float(gap["midpoint"])
        far_edge = float(gap["bottom"] if direction == BULLISH else gap["top"])
        stop = far_edge - direction * config.stop_buffer_points
        order = build_order(
            strategy="silver_bullet", signal_ts=gap["ts"],
            valid_from=gap["confirmed_at"], day=day, direction=direction,
            entry=entry, stop=stop,
            target=fixed_r_target(entry, stop, direction, config.target_r),
            config=config, setup_note="first aligned FVG in 10:00-11:00 ET",
            fvg_top=float(gap["top"]), fvg_bottom=float(gap["bottom"]),
        )
        if order is not None:
            rows.append(order)

    return finalize(rows)
