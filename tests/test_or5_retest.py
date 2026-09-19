"""Opening-range break and retest, checked against hand-built bars.

Real data would test the data. These test the rules in
``docs/or5_retest_spec.md``, one clause at a time, including the two the author
had to disambiguate: a bar may be both retest and trigger (B1a), and nothing
stops the runner's target landing inside the first one (B2).
"""
from __future__ import annotations

import pandas as pd
import pytest

from ict import data as D
from strategies.or5_retest import OR5Config, generate_orders, opening_range

CFG = OR5Config()


def bars(rows, start="2024-03-05 09:30"):
    """1-minute bars from (open, high, low, close), ET, with the time columns."""
    ts = pd.date_range(pd.Timestamp(start, tz="America/New_York"),
                       periods=len(rows), freq="1min").tz_convert("UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "ts", ts)
    df["volume"] = 1
    return D.add_time_columns(df)


def opening(height=40.0, body=30.0, direction=1):
    """Five 1-minute bars forming one opening candle of a given shape."""
    low, high = 100.0, 100.0 + height
    o = low if direction > 0 else high
    c = o + direction * body
    return [(o, high, low, o)] + [(o, high, low, o)] * 3 + [(o, high, low, c)]


def flat(price, n):
    return [(price, price, price, price)] * n


# --- the opening range -------------------------------------------------------

def test_the_range_is_the_first_five_minutes():
    r = opening_range(bars(opening()), bars(opening())["minutes_from_midnight"].to_numpy())
    assert r["high"] == 140.0 and r["low"] == 100.0
    assert r["height"] == 40.0 and r["direction"] == 1


def test_a_short_session_has_no_range():
    """Four bars is not the 09:30-09:35 candle. Padding it would invent one."""
    df = bars(opening()[:4])
    assert opening_range(df, df["minutes_from_midnight"].to_numpy()) is None


def test_a_doji_opening_is_not_traded():
    """F3: body under 40% of the range has no direction worth trading."""
    thin = opening(height=40.0, body=15.0)        # 37.5% of the range
    assert generate_orders(bars(thin + flat(141.0, 30))).empty
    fat = opening(height=40.0, body=17.0)         # 42.5%
    assert not generate_orders(bars(fat + flat(141.0, 30))).empty


# --- the sequence ------------------------------------------------------------
# Range 100-140, bullish, so the level is 140 and the retest band is 135-145.

def sequence_day(tail):
    return bars(opening() + tail)


def test_a_clean_break_retest_and_trigger_produces_one_order():
    tail = [
        (140, 146, 139, 145),     # T1: closes above 140
        (145, 145, 137, 138),     # T2: closes inside 135-145, below the level
        (138, 143, 137, 142),     # T3: closes above 140 again
    ] + flat(142.0, 20)
    o = generate_orders(sequence_day(tail))
    assert len(o) == 1
    assert o.direction[0] == 1
    assert o.entry_price[0] == pytest.approx(144.0)      # trigger close + 2


def test_price_that_never_comes_back_is_skipped():
    """T5: the run-away day is the price paid for the retest entry."""
    tail = [(140, 150, 139, 149)] + flat(160.0, 20)
    assert generate_orders(sequence_day(tail)).empty


def test_one_bar_can_be_both_retest_and_trigger():
    """B1a: a close between the level and the band's top satisfies both at
    once, which is the reading the author chose."""
    tail = [
        (140, 146, 139, 146),     # T1: above the level and above the band
        (146, 147, 141, 143),     # T2 and T3 in one bar: inside 135-145 and >140
    ] + flat(143.0, 20)
    o = generate_orders(sequence_day(tail))
    assert len(o) == 1
    assert o.entry_price[0] == pytest.approx(145.0)


def test_the_break_bar_cannot_also_be_the_retest():
    """The other overlap, B1b, was NOT chosen. The break here closes at 142,
    inside the 135-145 band, and price then leaves the band for good. Under
    B1b the break would count as its own retest and the next close above the
    level would trigger; under B1a, which is the chosen reading, the retest
    never happens and there is no trade."""
    tail = [(140, 146, 139, 142)] + flat(150.0, 20)
    assert generate_orders(sequence_day(tail)).empty


def test_a_trigger_after_ten_oclock_is_too_late():
    """T4: the sequence has until 10:00 ET."""
    late = flat(138.0, 26) + [(138, 143, 137, 142)] + flat(142.0, 10)
    tail = [(140, 146, 139, 145)] + late
    assert generate_orders(sequence_day(tail)).empty


# --- the stop ----------------------------------------------------------------

def test_the_stop_uses_the_lowest_bar_of_the_pullback():
    """A4: the pullback's extreme, not the trigger bar's own low."""
    tail = [
        (140, 146, 139, 145),
        (145, 145, 130, 138),     # the pullback's low, 130
        (138, 143, 136, 142),     # trigger; its own low is 136
    ] + flat(142.0, 20)
    o = generate_orders(sequence_day(tail))
    # 130 - 3 = 127 against a 144 entry is 17 points, inside the 12-35 bounds.
    assert o.stop_price[0] == pytest.approx(127.0)


def test_a_wide_pullback_is_clamped_to_the_maximum_stop():
    tail = [
        (140, 146, 139, 145),
        (145, 145, 90, 138),      # 90 - 3 = 87, which is 57 points away
        (138, 143, 137, 142),
    ] + flat(142.0, 20)
    o = generate_orders(sequence_day(tail))
    assert o.stop_price[0] == pytest.approx(144.0 - CFG.max_stop)


def test_a_tight_pullback_is_floored_at_the_minimum_stop():
    tail = [
        (140, 146, 139, 145),
        (145, 145, 141, 143),     # 141 - 3 = 138, only 6 points from entry
        (143, 146, 142, 144),
    ] + flat(146.0, 20)
    o = generate_orders(sequence_day(tail))
    assert o.risk_points[0] == pytest.approx(CFG.min_stop)


# --- the targets -------------------------------------------------------------

def test_the_first_target_is_one_r_and_the_runner_is_the_range_projected():
    tail = [
        (140, 146, 139, 145),
        (145, 145, 137, 138),
        (138, 143, 137, 142),
    ] + flat(142.0, 20)
    o = generate_orders(sequence_day(tail))
    entry, risk = o.entry_price[0], o.risk_points[0]
    assert o.target_price[0] == pytest.approx(entry + risk)
    assert o.runner_target[0] == pytest.approx(140.0 + 40.0)   # level + height


def test_a_narrow_range_puts_the_runner_target_inside_the_first_one():
    """B2, run as written rather than patched: with a 10-point range the
    runner's target is nearer than 1R, and the order says so."""
    day = opening(height=10.0, body=8.0) + [
        (110, 116, 109, 115),
        (115, 115, 107, 108),
        (108, 113, 107, 112),
    ] + flat(112.0, 20)
    o = generate_orders(bars(day))
    assert bool(o.height_under_r[0])
    assert o.runner_target[0] < o.target_price[0]


# --- shorts ------------------------------------------------------------------

def test_the_short_side_mirrors_the_long_one():
    """A5: confirmed as a straight mirror."""
    day = opening(direction=-1) + [
        (100, 101, 94, 95),       # T1: closes below 100
        (95, 103, 95, 102),       # T2: inside 95-105, above the level
        (102, 103, 97, 98),       # T3: closes below 100 again
    ] + flat(98.0, 20)
    o = generate_orders(bars(day))
    assert len(o) == 1
    assert o.direction[0] == -1
    assert o.entry_price[0] == pytest.approx(96.0)            # close - 2
    assert o.runner_target[0] == pytest.approx(100.0 - 40.0)  # level - height


# --- the day -----------------------------------------------------------------

def test_at_most_one_order_a_day():
    """P3, and the engine would otherwise be handed two positions at once."""
    tail = [
        (140, 146, 139, 145), (145, 145, 137, 138), (138, 143, 137, 142),
        (142, 146, 139, 145), (145, 145, 137, 138), (138, 143, 137, 142),
    ] + flat(142.0, 20)
    assert len(generate_orders(sequence_day(tail))) == 1


# --- causality ---------------------------------------------------------------

def test_the_order_is_not_live_inside_its_own_trigger_bar():
    """The regression that once gave this project a 100% fill rate. The trigger
    is a bar's CLOSE; an order valid from that bar's open could be filled
    inside it, on a price that had not printed when the signal existed."""
    tail = [
        (140, 146, 139, 145),
        (145, 145, 137, 138),
        (138, 143, 137, 142),
    ] + flat(142.0, 20)
    o = generate_orders(sequence_day(tail))
    assert (o["valid_from"] >= o["signal_ts"] + pd.Timedelta(minutes=1)).all()


def test_the_trigger_deadline_is_measured_at_the_bar_close():
    """T4's window ends at 10:00, and a bar opening at 09:59 closes on it."""
    # 09:35 break, then flat until the 09:59 bar triggers.
    tail = [(140, 146, 139, 145)] + flat(138.0, 23) + [(138, 143, 137, 142)]
    o = generate_orders(sequence_day(tail))
    assert len(o) == 1
    assert o.signal_ts[0] == pd.Timestamp("2024-03-05 09:59", tz="America/New_York")

    # One minute later the same bar closes at 10:01 and is out of the window.
    late = [(140, 146, 139, 145)] + flat(138.0, 24) + [(138, 143, 137, 142)]
    assert generate_orders(sequence_day(late)).empty


def test_orders_expire_and_exit_inside_their_own_session():
    tail = [
        (140, 146, 139, 145), (145, 145, 137, 138), (138, 143, 137, 142),
    ] + flat(142.0, 20)
    o = generate_orders(sequence_day(tail))
    assert (o["valid_from"] <= o["expires_at"]).all()
    assert (o["expires_at"] <= o["time_exit_ts"]).all()
    # A6: the entry stop rests until the flat time, not until 10:00.
    assert o["expires_at"][0] == pd.Timestamp("2024-03-05 10:45",
                                              tz="America/New_York")


def test_risk_points_agree_with_the_prices_they_came_from():
    tail = [
        (140, 146, 139, 145), (145, 145, 130, 138), (138, 143, 136, 142),
    ] + flat(142.0, 20)
    o = generate_orders(sequence_day(tail))
    computed = (o["entry_price"] - o["stop_price"]).abs()
    assert (computed - o["risk_points"]).abs().max() < 1e-9
