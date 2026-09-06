"""Low-timeframe liquidity sweep: 15-minute pools, 1-minute CHoCH, 1-minute FVG.

A different shape of ICT model from the seven in ``registry.py``. Those hang off
a session range built overnight; this one hangs off liquidity that formed
minutes ago:

1. **Liquidity pool** — the highest high and lowest low of the previous
   ``lookback_candles`` *completed* 15-minute candles. Stops rest beyond a
   recent extreme just as they rest beyond a session extreme, only closer.
2. **Sweep** — a 1-minute bar wicks through that extreme and closes back
   inside. Fresh liquidity taken and rejected.
3. **Confirmation** — a change of character on the 1-minute chart in the
   opposite direction, within ``confirm_within_bars``.
4. **Entry** — a limit at the midpoint of the fair value gap left by the
   displacement that broke structure.
5. **Target** — a fixed 1:1 multiple of risk, so the reward distance is set by
   the stop rather than by wherever the next pool happens to sit.

The pool is the *extreme* of the lookback window, not every candle's high and
low: a level that price already traded through when a later candle formed is
not liquidity waiting to be taken.

**A resolution caveat this model cannot avoid.** The seven session strategies
sign on 5-minute bars and fill on 1-minute bars, so it is nearly always
knowable whether the stop or the target came first. Here signals and fills are
both 1-minute, and there is no finer data, so a 1-minute bar spanning both
levels is genuinely unresolvable. A 1:1 target sits close to the stop, which
makes such bars more likely, not less. ``ambiguous_pct`` in the results counts
them, and the runner reports the pessimistic and optimistic readings as a band.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ict import data as D
from ict.events import BEARISH, BULLISH
from ict.fvg import find_fvgs
from ict.structure import find_msb

from .base import BaseConfig, build_order, finalize, fixed_r_target, one_trade_per_day


@dataclass(frozen=True)
class LTFSweepConfig(BaseConfig):
    """Parameters. Declared before the first run; none are searched over."""

    lookback_candles: int = 4        # prior 15m candles forming the pool
    session_start: int = 9 * 60 + 30  # sweeps accepted from 09:30 ET
    session_end: int = 15 * 60        # ... until 15:00 ET
    entry_deadline: int = 15 * 60 + 30
    exit_minute: int = 16 * 60        # flat at the close
    min_penetration: float = 0.25     # one tick beyond the pool
    min_fvg_size: float = 0.5         # 1-minute gaps are small
    confirm_within_bars: int = 30     # CHoCH must follow the sweep this soon
    require_choch: bool = True        # a BOS is continuation, not a reversal
    stop_buffer_points: float = 1.0   # tighter than the session models
    target_r: float = 1.0             # the 1:1 the model is specified with
    swing_n: int = 2
    min_risk_points: float = 1.0
    max_risk_points: float = 120.0


def liquidity_pools(df15m: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Rolling extreme of the previous ``lookback`` completed 15m candles.

    Shifted by one so a candle never contributes to the pool it is being tested
    against, and stamped with the time the final contributing candle closed —
    the moment the level is actually knowable.
    """
    out = pd.DataFrame({"ts": df15m["ts"]})
    out["pool_high"] = df15m["high"].rolling(lookback).max().shift(1)
    out["pool_low"] = df15m["low"].rolling(lookback).min().shift(1)
    # A pool built from candles up to and including the one opening at ts-15min
    # is known once that candle closes, i.e. at ts.
    return out.dropna().reset_index(drop=True)


def generate_orders(
    df1m: pd.DataFrame, config: LTFSweepConfig | None = None,
    df15m: pd.DataFrame | None = None, one_per_day: bool = True,
) -> pd.DataFrame:
    """Build orders from 1-minute bars carrying ``data.add_time_columns``."""
    config = config or LTFSweepConfig()
    if df1m.empty:
        return finalize([])

    if df15m is None:
        df15m = D.resample(df1m[["ts", "open", "high", "low", "close", "volume"]], "15min")
    pools = liquidity_pools(df15m, config.lookback_candles)
    if pools.empty:
        return finalize([])

    # Attach each 1-minute bar to the most recent *closed* 15m pool.
    merged = pd.merge_asof(
        df1m.sort_values("ts"), pools.sort_values("ts"),
        on="ts", direction="backward", allow_exact_matches=True,
    )

    rows = []
    for _, day in merged.groupby("trading_date", sort=True):
        rows.extend(_day_orders(day.reset_index(drop=True), config))

    if not rows:
        return finalize([])
    frame = pd.DataFrame(rows)
    return (one_trade_per_day(frame).reset_index(drop=True)
            if one_per_day else frame.reset_index(drop=True))


