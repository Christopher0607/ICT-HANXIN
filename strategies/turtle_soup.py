"""Turtle Soup — fade the raid on the previous day's high or low (Lectures 009/010).

The previous day's extremes are the most widely watched levels on an intraday
chart, which is exactly why stops pile up beyond them.  Turtle Soup trades the
failure of that breakout: price reaches through the level, fails to hold, and
reverses back into the prior day's range.

This is the one model in the set that trades *against* the immediate move, so
it needs the strictest confirmation.  A raid alone is not enough — structure
must break in the reversal direction before an order is placed, otherwise every
genuine breakout day becomes a loss.

Uses ``prior_day_levels`` and ``find_level_sweeps``, which the concept library
provides and no earlier strategy exercised.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict.events import BEARISH, BULLISH
from ict.fvg import find_fvgs
from ict.liquidity import find_level_sweeps
from ict.sessions import KILLZONES, in_window, prior_day_levels
from ict.structure import find_msb

from .base import (BaseConfig, build_order, day_groups, finalize,
                   first_entry_after)


@dataclass(frozen=True)
class TurtleSoupConfig(BaseConfig):
    window_start: int = KILLZONES["ny_am"][0]     # 07:00 ET
    window_end: int = 12 * 60                     # 12:00 ET
    confirmation_deadline: int = 13 * 60          # structure must break by 13:00 ET
    target_mode: str = "opposite_level"           # or "fixed_r"


def generate_orders(df5m: pd.DataFrame, config: TurtleSoupConfig | None = None) -> pd.DataFrame:
    config = config or TurtleSoupConfig()

    levels = prior_day_levels(df5m)
    if levels.empty:
        return finalize([])

    window = df5m[in_window(df5m, config.window_start, config.window_end)]
    if window.empty:
        return finalize([])

    # Raiding the previous day's HIGH is bearish; raiding the LOW is bullish.
    sweeps = pd.concat([
        find_level_sweeps(window, levels, "pdh", BEARISH),
        find_level_sweeps(window, levels, "pdl", BULLISH),
    ], ignore_index=True)
    if sweeps.empty:
        return finalize([])
    sweeps = sweeps.sort_values("confirmed_at").reset_index(drop=True)

    msb = find_msb(df5m, n=config.swing_n)
    gaps = find_fvgs(df5m, min_size=config.min_fvg_size)
    if msb.empty or gaps.empty:
        return finalize([])

    level_by_date = levels.set_index("trading_date")

    days = day_groups(df5m)
    date_by_ts = pd.Series(df5m["trading_date"].to_numpy(),
                           index=pd.DatetimeIndex(df5m["ts"]))

    rows = []
    for _, sweep in sweeps.iterrows():
        # The sweep row carries the window's positional index, so recover the
        # trading day from the timestamp instead.
        trading_date = date_by_ts.get(sweep["ts"])
        if trading_date is None:
            continue
        day = days.get(trading_date)
        if day is None or day.empty:
            continue

        cutoff_bars = day[day["minutes_from_midnight"] <= config.confirmation_deadline]
        if cutoff_bars.empty:
            continue
        cutoff = cutoff_bars["ts"].max()

        direction = int(sweep["direction"])
        confirmation = first_entry_after(msb, direction, sweep["confirmed_at"], cutoff)
        if confirmation is None:
            continue

        gap = first_entry_after(gaps, direction, sweep["confirmed_at"],
                                confirmation["confirmed_at"])
        if gap is None:
            continue

        entry = float(gap["midpoint"])
        # Protective extreme: the furthest point of the raid, not just the
        # sweeping bar, since price often extends before turning.
        leg = day[(day["ts"] >= sweep["ts"]) & (day["ts"] <= confirmation["confirmed_at"])]
        if leg.empty:
            continue
        if direction == BULLISH:
            extreme = min(float(sweep["extreme"]), float(leg["low"].min()))
            stop = extreme - config.stop_buffer_points
        else:
            extreme = max(float(sweep["extreme"]), float(leg["high"].max()))
            stop = extreme + config.stop_buffer_points

        risk = abs(entry - stop)
        if config.target_mode == "opposite_level" and trading_date in level_by_date.index:
            row = level_by_date.loc[trading_date]
            target = float(row["pdl"] if direction == BEARISH else row["pdh"])
            reaching_wrong_way = (
                target <= entry if direction == BULLISH else target >= entry
            )
            if reaching_wrong_way:
                target = entry + direction * config.target_r * risk
        else:
            target = entry + direction * config.target_r * risk

        order = build_order(
            strategy="turtle_soup", signal_ts=sweep["ts"],
            valid_from=confirmation["confirmed_at"], day=day, direction=direction,
            entry=entry, stop=stop, target=target, config=config,
            setup_note=f"raid on {sweep['kind']} then {confirmation['structure']}",
            swept_level=float(sweep["level"]), sweep_extreme=extreme,
        )
        if order is not None:
            rows.append(order)

    return finalize(rows)
