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


def test_commission_default_matches_the_default_contract():
    """Micro sizing with an E-mini fee is a hundred-fold cost overstatement.

    Fixed-risk sizing buys roughly ten times as many micros as e-minis for the
    same dollar risk, so pairing the E-mini per-contract fee with the micro tick
    value multiplies the fee by ten while dividing the tick value by ten.
    """
    from backtest.engine import COMMISSION_MNQ, COMMISSION_NQ

    cfg = BacktestConfig()
    assert cfg.tick_value == TICK_VALUE_MNQ
    assert cfg.commission_per_round_turn == COMMISSION_MNQ
    assert COMMISSION_MNQ < COMMISSION_NQ


def test_fees_scale_inversely_with_stop_distance():
    """Fixed risk means a tighter stop buys more contracts, so fees rise.

    This is the economics, not a defect: at a fixed dollar risk the contract
    count is inversely proportional to the stop distance, and the per-contract
    fee rides on the count. It sets a floor on the stop distance a model can
    profitably use, and it is why the same fee assumption that barely touches a
    70-point stop can dominate a 5-point one.
    """
    cfg = BacktestConfig(risk_per_trade_usd=500.0)
    fee = lambda stop: cfg.size_for(stop) * cfg.commission_per_round_turn

    assert fee(5.0) > fee(20.0) > fee(69.0)
    # A wide stop pays almost nothing; a very tight one pays a tenth of its risk.
    assert fee(69.0) / 500.0 < 0.01
    assert fee(5.0) / 500.0 > 0.10
