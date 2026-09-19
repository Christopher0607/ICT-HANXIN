"""Search the opening-range family for a high win rate that actually pays.

    uv run python scripts/search_orb.py

This is a SEARCH, and it is labelled one everywhere it prints. The seven
strategies in ``registry.py`` were specified before their results existed;
``or5_retest`` at least had its rules committed before its first run. This has
neither protection: it looks at eleven years of data and asks which of several
hundred rule combinations would have worked, which is the mechanism of
overfitting rather than an accident of it.

Three things keep it honest, and all three are printed:

1. **Selection happens on development data only.** Out-of-sample is scored for
   every combination but never used to choose one.
2. **The count of combinations tried is reported**, because "the best of 900"
   is a different claim from "this worked".
3. **The correlation between development and out-of-sample rank is reported.**
   If a search has found something real, combinations that did well in one
   period do well in the other. Near zero means the ranking is noise and the
   winner is the luckiest draw, not the best idea.

The question being tested is whether a win rate high enough to carry a payoff
below 1:1 exists here. Expectancy is what decides that, not the win rate: at a
0.5R target you need better than 2 wins per loss just to break even, and at
0.25R better than 4.
"""

from __future__ import annotations

import argparse
import itertools

import numpy as np
import pandas as pd

from backtest import metrics
from backtest.engine import BacktestConfig, simulate
from ict import data as D
from strategies.orb_family import DetectConfig, PriceConfig, detect, price

DEV = ("2016-01-01", "2024-01-01")
OOS = ("2024-01-01", "2027-01-01")

#: The grid. Wide enough to cover the family, small enough that the
#: multiple-comparisons problem stays reportable rather than hopeless.
GRID = {
    "entry_kind": ("break_retest", "break_first", "fail_fade"),
    "side_rule": ("body", "either"),
    "stop": (("pullback", 3.0), ("fixed", 15.0), ("fixed", 25.0),
             ("height", 0.5), ("height", 1.0)),
    "min_body_frac": (0.0, 0.4),
    "exit_minute": (10 * 60 + 45, 12 * 60, 16 * 60),
}
#: Varied without re-pricing: only ``target_price`` moves.
TARGETS = (0.25, 0.4, 0.5, 0.75, 1.0)

EXEC = BacktestConfig(risk_per_trade_usd=500.0, entry_side="stop",
                      entry_slippage_ticks=1.0)


