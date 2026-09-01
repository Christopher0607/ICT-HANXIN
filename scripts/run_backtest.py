"""Run the PO3 / Judas Swing backtest.

    uv run python scripts/run_backtest.py                     # full report
    uv run python scripts/run_backtest.py --start 2024-09-01  # last two years
    uv run python scripts/run_backtest.py --split             # dev vs out-of-sample

The ``--split`` mode is the one that matters.  Development runs on 2010-2023;
2024-2026 is held back.  Any parameter chosen after looking at the out-of-sample
result stops being out-of-sample, so it is reported once and left alone.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from backtest import controls, metrics
from backtest.engine import BacktestConfig, simulate
from ict import data as D
from strategies.po3_judas import PO3Config, generate_orders

#: Development window. It starts in 2016 rather than 2010 because the Asian
#: session is only 34-45% populated before then (see sessions.session_coverage),
#: which makes the accumulation range — the input the whole model rests on —
#: not comparable to a modern one.
DEV_START = "2016-01-01"
DEV_END = "2024-01-01"
OOS_START = "2024-01-01"


def run(df5m, df1m, strategy_config, exec_config, label=""):
    orders = generate_orders(df5m, strategy_config)
    if orders.empty:
        print(f"{label}: no signals")
        return None, None
    trades = simulate(orders, df1m, exec_config)
    return trades, metrics.summarize(trades)


def print_summary(name: str, s: dict) -> None:
    if not s or s.get("trades", 0) == 0:
        print(f"\n{name}: no trades")
        return
    print(f"\n{'=' * 62}\n{name}\n{'=' * 62}")
    print(f"  signals / filled     {s['signals']} / {s['trades']}  "
          f"(fill rate {s['fill_rate']:.1%})")
    print(f"  win rate             {s['win_rate']:.1%}")
    print(f"  average R            {s['avg_r']:+.3f}   median {s['median_r']:+.3f}")
    print(f"  expectancy / trade   ${s['expectancy_usd']:+,.2f}")
    print(f"  total net P&L        ${s['total_pnl']:+,.2f}")
    print(f"  profit factor        {s['profit_factor']:.3f}")
    print(f"  max drawdown         ${s['max_drawdown_usd']:,.2f}  "
          f"({s['max_drawdown_r']:.2f} R)")
    print(f"  avg win / avg loss   ${s['avg_win_usd']:,.2f} / ${s['avg_loss_usd']:,.2f}")
    print(f"  per-trade Sharpe     {s['sharpe_per_trade']:.3f}")
    print(f"  ambiguous exits      {s['ambiguous_trades']} ({s['ambiguous_pct']:.1%})")
    print(f"  exit reasons         {s['exit_reasons']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default=None, help="ISO date to start from")
    ap.add_argument("--end", default=None, help="ISO date to stop at")
    ap.add_argument("--split", action="store_true",
                    help="report development and out-of-sample periods separately")
    ap.add_argument("--target-mode", default="opposite_range",
                    choices=["opposite_range", "fixed_r"])
    ap.add_argument("--target-r", type=float, default=2.0)
    ap.add_argument("--entry-level", default="midpoint",
                    choices=["midpoint", "far", "near"])
    ap.add_argument("--ambiguity", default="pessimistic",
                    choices=["pessimistic", "optimistic"])
    ap.add_argument("--by-year", action="store_true", help="per-year breakdown")
    ap.add_argument("--monte-carlo", action="store_true",
                    help="drawdown distribution from reshuffled trade order")
    ap.add_argument("--control", action="store_true",
                    help="compare against randomised trade direction on the same setups")
    ap.add_argument("--csv", default=None, help="write the trade log to this path")
    args = ap.parse_args(argv)

    strategy_config = PO3Config(
        target_mode=args.target_mode, target_r=args.target_r,
        entry_level=args.entry_level,
    )
    exec_config = BacktestConfig(ambiguity=args.ambiguity)

    print("loading data ...")
    df5m = D.load("5m")
    df1m = D.load("1m", with_time_columns=False)

    if args.split:
        dev = df5m[(df5m.ts >= DEV_START) & (df5m.ts < DEV_END)].reset_index(drop=True)
        oos = df5m[df5m.ts >= OOS_START].reset_index(drop=True)
        dev_trades, dev_stats = run(dev, df1m, strategy_config, exec_config)
        oos_trades, oos_stats = run(oos, df1m, strategy_config, exec_config)
        print_summary(f"DEVELOPMENT  {DEV_START} -> {DEV_END}", dev_stats)
        print_summary(f"OUT-OF-SAMPLE  {OOS_START} -> 2026-08", oos_stats)
        trades = pd.concat([dev_trades, oos_trades], ignore_index=True)
    else:
        sub = df5m
        if args.start:
            sub = sub[sub.ts >= args.start]
        if args.end:
            sub = sub[sub.ts < args.end]
        sub = sub.reset_index(drop=True)
        window = f"{args.start or '2010-06'} -> {args.end or '2026-08'}"
        trades, stats = run(sub, df1m, strategy_config, exec_config)
        print_summary(f"PO3 / JUDAS SWING   {window}", stats)

    if trades is None:
        return 1

    if args.control:
        orders = generate_orders(
            df5m[df5m.ts >= (args.start or DEV_START)].reset_index(drop=True),
            strategy_config,
        )
        ctrl = controls.random_direction_control(orders, df1m, exec_config, runs=200)
        if ctrl:
            stats = metrics.summarize(simulate(orders, df1m, exec_config))
            pct = controls.percentile_of(stats["total_pnl"], ctrl)
            print(f"\ncontrol: same setups, randomised direction ({ctrl['runs']} runs)")
            print(f"  strategy total P&L   ${stats['total_pnl']:+,.2f}  "
                  f"(win rate {stats['win_rate']:.1%})")
            print(f"  random median        ${ctrl['median_total_pnl']:+,.2f}  "
                  f"[5th ${ctrl['p05_total_pnl']:+,.2f}, "
                  f"95th ${ctrl['p95_total_pnl']:+,.2f}]")
            print(f"  random win rate      {ctrl['median_win_rate']:.1%}")
            print(f"  strategy percentile  ~{pct:.0f}th")

    if args.by_year:
        print("\nper-year breakdown")
        print(metrics.by_period(trades, "YE").to_string(index=False))

    if args.monte_carlo:
        mc = metrics.monte_carlo(trades)
        if mc:
            print(f"\nmonte carlo ({mc['runs']} reshuffles)")
            print(f"  median max drawdown  ${mc['median_max_dd']:,.2f}")
            print(f"  5th pct max drawdown ${mc['p05_max_dd']:,.2f}")
            print(f"  probability of loss  {mc['prob_negative']:.1%}")

    if args.csv:
        trades.to_csv(args.csv, index=False)
        print(f"\nwrote trade log to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
