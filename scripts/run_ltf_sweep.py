"""Backtest the low-timeframe sweep model (15m pools, 1m CHoCH, 1m FVG, 1:1).

    uv run python scripts/run_ltf_sweep.py

Reports the same development / out-of-sample split as the other strategies, at
the same $500 risk per trade, so the numbers are directly comparable.

Both ambiguity policies are always reported. Signals and fills are both on
1-minute bars here, so a bar containing the stop and the target cannot be
resolved from the data at any price; the two policies bracket the truth.
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from backtest import metrics
from backtest.engine import BacktestConfig, simulate
from ict import data as D
from strategies.ltf_sweep import LTFSweepConfig, generate_orders

PERIODS = {"development": ("2016-01-01", "2024-01-01"),
           "out_of_sample": ("2024-01-01", "2027-01-01")}


def summarise(name, orders, bars, risk):
    line = {}
    for policy in ("pessimistic", "optimistic"):
        t = simulate(orders, bars, BacktestConfig(risk_per_trade_usd=risk,
                                                  ambiguity=policy))
        line[policy] = metrics.summarize(t)
        line[policy + "_trades"] = t
    return line


def show(title, s_pess, s_opt):
    print(f"\n{'=' * 76}\n{title}\n{'=' * 76}")
    if not s_pess.get("trades"):
        print("  no trades")
        return
    print(f"  signals / filled     {s_pess['signals']} / {s_pess['trades']}"
          f"  (fill rate {s_pess['fill_rate']:.1%})")
    print(f"  win rate             {s_pess['win_rate']:.1%}"
          f"   .. {s_opt['win_rate']:.1%} optimistic")
    print(f"  average R            {s_pess['avg_r']:+.3f}"
          f"   .. {s_opt['avg_r']:+.3f}")
    print(f"  profit factor        {s_pess['profit_factor']:.3f}"
          f"   .. {s_opt['profit_factor']:.3f}")
    print(f"  net P&L              ${s_pess['total_pnl']:+,.0f}"
          f"   .. ${s_opt['total_pnl']:+,.0f}")
    print(f"  max drawdown         ${s_pess['max_drawdown_usd']:,.0f}")
    print(f"  UNRESOLVABLE bars    {s_pess['ambiguous_trades']}"
          f" ({s_pess['ambiguous_pct']:.1%} of trades)")
    print(f"  exit reasons         {s_pess['exit_reasons']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=500.0)
    ap.add_argument("--lookback", type=int, default=4)
    ap.add_argument("--target-r", type=float, default=1.0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    cfg = LTFSweepConfig(lookback_candles=args.lookback, target_r=args.target_r)
    print("loading 1-minute data ...")
    df1 = D.load("1m")

    payload = {}
    for period, (start, end) in PERIODS.items():
        bars = df1[(df1.ts >= start) & (df1.ts < end)].reset_index(drop=True)
        print(f"generating {period} ({start} -> {end}, {len(bars):,} bars) ...")
        for cap, label in ((True, "one trade per day"), (False, "unrestricted")):
            orders = generate_orders(bars, cfg, one_per_day=cap)
            res = summarise(period, orders, bars, args.risk)
            show(f"{period.upper().replace('_','-')}  ·  {label}"
                 f"  ·  {start} -> {end}", res["pessimistic"], res["optimistic"])
            payload[f"{period}_{'capped' if cap else 'uncapped'}"] = {
                k: (None if isinstance(v, float) and pd.isna(v) else v)
                for k, v in res["pessimistic"].items() if k != "exit_reasons"
            } | {"optimistic_pnl": res["optimistic"]["total_pnl"],
                 "optimistic_pf": res["optimistic"]["profit_factor"],
                 "exit_reasons": res["pessimistic"]["exit_reasons"]}

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(payload, fh, indent=2, default=str)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
