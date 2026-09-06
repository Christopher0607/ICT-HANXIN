"""Loading, timezone handling and CME trading-day labelling.

ICT is an entirely session-relative methodology: "the previous day's high",
"the Asian range", "the 09:30 open" are all New York clock concepts.  The
parquet files are stored in UTC, so every consumer needs the conversion done
once, correctly, in one place.

Two details that quietly wreck ICT backtests if you get them wrong:

**Daylight saving.**  09:30 New York is 13:30 UTC in summer and 14:30 UTC in
winter.  Slicing sessions by a fixed UTC offset silently mislabels half the
year, so all session logic runs on ``America/New_York`` local time via
``zoneinfo``.

**The CME trading day is not the calendar day.**  NQ trades Sunday 18:00 ET
through Friday 17:00 ET with a daily 17:00-18:00 ET maintenance halt.  Monday's
session therefore *begins* at 18:00 ET on Sunday.  Labelling by calendar date
would split every session in two and make "previous day's high" wrong for the
entire overnight stretch — which is exactly the stretch ICT setups reference.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

NY = "America/New_York"

#: Hour (ET) at which the CME electronic session rolls into the next trading day.
SESSION_ROLL_HOUR = 18

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

OHLCV = ["open", "high", "low", "close", "volume"]


def add_time_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Attach New York local time and CME trading-date columns.

    Adds:
      ``ts_ny``          -- bar open in America/New_York
      ``ny_date``        -- calendar date in New York
      ``ny_time``        -- time of day in New York
      ``trading_date``   -- CME trading day (rolls at 18:00 ET)
      ``minutes_from_midnight`` -- ET minutes, for fast session windowing
    """
    out = df.copy()
    ts_ny = out["ts"].dt.tz_convert(NY)
    out["ts_ny"] = ts_ny
    out["ny_date"] = ts_ny.dt.date
    out["ny_time"] = ts_ny.dt.time
    out["minutes_from_midnight"] = ts_ny.dt.hour * 60 + ts_ny.dt.minute

    # Bars at or after 18:00 ET belong to the *next* trading day.
    rolls = ts_ny.dt.hour >= SESSION_ROLL_HOUR
    trading = ts_ny.dt.normalize() + pd.to_timedelta(rolls.astype(int), unit="D")
    out["trading_date"] = trading.dt.date
    return out


def load_raw(pattern: str) -> pd.DataFrame:
    """Concatenate the raw parquet shards matching ``pattern`` under data/raw."""
    files = sorted(RAW_DIR.glob(pattern))
    if not files:
        raise FileNotFoundError(
            f"no raw files match {pattern!r} in {RAW_DIR}. "
            "See docs/DATABENTO_README.md for how to obtain them."
        )
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values("ts", kind="mergesort").reset_index(drop=True)
    assert_clean(df)
    return df


def assert_clean(df: pd.DataFrame) -> None:
    """Fail loudly on the data defects that would corrupt a backtest."""
    if not df["ts"].is_monotonic_increasing:
        raise ValueError("timestamps are not monotonically increasing")
    dupes = int(df["ts"].duplicated().sum())
    if dupes:
        raise ValueError(f"{dupes} duplicate timestamps")
    bad = df["high"] < df["low"]
    if bad.any():
        raise ValueError(f"{int(bad.sum())} bars with high < low")
    for col in ("open", "close"):
        outside = (df[col] > df["high"]) | (df[col] < df["low"])
        if outside.any():
            raise ValueError(f"{int(outside.sum())} bars with {col} outside [low, high]")


def load(timeframe: str, with_time_columns: bool = True) -> pd.DataFrame:
    """Load a processed dataset. ``timeframe`` is ``"1m"`` or ``"5m"``."""
    path = PROCESSED_DIR / f"nq_{timeframe}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `uv run python scripts/ingest.py` first."
        )
    df = pd.read_parquet(path)
    return add_time_columns(df) if with_time_columns else df


def bar_duration(df: pd.DataFrame) -> pd.Timedelta:
    """Infer the bar interval from the most common timestamp spacing.

    Detectors need this to answer "when did this bar close?", which is when a
    close-based pattern first became knowable.
    """
    if len(df) < 2:
        raise ValueError("need at least two bars to infer bar duration")
    return pd.Timedelta(df["ts"].diff().dropna().mode().iloc[0])


def resample_1m_to_5m(df1m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1-minute bars to 5-minute bars on UTC epoch boundaries.

    Matches the aggregation described in docs/DATABENTO_README.md, and is used
    by the test suite to verify the shipped 5-minute file bar for bar.
    """
    return resample(df1m, "5min")


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate OHLCV bars to a coarser interval on UTC epoch boundaries.

    Epoch origin keeps every coarser interval aligned with the finer ones, and
    with the New York session: 09:30 ET falls on a 5-, 15- and 30-minute
    boundary in both daylight and standard time.
    """
    agg = (
        df.set_index("ts")
        .resample(rule, origin="epoch", label="left", closed="left")
        .agg(open=("open", "first"), high=("high", "max"),
             low=("low", "min"), close=("close", "last"),
             volume=("volume", "sum"))
        .dropna(subset=["open"])
        .reset_index()
    )
    agg["volume"] = agg["volume"].astype("uint64")
    return agg


def ema(series: pd.Series, span: int) -> pd.Series:
    """EMA seeded with the SMA of the first ``span`` values.

    Seeding matters: recursing from the very first bar gives a different curve
    for hundreds of bars, which silently shifts every signal that depends on it.
    """
    alpha = 2.0 / (span + 1.0)
    seeded = series.copy().astype("float64")
    seeded.iloc[: span - 1] = pd.NA
    seeded.iloc[span - 1] = series.iloc[:span].mean()
    return seeded.ewm(alpha=alpha, adjust=False, ignore_na=False).mean()


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR (alpha = 1/period), seeded with the SMA of true range."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    # The first bar has no previous close, so its true range degenerates to the
    # bar range. Including it puts the seed at index period-1, matching the
    # shipped atr14 column (13 leading NaNs for period=14).
    seeded = tr.copy()
    seeded.iloc[: period - 1] = pd.NA
    seeded.iloc[period - 1] = tr.iloc[:period].mean()
    return seeded.ewm(alpha=1.0 / period, adjust=False, ignore_na=False).mean()
