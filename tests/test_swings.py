"""Fractal swing detection and its confirmation delay."""

import pandas as pd

from ict.swings import find_swings
from tests.fixtures import bars, flat


def test_finds_single_swing_high_at_the_peak():
    # A clean tent: bar 2 is the highest, two lower bars either side.
    df = bars([(100, 101, 99, 100), (100, 102, 99, 101), (101, 105, 100, 104),
               (104, 103, 99, 100), (100, 102, 98, 99)])
    swings = find_swings(df, n=2)
    highs = swings[swings.kind == "swing_high"]
    assert len(highs) == 1
    assert highs.iloc[0]["price"] == 105
    assert highs.iloc[0]["ts"] == df.ts.iloc[2]


def test_swing_confirms_n_bars_after_the_swing_itself():
    df = bars([(100, 101, 99, 100), (100, 102, 99, 101), (101, 105, 100, 104),
               (104, 103, 99, 100), (100, 102, 98, 99)])
    high = find_swings(df, n=2).iloc[0]
    # The peak is at bar 2 but is unknowable until bar 4 has CLOSED. Bar
    # timestamps are opens, so confirmation is bar 4's open plus one interval.
    assert high["ts"] == df.ts.iloc[2]
    assert high["confirmed_at"] == df.ts.iloc[4] + pd.Timedelta(minutes=5)


def test_plateau_of_equal_highs_is_not_a_swing():
    df = bars([(100, 101, 99, 100), (100, 105, 99, 104), (104, 105, 100, 104),
               (104, 105, 99, 100), (100, 102, 98, 99)])
    assert find_swings(df, n=2)[lambda d: d.kind == "swing_high"].empty


def test_flat_series_produces_no_swings():
    assert find_swings(flat(20), n=2).empty


def test_too_few_bars_returns_empty_not_error():
    assert find_swings(flat(3), n=2).empty
