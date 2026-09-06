"""Swing failure pattern reversal (Lecture 010).

The purest expression of "price seeks liquidity": a swing point is taken out by
a wick and price closes back inside.  The stops beyond that swing have been
filled, and the move that filled them had no follow-through.

Unlike Turtle Soup this fades *internal* swing points rather than the previous
day's extremes, so setups are far more frequent — which makes it a useful test
of whether frequency helps or simply multiplies the cost drag.

Entry waits for a retrace toward the swing level that was swept, rather than
chasing the reversal bar's close.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict.events import BULLISH
from ict.liquidity import find_sfp
from ict.sessions import KILLZONES, in_window

from .base import BaseConfig, build_order, day_groups, finalize, fixed_r_target


@dataclass(frozen=True)
class SFPConfig(BaseConfig):
    window_start: int = KILLZONES["ny_am"][0]   # 07:00 ET
    window_end: int = 12 * 60                   # 12:00 ET
    min_penetration: float = 1.0                # points beyond the swing
    retrace_fraction: float = 0.5               # entry between close and extreme


def generate_orders(df5m: pd.DataFrame, config: SFPConfig | None = None) -> pd.DataFrame:
    config = config or SFPConfig()

    sfps = find_sfp(df5m, n=config.swing_n)
    if sfps.empty:
        return finalize([])

    in_kz = set(df5m.index[in_window(df5m, config.window_start, config.window_end)])

    days = day_groups(df5m)

    rows = []
    for _, sfp in sfps.iterrows():
        bar_index = int(sfp["bar_index"])
        if bar_index not in in_kz:
            continue
        if float(sfp["penetration"]) < config.min_penetration:
            continue

        direction = int(sfp["direction"])
        bar = df5m.iloc[bar_index]
        day = days.get(bar["trading_date"])
        if day is None or day.empty:
            continue

        # Enter partway back toward the swept extreme: the reversal bar closed
        # inside, so a limit between its close and the wick gets a better price
        # without requiring a full retest that often never comes.
        extreme = float(sfp["extreme"])
        close = float(bar["close"])
        entry = close + (extreme - close) * config.retrace_fraction
        stop = extreme - direction * config.stop_buffer_points

        order = build_order(
            strategy="sfp_reversal", signal_ts=sfp["ts"],
            valid_from=sfp["confirmed_at"], day=day, direction=direction,
            entry=entry, stop=stop,
            target=fixed_r_target(entry, stop, direction, config.target_r),
            config=config, setup_note=f"SFP of swing at {sfp['level']:.2f}",
            swept_level=float(sfp["level"]), sweep_extreme=extreme,
        )
        if order is not None:
            rows.append(order)

    return finalize(rows)
