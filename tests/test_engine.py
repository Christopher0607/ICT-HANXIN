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
    cfg = BacktestConfig(commission_per_round_turn=4.0, exit_slippage_ticks=1.0,
                         sizing="fixed_contracts", contracts=1)
    t = simulate(_order(), RISING, cfg)
    assert t.exit_reason[0] == "target"
    assert t.exit_price[0] == 110.0
    assert t.points[0] == 10.0
    assert t.gross_pnl[0] == pytest.approx(10.0 / TICK_SIZE * cfg.tick_value)
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


def test_through_fill_mode_requires_price_to_trade_past_the_limit():
    """'touch' fills at the level; 'through' needs a tick beyond it.

    At the limit price you are behind everyone already resting there, so a
    level that is merely tagged may leave the order unfilled. For a model
    taking thousands of trades this assumption dominates the result.
    """
    tagged = _bars([(101, 101.5, 100.0, 100.5), (100.5, 101, 100.2, 100.8)])
    o = _order(entry_price=100.0, expires_at=TS[1], time_exit_ts=TS[1])

    assert bool(simulate(o, tagged, BacktestConfig(entry_fill_mode="touch")).filled[0])
    # Price reached exactly 100.00 and turned; a conservative fill says no.
    assert not bool(simulate(o, tagged, BacktestConfig(entry_fill_mode="through")).filled[0])

    through = _bars([(101, 101.5, 99.5, 100.5), (100.5, 101, 100.2, 100.8)])
    assert bool(simulate(o, through, BacktestConfig(entry_fill_mode="through")).filled[0])


def test_rejects_an_invalid_fill_mode():
    with pytest.raises(ValueError, match="entry_fill_mode"):
        BacktestConfig(entry_fill_mode="hopeful")


def test_exit_fill_mode_governs_the_target_but_not_the_stop():
    """A target is a resting limit; a stop is a market order.

    The distinction matters most for tight targets, which are touched often and
    briefly — so a reward-to-risk sweep that favours small targets has to be
    checked against this assumption before it is believed.
    """
    # The first bar has to OPEN above a buy limit, or the order is marketable
    # before it rests and the engine declines it -- see the premise_failed
    # tests below.
    tagged = _bars([(100.8, 100.9, 99.8, 100), (100, 102.0, 99.9, 101)])
    o = _order(entry_price=100.5, stop_price=98.0, target_price=102.0,
               expires_at=TS[1], time_exit_ts=TS[1])
    # Price reached exactly 102.00.
    assert simulate(o, tagged, BacktestConfig(exit_fill_mode="touch")).exit_reason[0] == "target"
    assert simulate(o, tagged, BacktestConfig(exit_fill_mode="through")).exit_reason[0] != "target"

    # The stop is unaffected: touching it still triggers.
    s = _bars([(100, 100.2, 99.8, 100), (100, 100.1, 98.0, 98.5)])
    o2 = _order(entry_price=100.0, stop_price=98.0, target_price=200.0,
                expires_at=TS[0], time_exit_ts=TS[1])
    for mode in ("touch", "through"):
        assert simulate(o2, s, BacktestConfig(exit_fill_mode=mode)).exit_reason[0] == "stop"


def test_rejects_an_invalid_exit_fill_mode():
    with pytest.raises(ValueError, match="exit_fill_mode"):
        BacktestConfig(exit_fill_mode="hopeful")


# ---------------------------------------------------------------------------
# a limit the market has already passed

def test_a_buy_limit_the_market_opened_below_is_declined():
    """It is not a limit order any more; it is marketable.

    A buy limit at 100 with the market at 98 fills instantly at 98, not at
    100. Filling it at 100 invents a price the market had already left behind
    and sizes the trade off a stop distance that no longer applies.
    """
    bars = _bars([(98.0, 99.0, 97.0, 98.5), (99, 111, 98, 110)])
    t = simulate(_order(entry_price=100.0), bars)
    assert not bool(t.filled[0])
    assert t.exit_reason[0] == "premise_failed"


