"""Three-bar fair value gaps."""

from ict.events import BEARISH, BULLISH
from ict.fvg import find_fvgs
from tests.fixtures import bars, flat


def test_bullish_gap_between_first_high_and_third_low():
    # bar0 high 101, bar2 low 105 -> untraded band 101..105
    df = bars([(100, 101, 99, 100), (102, 106, 101, 105), (106, 108, 105, 107)])
    gaps = find_fvgs(df)
    assert len(gaps) == 1
    g = gaps.iloc[0]
    assert g["direction"] == BULLISH
    assert (g["bottom"], g["top"]) == (101, 105)
    assert g["midpoint"] == 103
    assert g["size"] == 4


def test_bearish_gap_is_the_mirror():
    df = bars([(107, 108, 105, 106), (104, 105, 100, 101), (100, 101, 98, 99)])
    g = find_fvgs(df).iloc[0]
    assert g["direction"] == BEARISH
    assert (g["bottom"], g["top"]) == (101, 105)


def test_gap_confirms_on_the_third_bar_not_the_displacement_bar():
    df = bars([(100, 101, 99, 100), (102, 106, 101, 105), (106, 108, 105, 107)])
    g = find_fvgs(df).iloc[0]
    assert g["ts"] == df.ts.iloc[1]            # anchored to the displacement bar
    assert g["confirmed_at"] == df.ts.iloc[2]  # visible only once bar 2 closes


def test_touching_bars_leave_no_gap():
    # bar2 low exactly equals bar0 high: no untraded band, so no FVG.
    df = bars([(100, 101, 99, 100), (101, 105, 100, 104), (104, 106, 101, 105)])
    assert find_fvgs(df).empty


def test_min_size_filters_narrow_gaps():
    df = bars([(100, 101, 99, 100), (102, 106, 101, 105), (106, 108, 102, 107)])
    assert len(find_fvgs(df, min_size=0.5)) == 1
    assert find_fvgs(df, min_size=5.0).empty


def test_flat_series_has_no_gaps():
    assert find_fvgs(flat(20)).empty
