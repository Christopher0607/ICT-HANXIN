"""Execution simulation: fills, exits, costs and the ambiguity band."""

import pandas as pd
import pytest

from backtest.engine import TICK_SIZE, BacktestConfig, simulate

TS = pd.date_range("2024-01-02 14:30", periods=8, freq="1min", tz="UTC")


def _bars(rows):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "ts", TS[: len(rows)])
    return df


def _order(**kw):
    base = {
        "signal_ts": TS[0], "valid_from": TS[0], "expires_at": TS[3],
        "direction": 1, "entry_price": 100.0, "stop_price": 95.0,
        "target_price": 110.0, "time_exit_ts": TS[-1],
    }
    base.update(kw)
    return pd.DataFrame([base])


RISING = _bars([(101, 101.5, 99.5, 100), (100, 101, 99, 100.5), (102, 104, 101, 103),
                (105, 107, 104, 106), (108, 110.5, 107, 109), (109.5, 110, 109, 109.5)])


def test_limit_order_fills_when_price_trades_to_it():
    t = simulate(_order(), RISING)
    assert bool(t.filled[0])
    assert t.entry_fill[0] == 100.0


def test_unreachable_limit_expires_and_is_kept_in_the_log():
    t = simulate(_order(entry_price=50.0), RISING)
    assert not bool(t.filled[0])
    assert t.exit_reason[0] == "expired"
    assert t.net_pnl[0] == 0.0
    # Retained rather than dropped, so fill rate stays visible in the stats.
    assert len(t) == 1


def test_target_exit_pays_commission_but_not_slippage():
    cfg = BacktestConfig(commission_per_round_turn=4.0, exit_slippage_ticks=1.0)
    t = simulate(_order(), RISING, cfg)
    assert t.exit_reason[0] == "target"
    assert t.exit_price[0] == 110.0
    assert t.points[0] == 10.0
    assert t.gross_pnl[0] == pytest.approx(10.0 / TICK_SIZE * 5.0)
    assert t.net_pnl[0] == pytest.approx(t.gross_pnl[0] - 4.0)


def test_stop_exit_slips_making_the_realised_loss_worse_than_one_r():
    cfg = BacktestConfig(exit_slippage_ticks=1.0)
    t = simulate(_order(stop_price=99.5, target_price=200.0), RISING, cfg)
    assert t.exit_reason[0] == "stop"
    assert t.exit_price[0] == pytest.approx(99.5 - TICK_SIZE)
    # Risk was 0.5 points; slippage turns a -1R stop into worse than -1R.
    assert t.r_multiple[0] < -1.0


def test_gap_through_the_stop_fills_at_the_open_not_the_stop_price():
    gapped = _bars([(100, 100.5, 99.8, 100), (90, 91, 88, 89)])
    t = simulate(_order(stop_price=95.0, expires_at=TS[0], time_exit_ts=TS[1]), gapped)
    # The bar opened at 90, below the 95 stop: you cannot get filled at 95.
    assert t.exit_price[0] == 90.0


def test_time_exit_flattens_at_the_close_of_the_last_bar():
    t = simulate(_order(target_price=500.0, stop_price=1.0), RISING)
    assert t.exit_reason[0] == "time_exit"
    assert t.exit_price[0] == pytest.approx(109.5 - TICK_SIZE)


def test_short_trade_mirrors_the_long_case():
    falling = _bars([(99, 100.5, 98, 99), (98, 99, 95, 96), (95, 96, 89, 90)])
    t = simulate(_order(direction=-1, entry_price=100.0, stop_price=105.0,
                        target_price=90.0, expires_at=TS[1], time_exit_ts=TS[2]), falling)
    assert bool(t.filled[0])
    assert t.exit_reason[0] == "target"
    assert t.points[0] == pytest.approx(10.0)
    assert t.net_pnl[0] > 0


def test_ambiguous_bar_is_flagged_and_resolves_by_policy():
    # Bar 1 spans 97..103, containing both the 98 stop and the 102 target.
    ambiguous = _bars([(100, 100.2, 99.8, 100), (100, 103, 97, 100)])
    o = _order(stop_price=98.0, target_price=102.0, expires_at=TS[0], time_exit_ts=TS[1])

    pess = simulate(o, ambiguous, BacktestConfig(ambiguity="pessimistic"))
    opt = simulate(o, ambiguous, BacktestConfig(ambiguity="optimistic"))

    assert bool(pess.ambiguous[0]) and bool(opt.ambiguous[0])
    assert pess.exit_reason[0] == "stop_ambiguous"
    assert opt.exit_reason[0] == "target_ambiguous"
    # The two policies bracket the true result; a wide gap means the edge is a
    # fill artifact rather than a real one.
    assert pess.net_pnl[0] < opt.net_pnl[0]


def test_rejects_an_invalid_ambiguity_policy():
    with pytest.raises(ValueError, match="ambiguity"):
        BacktestConfig(ambiguity="hopeful")


def test_missing_order_columns_fail_loudly():
    with pytest.raises(ValueError, match="missing columns"):
        simulate(pd.DataFrame([{"direction": 1}]), RISING)
