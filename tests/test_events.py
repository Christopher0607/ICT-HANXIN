"""The confirmed_at contract is the backbone of every other guarantee."""

import pandas as pd
import pytest

from ict.events import BULLISH, latest_visible, make_events, validate_events, visible_at

T0 = pd.Timestamp("2024-01-02 13:30", tz="UTC")


def _event(ts_offset, confirm_offset):
    return {
        "ts": T0 + pd.Timedelta(minutes=ts_offset),
        "confirmed_at": T0 + pd.Timedelta(minutes=confirm_offset),
        "kind": "test", "direction": BULLISH,
    }


def test_empty_frame_has_full_schema():
    df = make_events([], ["top", "bottom"])
    assert list(df.columns) == ["ts", "confirmed_at", "kind", "direction", "top", "bottom"]
    assert df.empty


def test_rejects_event_confirmed_before_it_happened():
    bad = pd.DataFrame([_event(10, 5)])
    with pytest.raises(ValueError, match="confirm before they occur"):
        validate_events(bad)


def test_visible_at_excludes_unconfirmed_events():
    df = make_events([_event(0, 10), _event(5, 20)])
    # At minute 15 only the first event has confirmed, even though both have
    # already *occurred*. This is exactly the distinction that prevents lookahead.
    assert len(visible_at(df, T0 + pd.Timedelta(minutes=15))) == 1
    assert len(visible_at(df, T0 + pd.Timedelta(minutes=25))) == 2
    assert visible_at(df, T0).empty


def test_latest_visible_returns_none_before_anything_confirms():
    df = make_events([_event(0, 10)])
    assert latest_visible(df, T0) is None
    assert latest_visible(df, T0 + pd.Timedelta(minutes=10)) is not None
