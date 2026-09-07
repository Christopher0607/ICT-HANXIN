"""Aggregation helpers: rolling windows and their percentile context."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.metrics import rolling_windows, summarize, window_percentile


def _trades(n, start="2024-01-01", pnl=100.0, freq="3D"):
    ts = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({
        "filled": True, "exit_ts": ts,
        "net_pnl": [pnl] * n, "gross_pnl": [pnl] * n,
        "r_multiple": [pnl / 100.0] * n, "bars_held": 1,
        "ambiguous": False, "exit_reason": "target", "contracts": 1,
    })


def test_windows_cover_the_history_and_carry_their_bounds():
    w = rolling_windows(_trades(120), months=3)
    assert not w.empty
    assert (w["end"] > w["start"]).all()
    assert (w["trades"] >= 5).all()
    # Stepping monthly over ~12 months of trades leaves roughly 10 three-month
    # windows, not one per trade.
    assert 5 <= len(w) <= 15


def test_thin_windows_are_dropped_rather_than_scored_on_two_trades():
    sparse = _trades(6, freq="60D")   # about one trade every two months
    assert rolling_windows(sparse, months=3).empty


def test_empty_input_returns_an_empty_frame_not_an_error():
    empty = pd.DataFrame(columns=["filled", "exit_ts", "net_pnl", "r_multiple"])
    assert rolling_windows(empty, months=3).empty


def test_percentile_places_a_value_in_the_distribution():
    w = pd.DataFrame({"profit_factor": [0.5, 0.8, 1.0, 1.2, 2.0]})
    assert window_percentile(w, 0.4) == 0.0
    assert window_percentile(w, 1.0) == 60.0
    assert window_percentile(w, 5.0) == 100.0


def test_percentile_ignores_infinite_profit_factors():
    # A window with no losing trade has an infinite factor; it must not poison
    # the distribution the current window is being compared against.
    w = pd.DataFrame({"profit_factor": [0.5, 1.0, np.inf, 2.0]})
    assert not np.isnan(window_percentile(w, 1.0))
    assert window_percentile(w, 1.0) == pytest.approx(200 / 3, abs=0.1)


def test_all_winners_and_all_losers_score_as_expected():
    assert summarize(_trades(20, pnl=100.0))["profit_factor"] == float("inf")
    assert summarize(_trades(20, pnl=-100.0))["profit_factor"] == 0.0
