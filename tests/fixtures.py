"""Hand-built bar sequences whose correct output is obvious by construction.

Detectors are tested against bars designed so the expected answer can be read
off the numbers, not against real data where "it looks about right" is the only
available check.
"""

from __future__ import annotations

import pandas as pd

from ict.data import add_time_columns


def bars(rows: list[tuple[float, float, float, float]],
         start: str = "2024-01-02 13:30", freq: str = "5min",
         volume: int = 100) -> pd.DataFrame:
    """Build an OHLCV frame from (open, high, low, close) tuples.

    Default start is 09:30 New York on a winter Tuesday, so bars land inside
    the regular session and the time columns are meaningful.
    """
    ts = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "ts", ts)
    df["volume"] = volume
    return add_time_columns(df)


def flat(n: int, price: float = 100.0, **kwargs) -> pd.DataFrame:
    """``n`` identical doji bars — a baseline that should produce no signals."""
    return bars([(price, price, price, price)] * n, **kwargs)
