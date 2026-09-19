"""Opening-range setups, separated from how they are priced.

``or5_retest`` hard-codes one reading of one idea. This module exists because
the next question was "what else is in this family", and answering it by
regenerating orders for every parameter set would take a scan of eleven years
per combination.

So it splits in two. :func:`detect` walks the bars once and records what
happened each morning -- the range, and when price first touched or first
closed beyond each side. :func:`price` turns those facts into orders under a
given set of rules, which is arithmetic on a small frame and costs nothing.
A search over hundreds of parameter sets is then two scans, not hundreds.

**What this module is not.** Nothing here is pre-registered. ``registry.py``
holds seven strategies specified before anyone looked at their results, and
``or5_retest`` at least had its rules written down and committed before its
first run. A search does neither: it looks at the data and asks what would have
worked, which is how overfitting happens on purpose. Whatever comes out is a
hypothesis needing its own out-of-sample test, not a finding.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ict import data as D

from .base import minute_cutoff

OPEN_MINUTE = 9 * 60 + 30
RANGE_END_MINUTE = 9 * 60 + 35

#: Columns :func:`detect` produces. Everything :func:`price` needs to build an
#: order, and nothing that depends on how the order is priced.
SETUP_COLUMNS = [
    "trading_date", "or_high", "or_low", "or_height", "or_body_frac", "or_dir",
    "touch_up_ts", "touch_dn_ts", "close_up_ts", "close_up_px",
    "close_dn_ts", "close_dn_px", "retest_up_ts", "retest_up_px",
    "retest_dn_ts", "retest_dn_px", "retest_up_low", "retest_dn_high",
    "fail_up_ts", "fail_up_px", "fail_up_high",
    "fail_dn_ts", "fail_dn_px", "fail_dn_low",
]


@dataclass(frozen=True)
class DetectConfig:
    """What counts as an event. Deliberately permissive: filtering belongs in
    :func:`price`, where it can be varied without another scan."""

    #: Events after this minute are ignored, so every variant shares one window.
    cutoff_minute: int = 11 * 60
    #: The band around the level a close must land in to count as a retest.
    retest_band: float = 5.0


def _first(mask: np.ndarray, times: pd.DatetimeIndex, values: np.ndarray):
    hit = np.flatnonzero(mask)
    if not len(hit):
        return None, np.nan
    i = int(hit[0])
    return times[i], float(values[i])


def detect(df1m: pd.DataFrame, config: DetectConfig | None = None) -> pd.DataFrame:
    """One row per session: the opening range and the first of each event.

    ``retest_*`` follows ``or5_retest``'s reading: a close beyond the level,
    then a later close back inside the band, then a close beyond again. The
    extreme between the break and the trigger comes with it, because a stop
    placed off the pullback needs it and it cannot be recovered later.
    """
    config = config or DetectConfig()
    rows = []

    for date, day in df1m.groupby("trading_date", sort=True):
        minutes = day["minutes_from_midnight"].to_numpy()
        window = day[(minutes >= OPEN_MINUTE) & (minutes < RANGE_END_MINUTE)]
        if len(window) < 5:
            continue
        high, low = float(window["high"].max()), float(window["low"].min())
        height = high - low
        if height <= 0:
            continue
        opened, closed = float(window["open"].iloc[0]), float(window["close"].iloc[-1])

        cutoff = minute_cutoff(day, config.cutoff_minute)
        after = day[(minutes >= RANGE_END_MINUTE)]
        after = after[pd.DatetimeIndex(after["ts"]) < cutoff] if cutoff is not None else after
        if after.empty:
            continue

        ts = pd.DatetimeIndex(after["ts"])
        highs = after["high"].to_numpy()
        lows = after["low"].to_numpy()
        closes = after["close"].to_numpy()

        row = {
            "trading_date": date, "or_high": high, "or_low": low,
            "or_height": height, "or_body_frac": abs(closed - opened) / height,
            "or_dir": 1 if closed > opened else (-1 if closed < opened else 0),
        }
        row["touch_up_ts"], _ = _first(highs >= high, ts, highs)
        row["touch_dn_ts"], _ = _first(lows <= low, ts, lows)
        row["close_up_ts"], row["close_up_px"] = _first(closes > high, ts, closes)
        row["close_dn_ts"], row["close_dn_px"] = _first(closes < low, ts, closes)

        for side, level, beyond in ((1, high, closes > high), (-1, low, closes < low)):
            tag = "up" if side > 0 else "dn"
            broken = np.flatnonzero(beyond)
            t_ts, t_px, extreme = None, np.nan, np.nan
            f_ts, f_px, f_ext = None, np.nan, np.nan
            if len(broken):
                t1 = int(broken[0])
                retested, run, excursion = False, None, (highs[t1] if side > 0
                                                         else lows[t1])
                for i in range(t1 + 1, len(after)):
                    # The pullback's extreme, for a stop placed behind it.
                    run = (min(run, lows[i]) if side > 0 else max(run, highs[i])) \
                        if run is not None else (lows[i] if side > 0 else highs[i])
                    # The excursion's extreme, for a stop placed beyond a
                    # breakout that failed.
                    excursion = (max(excursion, highs[i]) if side > 0
                                 else min(excursion, lows[i]))
                    if abs(closes[i] - level) <= config.retest_band:
                        retested = True
                    if retested and beyond[i] and t_ts is None:
                        t_ts, t_px, extreme = ts[i], float(closes[i]), float(run)
                    # The breakout failing: a close back on the other side of
                    # the level it broke.
                    back_inside = (closes[i] < level) if side > 0 else (closes[i] > level)
                    if back_inside and f_ts is None:
                        f_ts, f_px, f_ext = ts[i], float(closes[i]), float(excursion)
                    if t_ts is not None and f_ts is not None:
                        break
            row[f"retest_{tag}_ts"] = t_ts
            row[f"retest_{tag}_px"] = t_px
            row[f"retest_{tag}_" + ("low" if side > 0 else "high")] = extreme
            row[f"fail_{tag}_ts"] = f_ts
            row[f"fail_{tag}_px"] = f_px
            row[f"fail_{tag}_" + ("high" if side > 0 else "low")] = f_ext
        rows.append(row)

    return pd.DataFrame(rows, columns=SETUP_COLUMNS)


@dataclass(frozen=True)
class PriceConfig:
    """How a detected setup becomes an order. Every field is a search knob."""

    #: "break_retest" -- or5_retest's sequence; "break_first" -- a stop the
    #: moment a bar closes beyond the level; "fail_fade" -- the opposite side,
    #: entered when a breakout closes back inside the range.
    entry_kind: str = "break_retest"
    #: "body" keeps or5_retest's F4 (trade the opening candle's direction only);
    #: "either" takes whichever side triggers first.
    side_rule: str = "body"
    #: "pullback" -- behind the pullback extreme; "fixed" -- a set distance;
    #: "height" -- a fraction of the opening range.
    stop_kind: str = "pullback"
    stop_value: float = 3.0
    min_stop: float = 12.0
    max_stop: float = 35.0
    entry_offset: float = 2.0
    #: The target, in multiples of the entry-to-stop distance. Below 1.0 is the
    #: high-win-rate, low-payoff shape.
    target_r: float = 1.0
    #: Minimum opening-candle body, as a share of the range. Zero takes every day.
    min_body_frac: float = 0.0
    exit_minute: int = 10 * 60 + 45
    trigger_deadline: int = 10 * 60


def _entry_columns(setups: pd.DataFrame, kind: str, side: int):
    """(trigger time, trigger price, stop reference) for one side."""
    tag = "up" if side > 0 else "dn"
    if kind == "break_retest":
        ref = f"retest_{tag}_" + ("low" if side > 0 else "high")
        return f"retest_{tag}_ts", f"retest_{tag}_px", ref
    if kind == "break_first":
        return f"close_{tag}_ts", f"close_{tag}_px", None
    if kind == "fail_fade":
        ref = f"fail_{tag}_" + ("high" if side > 0 else "low")
        return f"fail_{tag}_ts", f"fail_{tag}_px", ref
    raise ValueError(f"unknown entry_kind {kind!r}")


def price(setups: pd.DataFrame, bars: pd.DataFrame,
          config: PriceConfig | None = None) -> pd.DataFrame:
    """Turn setups into orders. Arithmetic on a small frame; no bar scan.

    A ``fail_fade`` trades AGAINST the side that broke, so its direction is the
    opposite of the break's -- and its entry is still a stop, placed beyond the
    close that failed, because the idea is continuation of the reversal.
    """
    config = config or PriceConfig()
    step = D.bar_duration(bars)
    days = {d: g for d, g in bars.groupby("trading_date", sort=False)}
    rows = []

    for setup in setups.itertuples():
        if setup.or_height <= 0 or setup.or_body_frac < config.min_body_frac:
            continue
        day = days.get(setup.trading_date)
        if day is None:
            continue
        deadline = minute_cutoff(day, config.trigger_deadline)
        if deadline is None:
            continue

        # Which side of the range is in play, and which way the trade points.
        sides = ([setup.or_dir] if config.side_rule == "body" else [1, -1])
        best = None
        for side in sides:
            if side == 0:
                continue
            ts_col, px_col, ref_col = _entry_columns(setups, config.entry_kind, side)
            trigger_ts = getattr(setup, ts_col)
            trigger_px = getattr(setup, px_col)
            if trigger_ts is None or pd.isna(trigger_ts) or pd.isna(trigger_px):
                continue
            if trigger_ts + step > deadline:
                continue
            if best is None or trigger_ts < best[0]:
                best = (trigger_ts, float(trigger_px), side,
                        getattr(setup, ref_col) if ref_col else np.nan)
        if best is None:
            continue

        trigger_ts, trigger_px, side, ref = best
        # A failed breakout is traded the other way.
        direction = -side if config.entry_kind == "fail_fade" else side
        level = setup.or_high if side > 0 else setup.or_low

        entry = trigger_px + direction * config.entry_offset
        if config.stop_kind == "pullback" and not pd.isna(ref):
            raw = abs(entry - (ref - direction * config.stop_value))
        elif config.stop_kind == "height":
            raw = config.stop_value * setup.or_height
        else:
            raw = config.stop_value
        distance = float(min(max(raw, config.min_stop), config.max_stop))

        rows.append({
            "signal_ts": trigger_ts, "valid_from": trigger_ts + step,
            "expires_at": minute_cutoff(day, config.exit_minute),
            "time_exit_ts": minute_cutoff(day, config.exit_minute),
            "direction": int(direction), "entry_price": float(entry),
            "stop_price": float(entry - direction * distance),
            "target_price": float(entry + direction * config.target_r * distance),
            "trading_date": setup.trading_date, "strategy": "orb_family",
            "risk_points": distance, "setup_note": config.entry_kind,
            "or_height": float(setup.or_height), "or_level": float(level),
        })

    if not rows:
        return pd.DataFrame(columns=["signal_ts", "valid_from", "expires_at",
                                     "direction", "entry_price", "stop_price",
                                     "target_price", "time_exit_ts"])
    return pd.DataFrame(rows)
