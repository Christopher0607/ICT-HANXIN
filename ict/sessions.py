"""ICT killzones and session ranges, in New York local time.

ICT's entire framework is clock-driven: liquidity is built during one session
and taken during another.  The windows below are the standard ones, expressed
as New York local times so daylight saving is handled by the conversion in
``ict.data.add_time_columns`` rather than by arithmetic here.

The Asian session is the awkward one: it opens at 20:00 ET and runs past
midnight, so it belongs to the *next* trading day.  That is exactly why bars
carry ``trading_date`` (which rolls at 18:00 ET) instead of a calendar date.
"""

from __future__ import annotations

import pandas as pd

#: name -> (start minute-of-day ET, end minute-of-day ET). End is exclusive.
KILLZONES: dict[str, tuple[int, int]] = {
    "asian":        (20 * 60, 24 * 60),        # 20:00 - 00:00 ET
    "london":       (2 * 60, 5 * 60),          # 02:00 - 05:00 ET
    "ny_am":        (7 * 60, 10 * 60),         # 07:00 - 10:00 ET
    "silver_bullet": (10 * 60, 11 * 60),       # 10:00 - 11:00 ET
    "ny_pm":        (13 * 60 + 30, 16 * 60),   # 13:30 - 16:00 ET
    "rth":          (9 * 60 + 30, 16 * 60),    # 09:30 - 16:00 ET regular hours
}

RTH_OPEN_MINUTE = 9 * 60 + 30
RTH_CLOSE_MINUTE = 16 * 60


def in_window(df: pd.DataFrame, start_minute: int, end_minute: int) -> pd.Series:
    """Boolean mask for bars whose ET time falls in ``[start, end)``.

    Handles windows that wrap past midnight (e.g. the Asian session).
    """
    mins = df["minutes_from_midnight"]
    if start_minute <= end_minute:
        return (mins >= start_minute) & (mins < end_minute)
    return (mins >= start_minute) | (mins < end_minute)


def in_killzone(df: pd.DataFrame, name: str) -> pd.Series:
    """Boolean mask for bars inside the named killzone."""
    if name not in KILLZONES:
        raise KeyError(f"unknown killzone {name!r}; known: {sorted(KILLZONES)}")
    return in_window(df, *KILLZONES[name])


def session_ranges(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """High/low of the named session for each trading day.

    Returns one row per ``trading_date`` with ``high``, ``low``, ``start``,
    ``end``.  ``end`` is the close of the session's final bar and doubles as the
    timestamp from which the range may legitimately be used.
    """
    sel = df[in_killzone(df, name)]
    if sel.empty:
        return pd.DataFrame(columns=["trading_date", "high", "low", "start", "end"])
    grouped = sel.groupby("trading_date", sort=True).agg(
        high=("high", "max"), low=("low", "min"),
        start=("ts", "min"), end=("ts", "max"),
    ).reset_index()
    # `end` is the final bar's *open*. The range is only fully known once that
    # bar closes, so expose the moment it becomes usable and have callers gate
    # on that rather than on `end`.
    grouped["available_at"] = grouped["end"] + bar_duration(df)
    return grouped


def bar_duration(df: pd.DataFrame) -> pd.Timedelta:
    """Infer the bar interval from the most common timestamp spacing."""
    if len(df) < 2:
        raise ValueError("need at least two bars to infer bar duration")
    return pd.Timedelta(df["ts"].diff().dropna().mode().iloc[0])


def prior_day_levels(df: pd.DataFrame) -> pd.DataFrame:
    """Previous trading day's high, low and close, keyed by trading date.

    These are the liquidity pools ICT references most often (PDH / PDL).  The
    values are shifted forward one trading day, so the row for day D carries
    day D-1's levels and is safe to use from the first bar of day D.
    """
    daily = df.groupby("trading_date", sort=True).agg(
        high=("high", "max"), low=("low", "min"), close=("close", "last"),
    ).reset_index()
    out = pd.DataFrame({
        "trading_date": daily["trading_date"],
        "pdh": daily["high"].shift(1),
        "pdl": daily["low"].shift(1),
        "pdc": daily["close"].shift(1),
    })
    return out.dropna().reset_index(drop=True)


def session_coverage(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """Per-year completeness of the named session, as a fraction of full bars.

    Databento's ohlcv schema emits no bar for an interval with no trades, so a
    thin overnight session simply has fewer rows rather than rows of zeros.
    NQ's Asian session before roughly 2016 is only a third to a half populated,
    which means a "session high/low" from that era is taken from a fraction of
    the window and is not comparable to a modern one.

    Any study that uses session ranges should check this first and start where
    coverage is complete.
    """
    start, end = KILLZONES[name]
    span_minutes = (end - start) % (24 * 60) or 24 * 60
    expected = span_minutes / (bar_duration(df) / pd.Timedelta(minutes=1))

    sel = df[in_killzone(df, name)].copy()
    if sel.empty:
        return pd.DataFrame(columns=["year", "bars_per_day", "expected", "coverage"])
    sel["year"] = sel["ts"].dt.year
    per_day = sel.groupby(["year", "trading_date"]).size().groupby("year").mean()
    out = per_day.rename("bars_per_day").reset_index()
    out["expected"] = expected
    out["coverage"] = (out["bars_per_day"] / expected).round(3)
    return out
