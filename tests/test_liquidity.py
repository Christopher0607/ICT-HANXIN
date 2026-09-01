"""Sweeps versus breaks — the same level, opposite conclusions."""

from ict.events import BEARISH, BULLISH
from ict.liquidity import find_equal_levels, find_sweeps
from ict.swings import find_swings
from tests.fixtures import bars, flat

_SETUP = [
    (100, 101, 99, 100), (100, 102, 99, 101), (101, 105, 100, 104),  # swing high 105
    (104, 104, 99, 100), (100, 102, 98, 99),                          # confirms it
]


def test_wick_above_a_swing_high_that_closes_back_below_is_a_bearish_sweep():
    df = bars(_SETUP + [(99, 107, 98, 103)])
    sweeps = find_sweeps(df, n=2)
    assert len(sweeps) == 1
    s = sweeps.iloc[0]
    # Taking out a high is BEARISH: liquidity was grabbed and rejected.
    assert s["direction"] == BEARISH
    assert s["level"] == 105
    assert s["extreme"] == 107
    assert s["penetration"] == 2


def test_a_close_beyond_the_level_is_a_break_not_a_sweep():
    df = bars(_SETUP + [(99, 107, 98, 106)])
    assert find_sweeps(df, n=2).empty


def test_min_penetration_rejects_a_level_that_is_merely_touched():
    df = bars(_SETUP + [(99, 105.25, 98, 103)])
    assert len(find_sweeps(df, n=2, min_penetration=0.0)) == 1
    assert find_sweeps(df, n=2, min_penetration=1.0).empty


def test_equal_highs_cluster_into_one_pool():
    df = bars([
        (100, 101, 99, 100), (100, 102, 99, 101), (101, 110, 100, 104),
        (104, 104, 99, 100), (100, 102, 98, 99), (99, 103, 98, 102),
        (102, 110.5, 101, 103), (103, 103, 99, 100), (100, 101, 98, 99),
    ])
    pools = find_equal_levels(find_swings(df, n=2), tolerance=2.0)
    highs = pools[pools.kind == "equal_highs"]
    assert len(highs) == 1
    assert highs.iloc[0]["count"] == 2


def test_flat_series_sweeps_nothing():
    assert find_sweeps(flat(30), n=2).empty