def test_a_sell_limit_the_market_opened_above_is_declined():
    """The same test with the signs flipped, which is where it would go wrong.

    This is the case that showed up live: a sell limit at 29,466 while price
    was at 29,516. TradingView filled it at 29,516; the engine was filling it
    at 29,466.
    """
    bars = _bars([(102.0, 103.0, 101.0, 102.5), (101, 102, 89, 90)])
    t = simulate(_order(direction=-1, entry_price=100.0,
                        stop_price=105.0, target_price=95.0), bars)
    assert not bool(t.filled[0])
    assert t.exit_reason[0] == "premise_failed"


def test_a_genuine_resting_limit_is_untouched():
    """The open on the correct side of the limit still fills normally."""
    long_t = simulate(_order(), RISING)                       # opens 101, limit 100
    assert bool(long_t.filled[0]) and long_t.entry_fill[0] == 100.0

    bars = _bars([(98.0, 101.0, 97.0, 100), (100, 101, 89, 90)])
    short_t = simulate(_order(direction=-1, entry_price=100.0,
                              stop_price=105.0, target_price=95.0), bars)
    assert bool(short_t.filled[0]) and short_t.entry_fill[0] == 100.0


def test_declined_orders_stay_in_the_log_and_count_against_fill_rate():
    """Silently dropping them would flatter the fill rate.

    The point of keeping unfilled orders is that a model which cannot get
    filled is not a good model, and hiding the rejects hides that.
    """
    bars = _bars([(98.0, 99.0, 97.0, 98.5), (99, 111, 98, 110)])
    t = simulate(_order(entry_price=100.0), bars)
    assert len(t) == 1
    assert t.net_pnl[0] == 0.0
    assert t.contracts[0] == 0


def test_the_old_behaviour_is_still_reachable_for_comparison():
    """Reproducing the previous numbers has to stay possible.

    Every figure published before this fix was computed with these fills in,
    so the switch that turns them back on is what makes those figures
    checkable rather than merely superseded.
    """
    bars = _bars([(98.0, 99.0, 97.0, 98.5), (99, 111, 98, 110)])
    t = simulate(_order(entry_price=100.0), bars,
                 BacktestConfig(skip_marketable_entries=False))
    assert bool(t.filled[0])
    assert t.entry_fill[0] == 100.0


# --- stop entries ------------------------------------------------------------
# A breakout model buys above the market. The level is reached by price going
# UP through it, not by price coming back down to it, and getting that backwards
# fills trades on days the market never offered one.

STOP_CFG = BacktestConfig(entry_side="stop")


def test_a_buy_stop_fills_only_when_price_trades_up_through_it():
    # Price opens below 102 and climbs through it on bar 2.
    t = simulate(_order(entry_price=102.0, stop_price=98.0), RISING, STOP_CFG)
    assert bool(t.filled[0])
    assert t.entry_ts[0] == TS[2]

    # The same level read as a limit is not a trade at all: the market opened
    # below it, so it is marketable rather than resting. One price, one set of
    # bars, and the two readings do not even agree that a trade exists.
    as_limit = simulate(_order(entry_price=102.0, stop_price=98.0), RISING)
    assert not bool(as_limit.filled[0])
    assert as_limit.exit_reason[0] == "premise_failed"


def test_a_buy_stop_that_gaps_pays_the_open_not_the_trigger():
    """A triggered market order cannot be filled better than the first price
    after it triggers, and pretending otherwise hands the model a price the
    market never showed."""
    gapped = _bars([(100, 100.5, 99.5, 100), (106, 108, 105.5, 107),
                    (107, 112, 106, 111)])
    t = simulate(_order(entry_price=102.0, stop_price=98.0, target_price=111.0),
                 gapped, STOP_CFG)
    assert t.entry_fill[0] == 106.0     # the open, not the 102 trigger


def test_a_sell_stop_mirrors_the_buy_case():
    falling = _bars([(100, 100.5, 99.5, 100), (99, 99.5, 96, 97),
                     (97, 97.5, 92, 93)])
    t = simulate(_order(direction=-1, entry_price=98.0, stop_price=102.0,
                        target_price=93.0), falling, STOP_CFG)
    assert bool(t.filled[0])
    assert t.entry_ts[0] == TS[1]


