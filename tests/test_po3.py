"""PO3 / Judas Swing — above all, that the direction inverts."""

import pandas as pd

from ict.events import BEARISH, BULLISH
from ict.po3 import find_po3
from ict.data import add_time_columns


def _day(asian_rows, ny_rows, date="2024-01-02"):
    """Build one trading day: Asian bars from 20:00 ET, NY bars from 09:30 ET.

    January, so New York is UTC-5: 20:00 ET on the 1st is 01:00 UTC on the 2nd,
    and 09:30 ET is 14:30 UTC.
    """
    asian_ts = pd.date_range(f"{date} 01:00", periods=len(asian_rows), freq="5min", tz="UTC")
    ny_ts = pd.date_range(f"{date} 14:30", periods=len(ny_rows), freq="5min", tz="UTC")
    df = pd.DataFrame(
        list(asian_rows) + list(ny_rows), columns=["open", "high", "low", "close"]
    )
    df.insert(0, "ts", list(asian_ts) + list(ny_ts))
    df["volume"] = 100
    return add_time_columns(df)


# An Asian range of 100 points: high 15100, low 15000.
ASIAN = [(15050, 15100, 15000, 15050)] * 4


def test_sweeping_the_range_high_expects_a_move_DOWN():
    # Price pokes above 15100 and closes back inside: the raid was upward, so
    # the expected distribution is bearish. Reading this as strength is the
    # single most costly way to misapply the model.
    ny = [(15080, 15130, 15070, 15090)]
    events = find_po3(_day(ASIAN, ny))
    assert len(events) == 1
    e = events.iloc[0]
    assert e["direction"] == BEARISH
    assert e["swept_level"] == 15100
    assert e["sweep_extreme"] == 15130


def test_sweeping_the_range_low_expects_a_move_UP():
    ny = [(15020, 15030, 14970, 15010)]
    e = find_po3(_day(ASIAN, ny)).iloc[0]
    assert e["direction"] == BULLISH
    assert e["swept_level"] == 15000
    assert e["sweep_extreme"] == 14970


def test_closing_outside_the_range_is_a_breakout_not_a_judas():
    ny = [(15080, 15130, 15070, 15125)]  # closes above the range high
    assert find_po3(_day(ASIAN, ny)).empty


def test_staying_inside_the_range_produces_no_signal():
    ny = [(15050, 15090, 15010, 15060)]
    assert find_po3(_day(ASIAN, ny)).empty


def test_only_the_first_sweep_of_the_day_is_taken():
    ny = [(15080, 15130, 15070, 15090), (15090, 15140, 15060, 15080)]
    assert len(find_po3(_day(ASIAN, ny))) == 1


def test_range_outside_the_size_bounds_is_rejected():
    ny = [(15080, 15130, 15070, 15090)]
    day = _day(ASIAN, ny)
    assert find_po3(day, min_range=200.0).empty   # 100-point range is too small
    assert find_po3(day, max_range=50.0).empty    # ... and too large


def test_level_already_taken_before_the_window_is_not_a_fresh_raid():
    # A bar between the Asian close and 09:30 trades above the range high, so
    # that liquidity is already gone when New York opens.
    day = _day(ASIAN, [(15080, 15130, 15070, 15090)])
    pre = pd.DataFrame([{
        "ts": pd.Timestamp("2024-01-02 10:00", tz="UTC"),  # 05:00 ET, London
        "open": 15090, "high": 15120, "low": 15080, "close": 15090, "volume": 100,
    }])
    combined = add_time_columns(
        pd.concat([day.drop(columns=[c for c in day.columns if c not in
                   ("ts", "open", "high", "low", "close", "volume")]), pre],
                  ignore_index=True).sort_values("ts").reset_index(drop=True)
    )
    assert find_po3(combined, require_unswept=True).empty
    assert len(find_po3(combined, require_unswept=False)) == 1
