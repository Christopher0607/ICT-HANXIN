"""Tests for the quarterly report's re-buy accounting."""
from __future__ import annotations

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import pandas as pd
import pytest
from quarterly_report import PATHS, PROFIT_TARGET, LOSS_LIMIT, path_cost, rebuy


def seq(pnls):
    n = len(pnls)
    ts = pd.date_range("2026-01-01", periods=n, freq="1D", tz="UTC")
    return pd.DataFrame({"net_pnl": pnls, "entry_ts": ts, "exit_ts": ts})


def test_a_clean_run_uses_one_account():
    assert rebuy(seq([1000.0, 1000.0, 1000.0]))["accounts"] == 1
    assert rebuy(seq([1000.0, 1000.0, 1000.0]))["passed"] is True


def test_each_breach_costs_an_account():
    """Down 2,000 from the peak three times, never reaching the target."""
    one_bust = [-1000.0, -1000.0, 500.0]          # -2,000 from a peak of 0
    out = rebuy(seq(one_bust * 3))
    assert out["accounts"] == 4, "three breaches means a fourth account"
    assert out["passed"] is False


def test_the_peak_resets_with_the_account():
    """A fresh account starts from zero, so old profit cannot cushion it."""
    out = rebuy(seq([2500.0, -2000.0, -100.0, -1900.0]))
    assert out["accounts"] == 3, "breach at -2,000 from 2,500, then again from 0"


def test_activation_is_charged_per_funded_account_earned():
    """Not once ever -- once for each XFA earned.

    A run of failures never pays it, which is the whole reason No-Activation is
    poor value for a re-buy plan. But lose the funded account and pass again and
    it lands again, so two cycles is two fees. ``path_cost`` covers one cycle
    because the replay stops at the first pass.
    """
    lost = path_cost(PATHS["Standard"], accounts=11, days=104, passed=False)
    won = path_cost(PATHS["Standard"], accounts=11, days=104, passed=True)
    assert lost["activation"] == 0.0, "failures alone never trigger it"
    assert won["activation"] == 149.0
    assert won["total"] - lost["total"] == 149.0

    # Two funded accounts earned is two activations -- the fee is per XFA, so
    # nothing about it is amortised across cycles.
    two_cycles = 2 * won["activation"]
    assert two_cycles == 298.0


def test_every_rebill_banks_one_free_reset():
    c = path_cost(PATHS["Standard"], accounts=11, days=104, passed=True)
    assert c["months"] == 4 and c["free_credits"] == 3
    assert c["reset_count"] == 7, "10 resets less 3 credits"
    assert c["paid_resets"] == pytest.approx(7 * 49.0)


def test_the_count_and_the_cost_are_different_keys():
    """They shared a name once and the count rendered as a dollar amount."""
    c = path_cost(PATHS["Standard"], accounts=11, days=104, passed=True)
    assert c["reset_count"] == 7
    assert c["paid_resets"] == pytest.approx(343.0)


def test_standard_wins_a_reset_heavy_run_and_loses_a_short_one():
    """The trade-off in one test: it turns on how many resets you buy."""
    heavy = {n: path_cost(p, accounts=11, days=104, passed=True)["total"]
             for n, p in PATHS.items()}
    assert heavy["Standard"] < heavy["No-Activation"]

    quick = {n: path_cost(p, accounts=1, days=20, passed=True)["total"]
             for n, p in PATHS.items()}
    assert quick["No-Activation"] < quick["Standard"], (
        "pass first time and the free activation is worth more than the premium")