def test_the_marketable_guard_does_not_apply_to_stop_entries():
    """That guard exists because a limit the market has passed is no longer a
    limit. A stop below the market is just a breakout that already happened."""
    t = simulate(_order(entry_price=100.5, stop_price=96.0), RISING, STOP_CFG)
    assert bool(t.filled[0])
    assert t.exit_reason[0] != "premise_failed"


def test_rejects_an_invalid_entry_side():
    with pytest.raises(ValueError, match="entry_side"):
        BacktestConfig(entry_side="market")


# --- scaling out -------------------------------------------------------------
# Half off at the first target, the rest to a further one with its stop moved
# up. The headline win rate of such a model is the FIRST target's hit rate, so
# that has to survive as its own column.

SCALED = BacktestConfig(scale_out_fraction=0.5, runner_stop_offset=2.0)


def _runner_order(**kw):
    o = _order(**kw)
    o["runner_target"] = kw.pop("runner_target", 115.0)
    return o


# Fill at 100 on bar 0; bar 1 reaches the 105 first target without dipping to
# the runner's 102 stop; bar 2 reaches the 115 runner target.
TWO_LEG = _bars([(101, 101.5, 99.5, 100), (103, 106, 103, 105),
                 (106, 116, 105, 115)])


def test_scaling_out_blends_the_two_legs():
    t = simulate(_runner_order(target_price=105.0), TWO_LEG, SCALED)
    assert bool(t.tp1_hit[0]) and bool(t.scaled_out[0])
    # 50 contracts at $500 over a 5-point stop; 25 out at +5, 25 out at +15.
    assert t.contracts[0] == 50
    assert t.runner_points[0] == pytest.approx(15.0)
    assert t.points[0] == pytest.approx(10.0)
    assert t.exit_reason[0] == "target"


def test_a_stopped_trade_never_reaches_the_second_leg():
    stopped = _bars([(101, 101.5, 99.5, 100), (99, 99.5, 94, 96)])
    t = simulate(_runner_order(target_price=105.0), stopped, SCALED)
    assert not bool(t.tp1_hit[0])
    assert not bool(t.scaled_out[0])
    assert t.exit_reason[0] == "stop"


def test_the_runner_stop_sits_where_the_config_puts_it():
    pulled_back = _bars([(101, 101.5, 99.5, 100), (103, 106, 103, 105),
                         (104, 104.5, 101, 102)])
    t = simulate(_runner_order(target_price=105.0), pulled_back, SCALED)
    assert bool(t.scaled_out[0])
    # Stop moved to entry + 2, and a stop is a market order that slips a tick.
    assert t.runner_points[0] == pytest.approx(2.0 - TICK_SIZE)
    assert t.points[0] == pytest.approx((5.0 + 2.0 - TICK_SIZE) / 2)


def test_one_contract_cannot_be_halved_and_exits_whole():
    cfg = BacktestConfig(scale_out_fraction=0.5, runner_stop_offset=2.0,
                         sizing="fixed_contracts", contracts=1)
    t = simulate(_runner_order(target_price=105.0), TWO_LEG, cfg)
    assert bool(t.tp1_hit[0])
    assert not bool(t.scaled_out[0])      # flagged, not averaged away
    assert t.points[0] == pytest.approx(5.0)


def test_the_second_leg_is_off_unless_asked_for():
    """The whole point of the default: eight strategies already depend on this
    engine, and none of them should move because a ninth needed two legs."""
    plain = simulate(_order(target_price=105.0), TWO_LEG)
    with_column = simulate(_runner_order(target_price=105.0), TWO_LEG)
    assert plain.points[0] == with_column.points[0] == pytest.approx(5.0)
    assert not bool(with_column.scaled_out[0])


def test_rejects_an_invalid_scale_out_fraction():
    with pytest.raises(ValueError, match="scale_out_fraction"):
        BacktestConfig(scale_out_fraction=1.0)
