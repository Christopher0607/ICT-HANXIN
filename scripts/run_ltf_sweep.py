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
    ap.add_argument("--window-months", type=int, default=None,
                    help="score only the last N months instead of the split")
    ap.add_argument("--rolling", action="store_true",
                    help="score every 3- and 6-month window across the history")
    ap.add_argument("--export", default=None,
                    help="write the full per-trade log as JSON for the report")
    args = ap.parse_args(argv)

    cfg = LTFSweepConfig(lookback_candles=args.lookback, target_r=args.target_r)
    print("loading 1-minute data ...")
    df1 = D.load("1m")

    if args.window_months or args.rolling or args.export:
        return _recent(df1, cfg, args)

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


def _recent(df1, cfg, args) -> int:
    """Recent-window scoring, rolling-window context, and the report export."""
    end = df1.ts.max()

    if args.window_months:
        start = (end - pd.DateOffset(months=args.window_months)).normalize()
        bars = df1[df1.ts >= start].reset_index(drop=True)
        print(f"last {args.window_months} months: {start.date()} -> {end.date()}"
              f"  ({bars.trading_date.nunique()} trading days)")
        for cap, label in ((True, "one trade per day"), (False, "unrestricted")):
            orders = generate_orders(bars, cfg, one_per_day=cap)
            res = summarise(label, orders, bars, args.risk)
            show(f"LAST {args.window_months} MONTHS  ·  {label}",
                 res["pessimistic"], res["optimistic"])

    if args.rolling or args.export:
        full = df1[df1.ts >= "2016-01-01"].reset_index(drop=True)
        orders = generate_orders(full, cfg, one_per_day=True)
        trades = simulate(orders, full,
                          BacktestConfig(risk_per_trade_usd=args.risk))

    if args.rolling:
        for months in (3, 6):
            windows = metrics.rolling_windows(trades, months=months)
            recent = windows.iloc[-1] if not windows.empty else None
            print(f"\n{'=' * 76}\nALL {months}-MONTH WINDOWS  ·  one trade per day"
                  f"\n{'=' * 76}")
            print(f"  windows scored       {len(windows)}")
            print(f"  profit factor        median {windows.profit_factor.median():.3f}"
                  f"   90th pct {windows.profit_factor.quantile(0.9):.3f}")
            print(f"  share above 1.0      "
                  f"{(windows.profit_factor > 1).mean():.1%}")
            if recent is not None:
                pct = metrics.window_percentile(windows, recent.profit_factor)
                print(f"  most recent window   PF {recent.profit_factor:.3f}"
                      f"  -> {pct:.0f}th percentile"
                      f"  ({recent.start.date()} -> {recent.end.date()})")

    if args.export:
        _export(trades, df1, cfg, args)
    return 0


def _export(trades, df1, cfg, args) -> None:
    """Write the per-trade log and rolling-window context for the report page."""
    import json

    filled = trades[trades["filled"]].copy()
    filled = filled.sort_values("exit_ts").reset_index(drop=True)
    filled["equity"] = filled["net_pnl"].cumsum()

    def rows(frame):
        out = []
        for r in frame.itertuples():
            out.append({
                "d": pd.Timestamp(r.exit_ts).tz_convert("America/New_York")
                       .strftime("%Y-%m-%d %H:%M"),
                "ep": round(float(r.entry_ts.tz_convert("America/New_York").hour * 60
                                  + r.entry_ts.tz_convert("America/New_York").minute), 0),
                "dir": int(r.direction), "eq": round(float(r.equity), 2),
                "en": round(float(r.entry_fill), 2), "st": round(float(r.stop_price), 2),
                "tg": round(float(r.target_price), 2), "ex": round(float(r.exit_price), 2),
                "rr": round(float(r.r_multiple), 3), "pnl": round(float(r.net_pnl), 2),
                "n": int(r.contracts), "why": str(r.exit_reason),
                "pool": round(float(r.pool_level), 2),
                "swx": round(float(r.sweep_extreme), 2),
                "ft": round(float(r.fvg_top), 2), "fb": round(float(r.fvg_bottom), 2),
                "amb": bool(r.ambiguous), "rp": round(float(r.risk_points), 2),
            })
        return out

    payload = {"trades": rows(filled),
               "start": str(filled.exit_ts.min()), "end": str(filled.exit_ts.max())}
    for months in (3, 6):
        w = metrics.rolling_windows(trades, months=months)
        payload[f"windows_{months}m"] = {
            "pf": [None if pd.isna(v) or v == float("inf") else round(float(v), 3)
                   for v in w.profit_factor],
            "start": [str(t.date()) for t in w.start],
            "trades": [int(v) for v in w.trades],
            "recent_pf": float(w.profit_factor.iloc[-1]) if len(w) else None,
            "percentile": metrics.window_percentile(w, w.profit_factor.iloc[-1])
                          if len(w) else None,
            "share_above_1": float((w.profit_factor > 1).mean()) if len(w) else None,
        }
    with open(args.export, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"), default=str)
    print(f"exported {len(filled)} trades to {args.export}")


if __name__ == "__main__":
    sys.exit(main())
