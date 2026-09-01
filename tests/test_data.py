"""Timezone handling, trading-day rollover and the aggregation cross-check."""

import pandas as pd
import pytest

from ict import data as D
from ict.sessions import in_killzone

HAS_DATA = (D.PROCESSED_DIR / "nq_5m.parquet").exists()
needs_data = pytest.mark.skipif(not HAS_DATA, reason="run scripts/ingest.py first")


def _frame(times):
    df = pd.DataFrame({
        "ts": pd.to_datetime(times, utc=True),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1,
    })
    return D.add_time_columns(df)


def test_summer_and_winter_both_map_0930_et_correctly():
    # 09:30 ET is 13:30 UTC in July and 14:30 UTC in January.
    df = _frame(["2024-07-02 13:30", "2024-01-02 14:30"])
    assert list(df.minutes_from_midnight) == [9 * 60 + 30, 9 * 60 + 30]


def test_trading_day_rolls_at_1800_et_not_midnight():
    # 17:59 ET belongs to the 2nd; 18:00 ET already belongs to the 3rd.
    df = _frame(["2024-01-02 22:59", "2024-01-02 23:00"])
    assert str(df.trading_date.iloc[0]) == "2024-01-02"
    assert str(df.trading_date.iloc[1]) == "2024-01-03"


def test_asian_session_bars_belong_to_the_next_trading_day():
    df = _frame(["2024-01-02 01:00"])  # 20:00 ET on the 1st
    assert bool(in_killzone(df, "asian").iloc[0])
    assert str(df.trading_date.iloc[0]) == "2024-01-02"


def test_assert_clean_rejects_corrupt_bars():
    good = pd.DataFrame({"ts": pd.to_datetime(["2024-01-01", "2024-01-02"], utc=True),
                         "open": [1.0, 1.0], "high": [2.0, 2.0],
                         "low": [0.5, 0.5], "close": [1.5, 1.5]})
    D.assert_clean(good)

    with pytest.raises(ValueError, match="high < low"):
        bad = good.copy(); bad.loc[0, "high"] = 0.1
        D.assert_clean(bad)

    with pytest.raises(ValueError, match="outside"):
        bad = good.copy(); bad.loc[0, "close"] = 99.0
        D.assert_clean(bad)

    with pytest.raises(ValueError, match="duplicate"):
        D.assert_clean(pd.concat([good, good.head(1)]).sort_values("ts"))

    with pytest.raises(ValueError, match="monotonic"):
        D.assert_clean(good.iloc[::-1])


@needs_data
def test_five_minute_bars_rebuild_exactly_from_one_minute_bars():
    """End-to-end proof that the two shipped datasets are consistent.

    If this passes, the 5-minute file, the 1-minute file and our understanding
    of the aggregation boundary all agree.
    """
    df1 = D.load("1m", with_time_columns=False)
    df5 = D.load("5m", with_time_columns=False)
    window = slice("2026-01-01", "2026-02-01")

    sub1 = df1[(df1.ts >= window.start) & (df1.ts < window.stop)]
    rebuilt = D.resample_1m_to_5m(sub1)
    shipped = df5[(df5.ts >= window.start) & (df5.ts < window.stop)].reset_index(drop=True)

    merged = shipped.merge(rebuilt, on="ts", suffixes=("_shipped", "_rebuilt"))
    assert len(merged) > 5000, "windows did not overlap as expected"
    for col in ("open", "high", "low", "close", "volume"):
        assert (merged[f"{col}_shipped"] == merged[f"{col}_rebuilt"]).all(), col


@needs_data
def test_shipped_indicators_match_our_implementations():
    """The shipped ema12/atr14 reproduce exactly from the merged series."""
    df5 = D.load("5m", with_time_columns=False)
    for col, rebuilt in (("ema12", D.ema(df5["close"], 12)),
                         ("atr14", D.wilder_atr(df5, 14))):
        both = df5[col].notna() & rebuilt.notna()
        assert both.sum() > 900_000
        assert (df5[col][both] - rebuilt[both]).abs().max() < 1e-9, col


@needs_data
def test_indicator_seeding_leaves_the_documented_leading_nans():
    df5 = D.load("5m", with_time_columns=False)
    # Documented in docs/DATABENTO_README.md: 11 for ema12, 13 for atr14.
    assert int(df5["ema12"].isna().sum()) == 11
    assert int(df5["atr14"].isna().sum()) == 13
