"""PO3 / Judas Swing model (Lecture 012), built from the ict concept library.

The full sequence, in the order a trader would actually see it:

1. **Accumulation** — the Asian session (20:00-00:00 ET) builds a range.
2. **Manipulation** — after the New York open, price sweeps one side of that
   range and rejects.  Sweeping the *high* is bearish.
3. **Confirmation** — price displaces back through the range and breaks
   structure on the 5-minute chart in the distribution direction.  Without this
   the model would be a pure fade, and every trending day would be a loss.
4. **Entry** — the displacement leg leaves a fair value gap.  A limit order
   waits there rather than chasing; unfilled orders are recorded, not discarded.
5. **Stop** — beyond the Judas extreme.  If price returns through the sweep
   high, the manipulation reading was wrong.
6. **Target** — the opposite side of the accumulation range (the liquidity the
   distribution leg is reaching for), or a fixed R multiple.
7. **Time exit** — flat by the close.  The dataset was built for a model that
   does not hold overnight, and holding through the maintenance halt would
   invite gap risk this backtest cannot model.

Every level used here comes from an event whose ``confirmed_at`` precedes the
order, so nothing is known before it could have been.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict import po3, structure
from ict.events import BULLISH
from ict.fvg import find_fvgs
from ict.sessions import RTH_CLOSE_MINUTE


@dataclass(frozen=True)
class PO3Config:
    """Strategy parameters. All times are New York minute-of-day."""

    accumulation_session: str = "asian"
    manipulation_start: int = 9 * 60 + 30   # 09:30 ET
    manipulation_end: int = 11 * 60         # 11:00 ET
    confirmation_deadline: int = 12 * 60    # structure must break by 12:00 ET
    entry_deadline: int = 15 * 60           # limit order expires 15:00 ET
    exit_minute: int = RTH_CLOSE_MINUTE     # flat at 16:00 ET

    min_penetration: float = 0.25           # sweep must exceed the level
    min_range: float = 10.0
    max_range: float = 400.0
    require_unswept: bool = True

    min_fvg_size: float = 1.0               # points
    entry_level: str = "midpoint"           # "midpoint" (CE), "far" or "near"
    stop_buffer_points: float = 2.0
    target_mode: str = "opposite_range"     # or "fixed_r"
    target_r: float = 2.0
    swing_n: int = 2


def generate_orders(df5m: pd.DataFrame, config: PO3Config | None = None) -> pd.DataFrame:
    """Turn 5-minute bars into limit orders ready for ``backtest.engine.simulate``.

    Returns one row per setup, with the diagnostic columns (``trading_date``,
    ``sweep_extreme``, ``fvg_top`` ...) retained so any trade can be traced back
    to the bars that produced it.
    """
    config = config or PO3Config()

    judas = po3.find_po3(
        df5m,
        accumulation_session=config.accumulation_session,
        manipulation_start=config.manipulation_start,
        manipulation_end=config.manipulation_end,
        min_penetration=config.min_penetration,
        min_range=config.min_range,
        max_range=config.max_range,
        require_unswept=config.require_unswept,
    )
    if judas.empty:
        return pd.DataFrame(columns=_ORDER_SCHEMA)

    msb = structure.find_msb(df5m, n=config.swing_n)
    fvgs = find_fvgs(df5m, min_size=config.min_fvg_size)
    if msb.empty or fvgs.empty:
        return pd.DataFrame(columns=_ORDER_SCHEMA)

    orders = []
    for _, signal in judas.iterrows():
        order = _build_order(df5m, signal, msb, fvgs, config)
        if order is not None:
            orders.append(order)

    if not orders:
        return pd.DataFrame(columns=_ORDER_SCHEMA)
    return pd.DataFrame(orders)


def _build_order(df5m, signal, msb, fvgs, config) -> dict | None:
    """Find confirmation and entry for one Judas signal, or None if absent."""
    direction = int(signal["direction"])
    day = df5m[df5m["trading_date"] == signal["trading_date"]]
    if day.empty:
        return None

    deadline = day[day["minutes_from_midnight"] <= config.confirmation_deadline]
    if deadline.empty:
        return None
    confirm_cutoff = deadline["ts"].max()

    # Confirmation: a structure break in the distribution direction, strictly
    # after the sweep and before the cutoff.
    breaks = msb[
        (msb["direction"] == direction)
        & (msb["confirmed_at"] > signal["confirmed_at"])
        & (msb["confirmed_at"] <= confirm_cutoff)
    ]
    if breaks.empty:
        return None
    confirmation = breaks.iloc[0]

    # Entry: the first FVG in the distribution direction created by the
    # displacement, i.e. confirmed at or before the structure break.
    gaps = fvgs[
        (fvgs["direction"] == direction)
        & (fvgs["confirmed_at"] > signal["confirmed_at"])
        & (fvgs["confirmed_at"] <= confirmation["confirmed_at"])
    ]
    if gaps.empty:
        return None
    gap = gaps.iloc[-1]

    if config.entry_level == "midpoint":
        entry = float(gap["midpoint"])
    elif config.entry_level == "far":
        entry = float(gap["bottom"] if direction == BULLISH else gap["top"])
    else:
        entry = float(gap["top"] if direction == BULLISH else gap["bottom"])

    # Stop sits beyond the Judas extreme: through it, the read was wrong.
    #
    # The sweep *bar's* own extreme is not sufficient. Price often pushes
    # further against the eventual direction after the initial raid and only
    # then reverses, so the protective level is the furthest point reached
    # between the sweep and the structure break. Using the sweep bar alone
    # produces stops on the wrong side of entry.
    leg = df5m[
        (df5m["ts"] >= signal["ts"]) & (df5m["ts"] <= confirmation["confirmed_at"])
    ]
    if leg.empty:
        return None
    if direction == BULLISH:
        extreme = float(min(signal["sweep_extreme"], leg["low"].min()))
        stop = extreme - config.stop_buffer_points
    else:
        extreme = float(max(signal["sweep_extreme"], leg["high"].max()))
        stop = extreme + config.stop_buffer_points

    # A stop on the wrong side of entry means the FVG sits beyond the
    # protective extreme; there is no coherent trade to place.
    if (direction == BULLISH and stop >= entry) or (direction < 0 and stop <= entry):
        return None

    risk = abs(entry - stop)
    if risk <= 0:
        return None

    if config.target_mode == "opposite_range":
        target = float(signal["range_high"] if direction == BULLISH else signal["range_low"])
        # A target on the wrong side of entry means the sweep already ran past
        # the far side of the range; fall back to a fixed multiple.
        if (direction == BULLISH and target <= entry) or (direction < 0 and target >= entry):
            target = entry + direction * config.target_r * risk
    else:
        target = entry + direction * config.target_r * risk

    entry_expiry = _minute_cutoff(day, config.entry_deadline)
    time_exit = _minute_cutoff(day, config.exit_minute)
    if entry_expiry is None or time_exit is None:
        return None

    return {
        "signal_ts": signal["ts"],
        # The order can only be placed once the structure break has printed.
        "valid_from": confirmation["confirmed_at"],
        "expires_at": entry_expiry,
        "direction": direction,
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
        "time_exit_ts": time_exit,
        "trading_date": signal["trading_date"],
        "range_high": float(signal["range_high"]),
        "range_low": float(signal["range_low"]),
        "sweep_extreme": extreme,
        "confirmation_ts": confirmation["confirmed_at"],
        "confirmation_structure": confirmation["structure"],
        "fvg_top": float(gap["top"]),
        "fvg_bottom": float(gap["bottom"]),
        "risk_points": risk,
    }


def _minute_cutoff(day: pd.DataFrame, minute: int) -> pd.Timestamp | None:
    """Timestamp of the day's last bar at or before the given ET minute."""
    sel = day[day["minutes_from_midnight"] <= minute]
    return None if sel.empty else sel["ts"].max()


_ORDER_SCHEMA = [
    "signal_ts", "valid_from", "expires_at", "direction", "entry_price",
    "stop_price", "target_price", "time_exit_ts", "trading_date",
    "range_high", "range_low", "sweep_extreme", "confirmation_ts",
    "confirmation_structure", "fvg_top", "fvg_bottom", "risk_points",
]
