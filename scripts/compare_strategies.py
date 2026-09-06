"""Run every pre-registered strategy over the same periods and report all of them.

    uv run python scripts/compare_strategies.py --split
    uv run python scripts/compare_strategies.py --split --json results.json

Development is 2016-2024 (the years where the Asian session is fully populated;
see ict.sessions.session_coverage).  Out-of-sample is 2024-2026 and is run once,
with every parameter already frozen in strategies/registry.py.

All seven strategies are reported, including the losers.  Reporting only the
winner would hide the selection bias that the --null test exists to measure.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from backtest import controls, metrics, portfolio
from backtest.engine import BacktestConfig, simulate
from ict import data as D
from strategies import registry

DEV_START, DEV_END = "2016-01-01", "2024-01-01"
OOS_START, OOS_END = "2024-01-01", "2027-01-01"

PERIODS = {
    "development": (DEV_START, DEV_END),
    "out_of_sample": (OOS_START, OOS_END),
}


def slice_bars(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    return df[(df.ts >= start) & (df.ts < end)].reset_index(drop=True)


def run_period(df5m, df1m, start, end, exec_config, null_runs=0):
    """Run all strategies plus the combined account over one period."""
    signals = slice_bars(df5m, start, end)
    # Slice the 1-minute bars too: simulate() searchsorts into this frame for
    # every order, and the full 4.8M-row series makes that needlessly slow.
    fills = slice_bars(df1m, start, end)

    orders_by_strategy, results, null_totals = {}, {}, {}
    for name in registry.NAMES:
        orders = registry.generate(name, signals)
        orders_by_strategy[name] = orders
        trades = simulate(orders, fills, exec_config)
        results[name] = {
            "stats": metrics.summarize(trades),
            "equity": metrics.equity_curve(trades),
            "trades": trades,
        }
        if null_runs:
            null_totals[name] = controls.randomized_totals(
                orders, fills, exec_config, runs=null_runs
            )

    combined_orders = portfolio.combine(orders_by_strategy)
    combined_trades = simulate(combined_orders, fills, exec_config)
    results["_portfolio"] = {
        "stats": metrics.summarize(combined_trades),
        "equity": metrics.equity_curve(combined_trades),
        "trades": combined_trades,
    }

    return {
        "results": results,
        "overlap": portfolio.overlap_matrix(orders_by_strategy),
        "contribution": portfolio.contribution(combined_trades),
        "null_totals": null_totals,
    }


def print_table(title: str, results: dict) -> None:
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")
    header = (f"{'strategy':<18}{'sig':>5}{'fill':>6}{'fill%':>7}{'win%':>7}"
              f"{'avgR':>8}{'PF':>7}{'net $':>12}{'maxDD $':>11}{'amb':>5}")
    print(header)
    print("-" * len(header))
    for name, payload in results.items():
        s = payload["stats"]
        if not s.get("trades"):
            print(f"{name:<18}{s.get('signals', 0):>5}{0:>6}{'-':>7}{'-':>7}"
                  f"{'-':>8}{'-':>7}{'-':>12}{'-':>11}{'-':>5}")
            continue
        label = "PORTFOLIO" if name == "_portfolio" else name
        print(f"{label:<18}{s['signals']:>5}{s['trades']:>6}{s['fill_rate']:>6.0%}"
              f"{s['win_rate']:>7.1%}{s['avg_r']:>8.3f}{s['profit_factor']:>7.2f}"
              f"{s['total_pnl']:>12,.0f}{s['max_drawdown_usd']:>11,.0f}"
              f"{s['ambiguous_trades']:>5}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", action="store_true", default=True)
    ap.add_argument("--null-runs", type=int, default=0,
                    help="direction-randomised runs per strategy for the best-of-N test")
    ap.add_argument("--risk", type=float, default=500.0, help="risk per trade in USD")
    ap.add_argument("--json", default=None, help="write full results here")
    args = ap.parse_args(argv)

    exec_config = BacktestConfig(risk_per_trade_usd=args.risk)

    print("loading data ...")
    df5m = D.load("5m")
    df1m = D.load("1m", with_time_columns=False)

    payload = {"risk_per_trade_usd": args.risk, "periods": {}}
    for period, (start, end) in PERIODS.items():
        print(f"running {period} ({start} -> {end}) ...")
        out = run_period(df5m, df1m, start, end, exec_config, args.null_runs)
        print_table(f"{period.upper().replace('_', '-')}   {start} -> {end}", out["results"])

        if args.null_runs:
            actual = {n: out["results"][n]["stats"].get("total_pnl", 0.0)
                      for n in registry.NAMES
                      if out["results"][n]["stats"].get("trades")}
            null = controls.best_of_n_null(out["null_totals"], actual)
            if null:
                print(f"\n  best-of-{null['n_strategies']} null test "
                      f"({null['rounds']} rounds, direction randomised)")
                print(f"    apparent winner        {null['best_strategy']} "
                      f"at ${null['best_actual_pnl']:+,.0f}")
                print(f"    a single no-edge model {null['null_single_median']:+,.0f} (median)")
                print(f"    best of {null['n_strategies']} no-edge models  "
                      f"${null['null_best_median']:+,.0f} (median), "
                      f"${null['null_best_p95']:+,.0f} (95th pct)")
                print(f"    p-value                {null['p_value']:.3f}  "
                      f"-> {'SURVIVES' if null['survives'] else 'INDISTINGUISHABLE FROM NOISE'}")
                out["null_test"] = null

        payload["periods"][period] = _serialize(out, start, end)

    print("\nsignal-day overlap (out-of-sample, fraction of active days shared)")
    print(payload["periods"]["out_of_sample"]["overlap_preview"])

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(payload, fh, indent=2, default=str)
        print(f"\nwrote {args.json}")
    return 0


def _serialize(out: dict, start: str, end: str) -> dict:
    """Reduce a period's results to JSON-friendly structures for the report."""
    serialized = {"start": start, "end": end, "strategies": {}}
    for name, payload in out["results"].items():
        equity = payload["equity"]
        serialized["strategies"][name] = {
            "stats": {k: (None if isinstance(v, float) and np.isnan(v) else v)
                      for k, v in payload["stats"].items()},
            "equity": {
                "ts": [str(t) for t in equity["exit_ts"]],
                "value": [float(v) for v in equity["equity"]],
            } if not equity.empty else {"ts": [], "value": []},
        }
    if "null_test" in out:
        serialized["null_test"] = out["null_test"]
    overlap = out["overlap"]
    serialized["overlap"] = overlap.to_dict() if not overlap.empty else {}
    serialized["overlap_preview"] = overlap.round(2).to_string() if not overlap.empty else ""
    serialized["contribution"] = (
        out["contribution"].to_dict("records") if not out["contribution"].empty else []
    )
    return serialized


if __name__ == "__main__":
    sys.exit(main())