def _day_orders(day: pd.DataFrame, config: LTFSweepConfig) -> list[dict]:
    """All setups for one trading day, computed on that day's bars alone.

    Running the detectors per day keeps structure from leaking across the
    overnight gap, where the last bar of one session and the first of the next
    are adjacent in the array but hours apart in the market.
    """
    session = day[(day["minutes_from_midnight"] >= config.session_start)
                  & (day["minutes_from_midnight"] <= config.exit_minute)]
    if len(session) < 60:
        return []
    session = session.reset_index(drop=True)

    msb = find_msb(session, n=config.swing_n)
    if config.require_choch and not msb.empty:
        msb = msb[msb["structure"] == "choch"]
    if msb.empty:
        return []
    gaps = find_fvgs(session, min_size=config.min_fvg_size)
    if gaps.empty:
        return []

    step = D.bar_duration(session)
    ts = session["ts"]
    high = session["high"].to_numpy()
    low = session["low"].to_numpy()
    close = session["close"].to_numpy()
    ph = session["pool_high"].to_numpy(dtype="float64")
    pl = session["pool_low"].to_numpy(dtype="float64")

    limit = config.session_end
    out = []
    for i in range(len(session)):
        if session["minutes_from_midnight"].iloc[i] > limit:
            break
        # Sweeping the pool HIGH is bearish; sweeping the LOW is bullish.
        if not np.isnan(ph[i]) and high[i] > ph[i] + config.min_penetration and close[i] < ph[i]:
            o = _from_sweep(session, i, BEARISH, ph[i], high[i], msb, gaps, step, config)
            if o:
                out.append(o)
        elif not np.isnan(pl[i]) and low[i] < pl[i] - config.min_penetration and close[i] > pl[i]:
            o = _from_sweep(session, i, BULLISH, pl[i], low[i], msb, gaps, step, config)
            if o:
                out.append(o)
    return out


def _from_sweep(session, i, direction, level, extreme, msb, gaps, step, config) -> dict | None:
    """Find the CHoCH and the FVG that follow one sweep, and price the trade."""
    sweep_ts = session["ts"].iloc[i]
    sweep_confirmed = sweep_ts + step  # the sweep needs its own bar to close

    deadline_index = min(len(session) - 1, i + config.confirm_within_bars)
    deadline = session["ts"].iloc[deadline_index]

    breaks = msb[(msb["direction"] == direction)
                 & (msb["confirmed_at"] > sweep_confirmed)
                 & (msb["confirmed_at"] <= deadline)]
    if breaks.empty:
        return None
    confirmation = breaks.iloc[0]

    window = gaps[(gaps["direction"] == direction)
                  & (gaps["confirmed_at"] > sweep_confirmed)
                  & (gaps["confirmed_at"] <= confirmation["confirmed_at"])]
    if window.empty:
        return None
    gap = window.iloc[-1]

    entry = float(gap["midpoint"])

    # Protective extreme: the furthest point reached between the raid and the
    # structure break, not the sweeping bar's own wick.
    leg = session[(session["ts"] >= sweep_ts)
                  & (session["ts"] <= confirmation["confirmed_at"])]
    if leg.empty:
        return None
    if direction == BULLISH:
        protective = float(min(extreme, leg["low"].min()))
        stop = protective - config.stop_buffer_points
    else:
        protective = float(max(extreme, leg["high"].max()))
        stop = protective + config.stop_buffer_points

    return build_order(
        strategy="ltf_sweep", signal_ts=sweep_ts,
        valid_from=confirmation["confirmed_at"], day=session, direction=direction,
        entry=entry, stop=stop,
        target=fixed_r_target(entry, stop, direction, config.target_r),
        config=config,
        setup_note=f"swept 15m pool {level:.2f} then 1m choch",
        pool_level=float(level), sweep_extreme=protective,
        fvg_top=float(gap["top"]), fvg_bottom=float(gap["bottom"]),
        confirmation_ts=confirmation["confirmed_at"],
    )
