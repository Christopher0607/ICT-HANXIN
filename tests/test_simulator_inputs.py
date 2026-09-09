"""Tests for the simulator input statistics.

These are arithmetic, so they are checked against hand-computable cases rather
than against the backtest, which would just be the code testing itself.
"""

from __future__ import annotations

import numpy as np
import pytest

from scripts.simulator_inputs import (edge_stats, independence_check,
                                      longest_losing_streak, max_drawdown)


def test_edge_stats_on_a_hand_computable_series():
    """Six trades: four winners of +1R, two losers of -1R."""
    r = np.array([1.0, 1.0, -1.0, 1.0, 1.0, -1.0])
    s = edge_stats(r)
    assert s["trades"] == 6
    assert s["win_rate"] == pytest.approx(4 / 6)
    assert s["avg_win_r"] == pytest.approx(1.0)
    assert s["avg_loss_r"] == pytest.approx(1.0)
    assert s["payoff"] == pytest.approx(1.0)
    assert s["expectancy_r"] == pytest.approx(2 / 6)
    assert s["breakeven_win_rate"] == pytest.approx(0.5)


def test_breakeven_is_where_expectancy_crosses_zero():
    """The definition, checked rather than asserted.

    At the break-even win rate the expected value must be zero:
        p * avg_win - (1 - p) * avg_loss == 0
    """
    for avg_w, avg_l in ((0.895, 1.014), (1.0, 1.0), (2.0, 1.0), (0.5, 1.5)):
        r = np.array([avg_w, -avg_l])
        p = edge_stats(r)["breakeven_win_rate"]
        assert p * avg_w - (1 - p) * avg_l == pytest.approx(0.0, abs=1e-12)


def test_a_payoff_below_one_raises_the_breakeven_above_half():
    """The whole point of reporting payoff separately.

    A 1:1 target does not mean a 1.0 payoff once commissions are paid, and a
    simulator told 1.0 puts break-even at 50% instead of 53%.
    """
    s = edge_stats(np.array([0.895, -1.014]))
    assert s["payoff"] < 1.0
    assert s["breakeven_win_rate"] > 0.5
    assert s["breakeven_win_rate"] == pytest.approx(0.531, abs=0.001)


def test_zero_is_counted_as_a_loss_not_a_win():
    """A scratch trade still paid commission; it is not a winner."""
    s = edge_stats(np.array([1.0, 0.0]))
    assert s["win_rate"] == pytest.approx(0.5)


def test_max_drawdown_is_peak_to_valley():
    assert max_drawdown(np.array([1.0, 1.0, -3.0, 1.0])) == pytest.approx(-3.0)
    assert max_drawdown(np.array([1.0, 1.0, 1.0])) == pytest.approx(0.0)
    assert max_drawdown(np.array([-1.0, -1.0])) == pytest.approx(-2.0)


def test_losing_streak_counts_consecutive_non_wins():
    assert longest_losing_streak(np.array([1.0, -1.0, -1.0, 1.0, -1.0])) == 2
    assert longest_losing_streak(np.array([1.0, 1.0])) == 0
    assert longest_losing_streak(np.array([-1.0, -1.0, -1.0])) == 3


def test_independence_check_detects_a_sequence_that_is_not_independent():
    """Built so the answer is known: all losses first, then all wins.

    That ordering produces a far deeper drawdown than any reshuffle, which is
    exactly the condition that would make an i.i.d. simulator lie.
    """
    r = np.concatenate([np.full(30, -1.0), np.full(30, 1.0)])
    ind = independence_check(r, runs=200)
    assert ind["dd"] < ind["dd_shuffled_median"], "sorted series must drawdown deeper"
    assert ind["dd_percentile"] < 5, "and must sit in the tail of the reshuffles"


def test_independence_check_passes_a_shuffled_series():
    rng = np.random.default_rng(0)
    r = rng.permutation(np.concatenate([np.full(200, -1.0), np.full(220, 1.0)]))
    ind = independence_check(r, runs=200)
    assert abs(ind["autocorr_r"]) < 0.2
    assert 5 < ind["dd_percentile"] < 95