def score(orders: pd.DataFrame, bars: pd.DataFrame, lo: str, hi: str) -> dict:
    lo, hi = pd.Timestamp(lo, tz="UTC"), pd.Timestamp(hi, tz="UTC")
    window = orders[(orders["valid_from"] >= lo) & (orders["valid_from"] < hi)]
    if len(window) < 30:
        return {"trades": len(window), "win_rate": np.nan, "avg_r": np.nan,
                "profit_factor": np.nan, "total_pnl": np.nan}
    m = metrics.summarize(simulate(window, bars, EXEC))
    return {"trades": m["trades"], "win_rate": m["win_rate"],
            "avg_r": m["avg_r"], "profit_factor": m["profit_factor"],
            "total_pnl": m["total_pnl"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args(argv)

    bars = D.add_time_columns(pd.read_parquet(
        D.PROCESSED_DIR / "nq_1m.parquet",
        filters=[("ts", ">=", pd.Timestamp("2015-12-01", tz="UTC"))])
    ).reset_index(drop=True)
    setups = detect(bars, DetectConfig())
    print(f"{len(setups):,} sessions with an opening range\n")

    keys = ("entry_kind", "side_rule", "stop", "min_body_frac", "exit_minute")
    rows = []
    for combo in itertools.product(*(GRID[k] for k in keys)):
        params = dict(zip(keys, combo))
        stop_kind, stop_value = params.pop("stop")
        cfg = PriceConfig(stop_kind=stop_kind, stop_value=stop_value, **params)
        orders = price(setups, bars, cfg)
        if orders.empty:
            continue
        for target_r in TARGETS:
            moved = orders.copy()
            moved["target_price"] = (moved["entry_price"] + moved["direction"]
                                     * target_r * moved["risk_points"])
            dev = score(moved, bars, *DEV)
            oos = score(moved, bars, *OOS)
            rows.append({"entry": params["entry_kind"], "side": params["side_rule"],
                         "stop": f"{stop_kind}/{stop_value:g}",
                         "body": params["min_body_frac"],
                         "exit": params["exit_minute"], "target_r": target_r,
                         **{f"dev_{k}": v for k, v in dev.items()},
                         **{f"oos_{k}": v for k, v in oos.items()}})

    res = pd.DataFrame(rows).dropna(subset=["dev_profit_factor"])
    print(f"{len(res)} combinations scored "
          f"({len(res[res.dev_profit_factor > 1.0])} profitable in development)\n")

    print("development profit factor, across every combination tried:")
    q = res["dev_profit_factor"].quantile([0.5, 0.9, 0.99, 1.0])
    for label, value in zip(("median", "90th", "99th", "best"), q):
        print(f"  {label:>7} {value:.2f}")

    both = res.dropna(subset=["oos_profit_factor"])
    # Spearman is Pearson on the ranks, and this project has no scipy.
    rho = both["dev_profit_factor"].rank().corr(both["oos_profit_factor"].rank())
    print(f"\ndev-to-oos rank correlation: {rho:+.3f}"
          f"   ({len(both)} combinations scored in both)")
    print("  near zero means the ranking is noise and the winner is the"
          " luckiest draw")

    best = res.sort_values("dev_profit_factor", ascending=False).head(args.top)
    print(f"\ntop {args.top} by DEVELOPMENT profit factor, with what they then"
          f" did out of sample:")
    print(f"  {'entry':<13}{'side':<7}{'stop':<12}{'body':>5}{'exit':>6}"
          f"{'tgt':>5}{'dev W':>7}{'dev PF':>8}{'oos W':>7}{'oos PF':>8}"
          f"{'oos P&L':>10}")
    for r in best.itertuples():
        print(f"  {r.entry:<13}{r.side:<7}{r.stop:<12}{r.body:>5.1f}{r.exit:>6}"
              f"{r.target_r:>5.2f}{r.dev_win_rate:>7.1%}{r.dev_profit_factor:>8.2f}"
              f"{r.oos_win_rate:>7.1%}{r.oos_profit_factor:>8.2f}"
              f"{r.oos_total_pnl:>10,.0f}")

    winners = both[(both.dev_profit_factor > 1.0) & (both.oos_profit_factor > 1.0)]
    print(f"\nprofitable in BOTH periods: {len(winners)} of {len(both)}"
          f"  ({len(winners) / len(both):.1%})")
    if len(winners):
        print(f"  {'entry':<13}{'side':<7}{'stop':<12}{'tgt':>5}"
              f"{'dev W':>7}{'dev PF':>8}{'oos W':>7}{'oos PF':>8}{'oos R':>8}")
        for r in winners.sort_values("oos_profit_factor", ascending=False).head(12).itertuples():
            print(f"  {r.entry:<13}{r.side:<7}{r.stop:<12}{r.target_r:>5.2f}"
                  f"{r.dev_win_rate:>7.1%}{r.dev_profit_factor:>8.2f}"
                  f"{r.oos_win_rate:>7.1%}{r.oos_profit_factor:>8.2f}"
                  f"{r.oos_avg_r:>8.3f}")
    # Is the winner a lucky cell, or is its whole family better? A single
    # profitable combination among 900 says little; a family whose members are
    # mostly profitable in both periods is a different kind of claim.
    print("\nby entry rule -- the share of each family profitable in BOTH periods:")
    print(f"  {'entry':<14}{'n':>5}{'dev PF':>9}{'oos PF':>9}{'both>1':>9}")
    for kind, g in both.groupby("entry"):
        win = ((g.dev_profit_factor > 1) & (g.oos_profit_factor > 1)).mean()
        print(f"  {kind:<14}{len(g):>5}{g.dev_profit_factor.median():>9.2f}"
              f"{g.oos_profit_factor.median():>9.2f}{win:>9.1%}")

    # The question that was actually asked: does a payoff below 1:1 pay?
    fade = both[both.entry == "fail_fade"]
    if len(fade):
        print("\nfail_fade only -- what the payoff ratio buys and costs:")
        print(f"  {'target':>7}{'dev W':>8}{'oos W':>8}{'dev PF':>8}{'oos PF':>8}"
              f"{'oos avg R':>11}{'both>1':>9}")
        for t, g in fade.groupby("target_r"):
            win = ((g.dev_profit_factor > 1) & (g.oos_profit_factor > 1)).mean()
            print(f"  {t:>7.2f}{g.dev_win_rate.median():>8.1%}"
                  f"{g.oos_win_rate.median():>8.1%}"
                  f"{g.dev_profit_factor.median():>8.2f}"
                  f"{g.oos_profit_factor.median():>8.2f}"
                  f"{g.oos_avg_r.median():>11.3f}{win:>9.1%}")

    oos_rate = (both.oos_profit_factor > 1).mean()
    dev_rate = (both.dev_profit_factor > 1).mean()
    print(f"\nprofitable in development {dev_rate:.1%}, out of sample"
          f" {oos_rate:.1%}; if the two were independent the joint rate would"
          f" be {dev_rate * oos_rate:.1%}, and it is"
          f" {len(winners) / len(both):.1%}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
