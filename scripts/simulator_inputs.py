"""Produce the numbers a Monte Carlo simulator actually needs.

    uv run python scripts/simulator_inputs.py

Typing "55% win rate, 1:1 risk/reward" into a simulator gives the wrong answer
for this strategy, in the direction that flatters it. Two reasons:

**The payoff is not 1.0.** The target is a 1:1 multiple of risk, but
commissions are not: a winner nets about +0.895R and a loser costs about
1.014R. That moves the break-even win rate from 50% to roughly 53%. Enter 1:1
and a 51.4% win rate looks profitable when the same 51.4% actually lost
$38,434 over the full history.

**The win rate depends entirely on which window you take.** Over 2016-2026 it
is 51.4%, below break-even. Since 2024 it is 54.9%, above it. That gap is the
whole open question about this strategy, not a detail -- so this script prints
every window rather than a single headline number, and the honest reading is
to run the simulator twice and treat the spread as the uncertainty.

One assumption a Monte Carlo does need, independence, was checked rather than
assumed. It holds; the check is reprinted on every run so it stays checked.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from backtest.engine import BacktestConfig, simulate
from ict import data as D
from strategies.ltf_sweep import LTFSweepConfig, generate_orders

WINDOWS = [("full history 2016-2026", "2016-01-01", "2027-01-01"),
           ("development 2016-2024", "2016-01-01", "2024-01-01"),
           ("out-of-sample 2024+", "2024-01-01", "2027-01-01")]


def edge_stats(r: np.ndarray) -> dict:
    """Win rate, payoff and the break-even that follows from them.

    ``r`` is per-trade R multiples net of costs, so the payoff already carries
    the commission drag that makes break-even higher than 50%.
    """
    wins, losses = r[r > 0], r[r <= 0]
    avg_w = float(wins.mean()) if len(wins) else 0.0
    avg_l = float(abs(losses.mean())) if len(losses) else 0.0
    return {
        "trades": len(r),
        "win_rate": len(wins) / len(r),
        "avg_win_r": avg_w,
        "avg_loss_r": avg_l,
        "payoff": avg_w / avg_l if avg_l else float("inf"),
        "expectancy_r": float(r.mean()),
        "breakeven_win_rate": avg_l / (avg_w + avg_l) if (avg_w + avg_l) else float("nan"),
        "sd_r": float(r.std()),
    }


def max_drawdown(r: np.ndarray) -> float:
    """Peak-to-valley, measured from the opening balance.

    The starting equity has to be in the running peak. Without it a series
    that opens with losses is measured from its own first low instead of from
    where the account actually started: [-1, -1] would report -1R when the
    account is really down 2R.
    """
    eq = np.concatenate([[0.0], np.cumsum(r)])
    return float((eq - np.maximum.accumulate(eq)).min())


def longest_losing_streak(r: np.ndarray) -> int:
    best = run = 0
    for x in r:
        run = run + 1 if x <= 0 else 0
        best = max(best, run)
    return best


def independence_check(r: np.ndarray, runs: int = 4000, seed: int = 5) -> dict:
    """Is a Monte Carlo entitled to assume these trades are independent?

    Reshuffling destroys any ordering. If the real sequence's drawdown and
    losing streaks look like the reshuffled ones, order carries no information
    and independence is a fair assumption.
    """
    rng = np.random.default_rng(seed)
    shuffled_dd = np.array([max_drawdown(rng.permutation(r)) for _ in range(runs)])
    shuffled_streak = np.mean([longest_losing_streak(rng.permutation(r))
                               for _ in range(min(runs, 300))])
    wins = (r > 0).astype(float)
    return {
        "autocorr_r": float(np.corrcoef(r[:-1], r[1:])[0, 1]),
        "autocorr_winloss": float(np.corrcoef(wins[:-1], wins[1:])[0, 1]),
        "streak": longest_losing_streak(r),
        "streak_shuffled": float(shuffled_streak),
        "dd": max_drawdown(r),
        "dd_shuffled_median": float(np.median(shuffled_dd)),
        "dd_percentile": float((shuffled_dd < max_drawdown(r)).mean() * 100),
    }


def returns(df1: pd.DataFrame, start: str, end: str, one_per_day: bool,
            risk: float) -> np.ndarray:
    bars = df1[(df1.ts >= start) & (df1.ts < end)].reset_index(drop=True)
    t = simulate(generate_orders(bars, LTFSweepConfig(), one_per_day=one_per_day),
                 bars, BacktestConfig(risk_per_trade_usd=risk))
    f = t[t["filled"]].sort_values("exit_ts")
    return (f["net_pnl"] / risk).to_numpy(), float(f["net_pnl"].sum())


def table(df1, one_per_day: bool, risk: float) -> dict:
    label = "one trade per day" if one_per_day else "unrestricted"
    print(f"\n{'=' * 100}\n{label.upper()}   (${risk:,.0f} risk per trade)\n{'=' * 100}")
    print(f"{'window':<24}{'trades':>8}{'win%':>8}{'avg win':>10}{'avg loss':>10}"
          f"{'payoff':>9}{'expect':>10}{'breakeven%':>12}{'net $':>13}")
    out = {}
    for name, a, b in WINDOWS:
        r, pnl = returns(df1, a, b, one_per_day, risk)
        s = edge_stats(r)
        out[name] = (s, r, pnl)
        print(f"{name:<24}{s['trades']:>8}{s['win_rate']:>7.1%}"
              f"{s['avg_win_r']:>+10.3f}{s['avg_loss_r']:>10.3f}{s['payoff']:>9.3f}"
              f"{s['expectancy_r']:>+10.4f}{s['breakeven_win_rate']:>11.1%}{pnl:>+13,.0f}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--risk", type=float, default=500.0,
                    help="dollar risk per trade (200 matches the prop-firm sizing)")
    ap.add_argument("--balance", type=float, default=100000.0,
                    help="starting balance to quote in the fill-in block")
    args = ap.parse_args(argv)

    print("loading 1-minute data ...")
    df1 = D.load("1m")
    capped = table(df1, True, args.risk)
    table(df1, False, args.risk)

    full = capped["full history 2016-2026"][0]
    oos, oos_r, _ = capped["out-of-sample 2024+"]

    print(f"\n{'=' * 100}\nWHAT TO TYPE IN\n{'=' * 100}")
    print("Run it twice. The gap between the two is the honest uncertainty, and")
    print("nothing in the data says which one is the future.\n")
    for name, s in (("A. full history  (pessimistic)", full),
                    ("B. out-of-sample (optimistic)", oos)):
        print(f"  {name}")
        print(f"      win rate            {s['win_rate']:.1%}")
        print(f"      reward:risk         {s['payoff']:.2f}   <-- NOT 1.0; commissions eat it")
        print(f"      number of trades    {s['trades']}")
        print(f"      risk per trade      ${args.risk:,.0f}")
        print(f"      starting balance    ${args.balance:,.0f}")
        print(f"      (break-even win rate at this payoff: {s['breakeven_win_rate']:.1%})\n")

    verdict = ("ABOVE" if full["win_rate"] > full["breakeven_win_rate"] else "BELOW")
    print(f"  Read this before believing either: over the full history the win rate")
    print(f"  ({full['win_rate']:.1%}) is {verdict} the break-even it needs "
          f"({full['breakeven_win_rate']:.1%}).")

    print(f"\n{'=' * 100}\nIS A MONTE CARLO ENTITLED TO ASSUME INDEPENDENCE?\n{'=' * 100}")
    ind = independence_check(oos_r)
    print(f"  lag-1 autocorrelation, R          {ind['autocorr_r']:+.4f}")
    print(f"  lag-1 autocorrelation, win/loss   {ind['autocorr_winloss']:+.4f}")
    print(f"  longest losing streak             {ind['streak']}  "
          f"(reshuffled average {ind['streak_shuffled']:.1f})")
    print(f"  max drawdown                      {ind['dd']:.2f}R  "
          f"(reshuffled median {ind['dd_shuffled_median']:.2f}R, "
          f"actual at the {ind['dd_percentile']:.0f}th percentile)")
    near_zero = abs(ind["autocorr_r"]) < 0.1 and abs(ind["autocorr_winloss"]) < 0.1
    print(f"\n  {'YES' if near_zero else 'NO'} -- "
          + ("ordering carries no information here, so an i.i.d. simulator is fair."
             if near_zero else
             "the trades are serially dependent; an i.i.d. simulator will understate "
             "drawdown."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
