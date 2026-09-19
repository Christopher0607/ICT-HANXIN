"""The opening-range setup detector and its pricing layer.

These check mechanics, not profitability. What the search built on top of them
is in ``scripts/search_orb.py`` and is a hypothesis, not a result.
"""
from __future__ import annotations

import pandas as pd
import pytest

from ict import data as D
from strategies.orb_family import DetectConfig, PriceConfig, detect, price


def bars(rows, start="2024-03-05 09:30"):
    ts = pd.date_range(pd.Timestamp(start, tz="America/New_York"),
                       periods=len(rows), freq="1min").tz_convert("UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "ts", ts)
    df["volume"] = 1
    return D.add_time_columns(df)


def opening(height=40.0, body=30.0, direction=1):
    low, high = 100.0, 100.0 + height
    o = low if direction > 0 else high
    return [(o, high, low, o)] * 4 + [(o, high, low, o + direction * body)]


def flat(price_, n):
    return [(price_, price_, price_, price_)] * n


def test_the_range_and_its_body_fraction():
    s = detect(bars(opening() + flat(141.0, 30)))
    assert len(s) == 1
    assert s.or_high[0] == 140.0 and s.or_low[0] == 100.0
    assert s.or_height[0] == 40.0
    assert s.or_body_frac[0] == pytest.approx(0.75)
    assert s.or_dir[0] == 1


def test_a_break_is_a_close_beyond_not_a_touch():
    """A wick through the level is not a break; a close is."""
    wicked = detect(bars(opening() + [(139, 145, 138, 139)] + flat(139.0, 20)))
    assert pd.isna(wicked.close_up_ts[0])
    assert wicked.touch_up_ts[0] is not None     # the high did reach it

    closed = detect(bars(opening() + [(139, 145, 138, 143)] + flat(143.0, 20)))
    assert closed.close_up_ts[0] is not None


def test_a_failed_breakout_records_its_excursion_high():
    """What fail_fade prices its stop from: how far the break got before it
    closed back inside."""
    day = opening() + [
        (140, 152, 139, 150),     # breaks and closes above; excursion high 152
        (150, 151, 137, 138),     # closes back inside the range
    ] + flat(138.0, 20)
    s = detect(bars(day))
    assert s.fail_up_ts[0] is not None
    assert s.fail_up_px[0] == pytest.approx(138.0)
    assert s.fail_up_high[0] == pytest.approx(152.0)


def test_a_break_that_never_comes_back_has_no_failure():
    s = detect(bars(opening() + [(140, 146, 139, 145)] + flat(160.0, 20)))
    assert s.close_up_ts[0] is not None
    assert pd.isna(s.fail_up_ts[0])


def test_fail_fade_trades_against_the_side_that_broke():
    """The break was up, so the fade is short -- and its stop sits above."""
    day = opening() + [
        (140, 152, 139, 150),
        (150, 151, 137, 138),
    ] + flat(138.0, 20)
    b = bars(day)
    o = price(detect(b), b, PriceConfig(entry_kind="fail_fade", side_rule="either"))
    assert len(o) == 1
    assert o.direction[0] == -1
    assert o.entry_price[0] == pytest.approx(136.0)       # failing close - 2
    assert o.stop_price[0] > o.entry_price[0]
    assert o.target_price[0] < o.entry_price[0]


def test_the_stop_kinds_give_different_distances():
    day = opening() + [(140, 152, 139, 150), (150, 151, 137, 138)] + flat(138.0, 20)
    b = bars(day)
    s = detect(b)
    seen = {}
    for kind, value in (("height", 0.5), ("fixed", 25.0), ("pullback", 3.0)):
        o = price(s, b, PriceConfig(entry_kind="fail_fade", side_rule="either",
                                    stop_kind=kind, stop_value=value))
        seen[kind] = float(o.risk_points[0])
    assert seen["height"] == pytest.approx(20.0)          # 0.5 x 40
    assert seen["fixed"] == pytest.approx(25.0)
    assert len(set(seen.values())) == 3


def test_the_stop_is_clamped_at_both_ends():
    day = opening(height=400.0, body=300.0) + [
        (500, 520, 499, 515), (515, 516, 480, 490)] + flat(490.0, 20)
    b = bars(day)
    o = price(detect(b), b, PriceConfig(entry_kind="fail_fade", side_rule="either",
                                        stop_kind="height", stop_value=1.0))
    assert o.risk_points[0] == pytest.approx(35.0)        # clamped down

    tiny = opening(height=4.0, body=3.0) + [
        (104, 106, 103, 105), (105, 105, 99, 100)] + flat(100.0, 20)
    b2 = bars(tiny)
    o2 = price(detect(b2), b2, PriceConfig(entry_kind="fail_fade", side_rule="either",
                                           stop_kind="height", stop_value=1.0))
    assert o2.risk_points[0] == pytest.approx(12.0)       # clamped up


def test_the_body_filter_drops_a_doji_day():
    day = opening(height=40.0, body=4.0) + [
        (140, 152, 139, 150), (150, 151, 137, 138)] + flat(138.0, 20)
    b = bars(day)
    s = detect(b)
    assert price(s, b, PriceConfig(entry_kind="fail_fade", side_rule="either",
                                   min_body_frac=0.4)).empty
    assert not price(s, b, PriceConfig(entry_kind="fail_fade", side_rule="either",
                                       min_body_frac=0.0)).empty


def test_orders_are_never_live_inside_their_signal_bar():
    day = opening() + [(140, 152, 139, 150), (150, 151, 137, 138)] + flat(138.0, 20)
    b = bars(day)
    o = price(detect(b), b, PriceConfig(entry_kind="fail_fade", side_rule="either"))
    assert (o["valid_from"] >= o["signal_ts"] + pd.Timedelta(minutes=1)).all()


def test_at_most_one_order_a_day():
    day = opening() + [(140, 152, 139, 150), (150, 151, 137, 138),
                       (138, 152, 137, 150), (150, 151, 137, 138)] + flat(138.0, 20)
    b = bars(day)
    o = price(detect(b), b, PriceConfig(entry_kind="fail_fade", side_rule="either"))
    assert len(o) <= 1
