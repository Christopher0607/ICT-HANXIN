"""Market structure breaks, and the BOS / CHoCH distinction."""

from ict.events import BEARISH, BULLISH
from ict.structure import find_msb
from tests.fixtures import bars, flat


def test_close_above_a_confirmed_swing_high_is_a_break():
    df = bars([
        (100, 101, 99, 100), (100, 102, 99, 101), (101, 105, 100, 104),  # swing high 105
        (104, 104, 99, 100), (100, 102, 98, 99),                          # confirms it
        (99, 107, 98, 106),                                               # closes above 105
    ])
    msb = find_msb(df, n=2)
    assert len(msb) == 1
    assert msb.iloc[0]["direction"] == BULLISH
    assert msb.iloc[0]["level"] == 105
    assert msb.iloc[0]["bar_index"] == 5


def test_a_wick_through_the_level_is_not_a_break():
    df = bars([
        (100, 101, 99, 100), (100, 102, 99, 101), (101, 105, 100, 104),
        (104, 104, 99, 100), (100, 102, 98, 99),
        (99, 107, 98, 104),   # trades above 105 but closes back below
    ])
    assert find_msb(df, n=2).empty


def test_break_is_not_counted_against_an_unconfirmed_swing():
    # The swing high at bar 2 needs bars 3 and 4 to confirm. Bar 3 closing high
    # must not count as a break, because at bar 3 the swing did not yet exist.
    df = bars([
        (100, 101, 99, 100), (100, 102, 99, 101), (101, 105, 100, 104),
        (104, 110, 103, 109),  # closes above 105, but the swing is unconfirmed here
        (109, 111, 108, 110),
    ])
    msb = find_msb(df, n=2)
    assert msb.empty or (msb["bar_index"] >= 4).all()


def test_break_against_the_trend_is_a_choch():
    df = bars([
        (100, 101, 99, 100), (100, 102, 99, 101), (101, 105, 100, 104),
        (104, 104, 99, 100), (100, 102, 98, 99),
        (99, 107, 98, 106),      # bullish break of 105 -> trend is up
        (106, 110, 105, 109),
        (109, 110, 106, 107),
        (107, 108, 104, 105),    # swing low at 104 ...
        (105, 107, 105, 106),
        (106, 108, 105, 107),    # ... confirmed here
        (107, 108, 100, 101),    # closes below 104 while the trend is up
    ])
    msb = find_msb(df, n=2)
    bullish = msb[msb.direction == BULLISH]
    bearish = msb[msb.direction == BEARISH]
    assert len(bullish) == 1 and bullish.iloc[0]["structure"] == "bos"
    assert len(bearish) == 1
    # Breaking the opposing extreme while trending up is a change of character,
    # not a continuation.
    assert bearish.iloc[0]["structure"] == "choch"
    assert bearish.iloc[0]["level"] == 104


def test_flat_series_breaks_nothing():
    assert find_msb(flat(30), n=2).empty
