"""Opening-range break and retest: the 09:30-09:35 candle, then a 1-minute retest.

A different shape again from the eight already here. Those enter on a
retracement into a level -- a limit order, filled when price comes back. This
one buys *above* the market after a break has been confirmed and retested, so
its entry is a stop order, filled when price goes through. ``backtest/engine.py``
had to learn that distinction before this file could exist.

The rules are not ICT's and not mine: they came from outside the project and
are recorded, with every ambiguity that had to be resolved and who resolved it,
in ``docs/or5_retest_spec.md``. That document was written and committed before
the first backtest ran, because this model does not get the pre-registration
the seven in ``registry.py`` have -- it was invented after sixteen years of this
data had already been looked at. Read the spec before changing anything here.

The shape:

1. **Opening range** -- the 09:30-09:35 ET five-minute candle. Its high and low
   are the levels; its height sets the runner's target; its body decides the
   day's direction and whether the day is traded at all.
2. **Break** (T1) -- a 1-minute bar *closes* beyond the level.
3. **Retest** (T2) -- a later 1-minute bar *closes* back inside a band around
   the level.
4. **Trigger** (T3) -- a bar closes beyond the level again. One bar can be both
   retest and trigger, and often is: a close just above the high is inside the
   band and above the level at once.
5. **Entry** -- a stop two points beyond that close.
6. **Exit** -- half off at 1R, the rest to the range height projected from the
   level, stop moved to breakeven plus two, flat at 10:45 ET.

**A consequence of the rules as given.** There is no floor on the range height,
so on a narrow morning the runner's target can sit nearer than the first
target, inverting the two legs. The spec records this, measures it at 14% of
qualifying days across the sample, and records the decision to run it as
written rather than patch it. ``height_under_r`` on each order marks those days
so the report can show them separately instead of averaging them in.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ict import data as D

from .base import BaseConfig, build_order, day_groups, finalize, minute_cutoff

#: Minutes from midnight ET.
OPEN_MINUTE = 9 * 60 + 30
RANGE_END_MINUTE = 9 * 60 + 35


@dataclass(frozen=True)
class OR5Config(BaseConfig):
    """Parameters. Every one of them is from ``docs/or5_retest_spec.md``;
    none was searched over, and the spec was committed before the first run."""

    #: F3: the opening candle's body must be at least this share of its own
    #: high-low range. A doji has no direction to trade.
    min_body_fraction: float = 0.40
    #: T2: how far either side of the level a close still counts as a retest.
    retest_band: float = 5.0
    #: T3: how far beyond the trigger bar's close the entry stop sits.
    entry_offset: float = 2.0
    #: S1: the buffer beyond the pullback extreme.
    stop_buffer: float = 3.0
    #: S2: hard bounds on the resulting stop distance.
    min_stop: float = 12.0
    max_stop: float = 35.0
    #: P1: the first target, in multiples of risk.
    tp1_r: float = 1.0
    #: P2: the runner's target, as a multiple of the range height projected
    #: from the broken level.
    runner_height_mult: float = 1.0
    #: P2: where the runner's stop goes once the first target is hit.
    runner_stop_offset: float = 2.0

    #: T4: the trigger must happen by this minute...
    trigger_deadline: int = 10 * 60
    #: ...and the entry stop, once placed, rests until the flat time. A6 in the
    #: spec: "the order, once placed, is fine". The flat time is the only thing
    #: that cancels it, since a fill after that would be closed on arrival.
    entry_deadline: int = 10 * 60 + 45
    exit_minute: int = 10 * 60 + 45

    #: The engine rejects a stop outside these; S2 already clamps into range,
    #: so these only catch a bug in the clamping.
    min_risk_points: float = 1.0
    max_risk_points: float = 250.0


def opening_range(day: pd.DataFrame, minutes: np.ndarray) -> dict | None:
    """The 09:30-09:35 candle, built from the five 1-minute bars inside it.

    Aggregated rather than resampled so a missing bar shows up as a short
    window and is skipped, instead of being silently padded into a candle that
    never traded.
    """
    window = day[(minutes >= OPEN_MINUTE) & (minutes < RANGE_END_MINUTE)]
    if len(window) < 5:
        return None
    high = float(window["high"].max())
    low = float(window["low"].min())
    height = high - low
    if height <= 0:
        return None
    opened = float(window["open"].iloc[0])
    closed = float(window["close"].iloc[-1])
    return {"high": high, "low": low, "height": height,
            "open": opened, "close": closed,
            "body": abs(closed - opened),
            "direction": 1 if closed > opened else (-1 if closed < opened else 0)}


def _sequence(bars: pd.DataFrame, level: float, direction: int,
              config: OR5Config, deadline: pd.Timestamp, step: pd.Timedelta):
    """Walk 1-minute bars for break, retest and trigger.

    Returns ``(trigger_row, pullback_extreme)`` or ``(None, None)``.

    The retest scan starts on the bar AFTER the break: the spec's answer (a)
    lets one bar serve as both retest and trigger, but not as both break and
    retest. So each bar is tested for the retest first and the trigger second,
    and a close just beyond the level satisfies both at once.
    """
    closes = bars["close"].to_numpy()
    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    times = pd.DatetimeIndex(bars["ts"])

    beyond = (closes > level) if direction > 0 else (closes < level)
    broken = np.flatnonzero(beyond)
    if not len(broken):
        return None, None
    t1 = int(broken[0])

    retested = False
    extreme = None
    for i in range(t1 + 1, len(bars)):
        # A bar's ts is its OPEN. The trigger is a close, so the deadline
        # applies to when the bar finished: the 09:59 bar closes at 10:00 and
        # is the last one that counts.
        if times[i] + step > deadline:
            break
        # Everything between the break and the trigger is "the pullback", and
        # S1 measures its extreme -- not the trigger bar's own extreme.
        extreme = (min(extreme, lows[i]) if direction > 0 else max(extreme, highs[i])) \
            if extreme is not None else (lows[i] if direction > 0 else highs[i])

        in_band = abs(closes[i] - level) <= config.retest_band
        if in_band:
            retested = True
        if retested and ((closes[i] > level) if direction > 0 else (closes[i] < level)):
            return bars.iloc[i], extreme
    return None, None


def generate_orders(df1m: pd.DataFrame, config: OR5Config | None = None) -> pd.DataFrame:
    """One order per qualifying day, on 1-minute bars."""
    config = config or OR5Config()
    step = D.bar_duration(df1m)
    rows = []

    for _, day in day_groups(df1m).items():
        minutes = day["minutes_from_midnight"].to_numpy()
        rng = opening_range(day, minutes)
        if rng is None or rng["direction"] == 0:
            continue
        # F3 and F4: a body worth trading, and its direction.
        if rng["body"] < config.min_body_fraction * rng["height"]:
            continue

        direction = rng["direction"]
        level = rng["high"] if direction > 0 else rng["low"]

        after = day[minutes >= RANGE_END_MINUTE]
        deadline = minute_cutoff(day, config.trigger_deadline)
        if after.empty or deadline is None:
            continue

        trigger, extreme = _sequence(after, level, direction, config, deadline, step)
        if trigger is None or extreme is None:
            continue

        entry = float(trigger["close"]) + direction * config.entry_offset
        raw_stop = extreme - direction * config.stop_buffer
        # S2: the hard bounds, applied to the distance rather than the price.
        distance = min(max(abs(entry - raw_stop), config.min_stop), config.max_stop)
        stop = entry - direction * distance

        target = entry + direction * config.tp1_r * distance
        runner = level + direction * config.runner_height_mult * rng["height"]

        order = build_order(
            strategy="or5_retest",
            # The signal is the trigger bar's CLOSE, so the order cannot be
            # live until the next bar opens. Making it valid at the trigger
            # bar's own open lets the engine fill it inside that bar, using a
            # close that had not printed -- the regression that once gave this
            # project a 100% fill rate.
            signal_ts=trigger["ts"], valid_from=trigger["ts"] + step, day=day,
            direction=direction, entry=entry, stop=stop, target=target,
            config=config,
            setup_note=(f"OR5 {rng['height']:.1f}pt range, "
                        f"stop {distance:.1f}pt"),
            runner_target=float(runner),
            range_height=rng["height"],
            # The rules put no floor under the range, so the runner's target
            # can land nearer than the first one. Marked, not silently mixed in.
            height_under_r=bool(rng["height"] < distance),
        )
        if order is not None:
            rows.append(order)

    return finalize(rows)
