"""Tests for the buy / pass / earn / lose cycle.

Built from hand-made sequences rather than real trades: a test that runs the
backtest is testing the data, and the point here is the accounting.
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from backtest.recycle import Policy, XFARules, costs, recycle

RULES = XFARules()


def seq(pnls, contracts=1):
    """One trade a day, in order, at a size that never hits a ceiling."""
    days = pd.date_range("2026-01-01", periods=len(pnls), freq="1D", tz="UTC")
    return pd.DataFrame({
        "net_pnl": [float(p) for p in pnls],
        "contracts": [contracts] * len(pnls),
        "entry_ts": days, "exit_ts": days,
        "trading_date": days.date,
    })


def test_a_clean_pass_buys_one_account_and_pays_activation_once():
    out = recycle(seq([1000.0] * 3))
    assert out["accounts_bought"] == 1
    assert out["funded_earned"] == 1
    assert out["cost_lines"]["activation"] == RULES.activation


def test_the_evaluation_breaches_at_exactly_the_limit():
    assert recycle(seq([-2000.0]))["combine_busts"] == 1
    assert recycle(seq([-1999.0]))["combine_busts"] == 0


def test_every_bust_buys_another_account():
    # Three failed evaluations, then one that passes.
    out = recycle(seq([-2000.0, -2000.0, -2000.0] + [1000.0] * 3))
    assert out["combine_busts"] == 3
    assert out["accounts_bought"] == 4
    assert out["funded_earned"] == 1


def test_the_bust_streak_counts_consecutive_dead_accounts():
    out = recycle(seq([-2000.0, -2000.0, -2000.0] + [1000.0] * 3))
    assert out["longest_bust_streak"] == 3


def test_the_funded_limit_locks_once_the_balance_reaches_it():
    """The rule the whole model turns on: past +$2,000 an XFA cannot be killed,
    only emptied. Peak $2,500 would leave a trailing floor of $500; locked, the
    floor is $0 and a fall to $300 survives."""
    out = recycle(seq([1000.0] * 3 + [2500.0, -2200.0]))
    assert out["funded_earned"] == 1
    assert out["funded_busts"] == 0
    assert out["unrealised"] == pytest.approx(300.0)


def test_an_unlocked_funded_account_still_dies():
    """Same shape below the lock: peak $1,800, so the floor is -$200."""
    out = recycle(seq([1000.0] * 3 + [1800.0, -2100.0]))
    assert out["funded_earned"] == 1
    assert out["funded_busts"] == 1
    assert out["accounts_bought"] == 2


def test_a_payout_needs_five_winning_days():
    # Funded, then four winning days: eligible on the fifth, not before.
    four = recycle(seq([1000.0] * 3 + [600.0] * 4, ))
    assert four["payouts"] == 0
    five = recycle(seq([1000.0] * 3 + [600.0] * 5))
    assert five["payouts"] == 1


def test_a_day_under_the_minimum_is_not_a_winning_day():
    under = recycle(seq([1000.0] * 3 + [3000.0] + [149.0] * 5))
    assert under["payouts"] == 0
    over = recycle(seq([1000.0] * 3 + [3000.0] + [150.0] * 5))
    assert over["payouts"] == 1


def test_a_payout_is_half_the_balance_capped_and_split():
    out = recycle(seq([1000.0] * 3 + [600.0] * 5))
    balance = 600.0 * 5
    expect = balance * RULES.payout_share
    assert out["gross_payout"] == pytest.approx(expect)
    assert out["cash"] == pytest.approx(expect * RULES.profit_split)
    # Half was taken out, so half is left in.
    assert out["unrealised"] == pytest.approx(balance - expect)


def test_the_payout_cap_binds():
    out = recycle(seq([1000.0] * 3 + [4000.0] * 5))
    assert out["gross_payout"] == pytest.approx(RULES.payout_cap)


def test_the_winning_day_count_restarts_after_a_payout():
    once = recycle(seq([1000.0] * 3 + [600.0] * 9))
    twice = recycle(seq([1000.0] * 3 + [600.0] * 10))
    assert once["payouts"] == 1
    assert twice["payouts"] == 2


def test_taking_money_early_is_what_the_policy_chooses():
    """Five small winning days that never reach the lock. LOCK_FIRST waits and
    holds nothing back; IMMEDIATE takes the cash and keeps the account exposed."""
    trades = seq([1000.0] * 3 + [300.0] * 5)
    assert recycle(trades, policy=Policy.LOCK_FIRST)["payouts"] == 0
    assert recycle(trades, policy=Policy.IMMEDIATE)["payouts"] == 1


def test_the_scaling_plan_clamps_a_funded_position():
    """At a $1,400 balance the plan allows 20 micros, so a 25-contract trade
    earns four fifths of what the backtest gave it."""
    trades = seq([1000.0] * 3 + [1400.0, 500.0], contracts=25)
    # The first four are sized 25 too, but only the funded ones are clamped.
    out = recycle(trades)
    assert out["unrealised"] == pytest.approx(1400.0 * 20 / 25 + 500.0 * 20 / 25)


def test_the_funded_phase_can_be_sized_differently():
    """The two-stage plan: $900 evaluations, $250 once funded."""
    trades = seq([1000.0] * 3 + [800.0])
    out = recycle(trades, funded_scale=0.25)
    assert out["unrealised"] == pytest.approx(200.0)


def test_money_left_in_the_account_is_not_cash():
    out = recycle(seq([1000.0] * 3 + [2900.0]))
    assert out["unrealised"] == pytest.approx(2900.0)
    assert out["cash"] == 0.0
    assert out["net"] < 0


def test_two_trades_on_one_day_is_refused():
    """The end-of-day loss limit is only walkable trade by trade while a day
    holds one trade. Silently averaging two would misstate every breach."""
    trades = seq([100.0, 100.0])
    trades["trading_date"] = trades["trading_date"].iloc[0]
    with pytest.raises(ValueError, match="one day"):
        recycle(trades)


def test_costs_match_the_quarterly_report_for_a_single_pass():
    """recycle.costs is path_cost generalised to several activations. They have
    to agree wherever both apply, or the two reports disagree about spend."""
    from quarterly_report import PATHS, path_cost

    mine = costs(RULES, days=104, resets=10, activations=1)
    theirs = path_cost(PATHS["Standard"], accounts=11, days=104, passed=True)
    assert mine["total"] == pytest.approx(theirs["total"])
    assert mine["reset_count"] == theirs["reset_count"]


def test_costs_are_hand_checkable():
    c = costs(RULES, days=90, resets=5, activations=2)
    assert c["months"] == 3                      # ceil(90 / 30.4)
    assert c["free_credits"] == 2                # one per rebill after the first
    assert c["reset_count"] == 3                 # 5 resets less 2 credits
    assert c["total"] == pytest.approx(3 * 49.0 + 3 * 49.0 + 3 * 14.5 + 2 * 149.0)


def test_the_timeline_says_which_trade_caused_each_event():
    """Accounts do not outlive the loop, so how long one lived is recoverable
    only from the trade index on the event that ended it."""
    out = recycle(seq([1000.0] * 3 + [600.0] * 5))
    events = [(e["event"], e["trade"]) for e in out["timeline"]]
    assert events == [("passed", 2), ("payout", 7)]


def test_which_pricing_path_wins_turns_on_a_ratio_not_a_count():
    """The threshold is (months + paid resets) per funded account EARNED.

    Written as a bare count it is only right when exactly one account is
    earned, and a plan built on recycling earns many -- which is how
    docs/GO_LIVE.md came to recommend the more expensive path for the case it
    was actually describing.
    """
    noaf = XFARules(monthly=85.0, reset=85.0, activation=0.0)
    # 36 x (M + P) < 149 x A is the break-even, so the ratio is 149/36 = 4.14.
    days, resets = 365.0, 5

    def cheaper(activations):
        a = costs(RULES, days, resets, activations)["total"]
        b = costs(noaf, days, resets, activations)["total"]
        return "standard" if a < b else "no-activation"

    months = costs(RULES, days, resets, 1)["months"]
    paid = costs(RULES, days, resets, 1)["reset_count"]
    units = months + paid
    # One funded account: the ratio is the whole count, far above 4.14.
    assert units / 1 > 4.14 and cheaper(1) == "standard"
    # Enough funded accounts to bring the ratio under it, and it flips.
    enough = int(units / 4.14) + 1
    assert units / enough < 4.14 and cheaper(enough) == "no-activation"


def test_activation_is_the_only_line_that_scales_with_funded_accounts():
    base = costs(RULES, 365.0, 5, 1)
    more = costs(RULES, 365.0, 5, 4)
    assert more["activation"] == 4 * RULES.activation
    for line in ("subscription", "paid_resets", "api_access"):
        assert more[line] == base[line]
