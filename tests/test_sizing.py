"""Risk-based position sizing."""

import pytest

from backtest.engine import TICK_SIZE, TICK_VALUE_MNQ, BacktestConfig


def dollars_at_risk(contracts: int, stop_points: float, tick_value: float) -> float:
    return contracts * stop_points / TICK_SIZE * tick_value


def test_fixed_risk_never_exceeds_the_budget():
    cfg = BacktestConfig(risk_per_trade_usd=500.0)
    for stop in (2, 5, 12.5, 20, 47, 69, 120, 249):
        n = cfg.size_for(stop)
        assert n >= 1, f"{stop} points should be tradeable within budget"
        assert dollars_at_risk(n, stop, cfg.tick_value) <= 500.0


def test_sizing_scales_inversely_with_stop_distance():
    cfg = BacktestConfig(risk_per_trade_usd=500.0)
    tight, wide = cfg.size_for(10.0), cfg.size_for(100.0)
    assert tight > wide
    # Ten times the stop distance means roughly a tenth of the size, so both
    # trades put a comparable amount of money at risk.
    assert dollars_at_risk(tight, 10.0, cfg.tick_value) == pytest.approx(
        dollars_at_risk(wide, 100.0, cfg.tick_value), rel=0.25
    )


def test_a_stop_too_wide_for_the_budget_returns_zero():
    cfg = BacktestConfig(risk_per_trade_usd=500.0)
    # 400 points at $2/point is $800 for a single micro — over budget.
    assert cfg.size_for(400.0) == 0
    assert cfg.size_for(0.0) == 0
    assert cfg.size_for(-5.0) == 0


def test_fixed_contracts_ignores_the_stop_distance():
    cfg = BacktestConfig(sizing="fixed_contracts", contracts=3)
    assert cfg.size_for(5.0) == cfg.size_for(500.0) == 3


def test_micro_tick_value_is_the_default_for_granularity():
    # E-mini sizing on a $500 budget rounds a 69-point stop to zero contracts;
    # micros give three. Granularity is the reason for this default.
    assert BacktestConfig().tick_value == TICK_VALUE_MNQ
    assert BacktestConfig().size_for(69.0) == 3


def test_rejects_an_invalid_sizing_mode():
    with pytest.raises(ValueError, match="sizing"):
        BacktestConfig(sizing="martingale")
