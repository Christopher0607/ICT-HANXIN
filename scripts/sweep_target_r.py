"""Sweep the reward-to-risk target on the LTF sweep model.

    uv run python scripts/sweep_target_r.py

This is a parameter search, and the point of the output is to make that
visible rather than to crown a winner. Three things guard against reading
noise as a discovery:

* **The whole curve is reported, not the best point.** A broad, smooth plateau
  is evidence of a real effect; a single spike surrounded by worse values is
  what an overfit optimum looks like.
* **The break-even win rate is computed from the realised wins and losses** and
  printed beside the actual win rate. Raising the target lowers the bar you
  must clear and lowers the rate at which you clear it; the curve is only
  informative because those two move at different speeds.
* **The exit mix is printed.** A distant target is often not reached before the
  16:00 flat, so the trade ends at the close instead. Past some multiple the
  label "1:3" stops describing what actually happens.

The sweep is a controlled experiment: orders are generated once per period and
re-targeted (see strategies.base.retarget), so every column below describes the
same trades with the profit taken in a different place.
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from backtest import metrics
from backtest.engine import BacktestConfig, simulate
from ict import data as D
from strategies.base import retarget
from strategies.ltf_sweep import LTFSweepConfig, generate_orders

R_VALUES = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
HEADLINE = [1.0, 1.5, 2.0]

PERIODS = {
    "development": ("2016-01-01", "2024-01-01"),
    "out_of_sample": ("2024-01-01", "2027-01-01"),
}


def breakeven_win_rate(trades: pd.DataFrame) -> float:
    """Win rate this trade set needed to break even, from its realised sizes.

    Uses the actual average win and average loss in dollars, so commission,
    slippage and time exits are all already inside the number.
    """
    filled = trades[trades["filled"]]
    wins = filled[filled["net_pnl"] > 0]["net_pnl"]
    losses = filled[filled["net_pnl"] < 0]["net_pnl"]
    if wins.empty or losses.empty:
        return float("nan")
    aw, al = wins.mean(), -losses.mean()
    return float(al / (aw + al))


def exit_mix(stats: dict) -> str:
    reasons = stats.get("exit_reasons", {})
    total = sum(reasons.values()) or 1
    parts = []
    for key, label in (("target", "利"), ("stop", "損"), ("time_exit", "時")):
        n = reasons.get(key, 0) + reasons.get(key + "_ambiguous", 0)
        parts.append(f"{label}{n/total:.0%}")
    return " ".join(parts)


def sweep(orders, bars, cfg, r_values):
    rows = []
    for r in r_values:
        trades = simulate(retarget(orders, r), bars, cfg)
        s = metrics.summarize(trades)
        if not s.get("trades"):
            continue
        be = breakeven_win_rate(trades)
        rows.append({
            "r": r, "trades": s["trades"], "win_rate": s["win_rate"],
            "breakeven": be, "edge": s["win_rate"] - be,
            "avg_r": s["avg_r"], "profit_factor": s["profit_factor"],
            "total_pnl": s["total_pnl"], "max_dd": s["max_drawdown_usd"],
            "exits": exit_mix(s), "_trades": trades,
        })
    return rows


def show(title, rows):
    print(f"\n{'=' * 96}\n{title}\n{'=' * 96}")
    print(f"{'R:R':>6}{'筆數':>8}{'勝率':>9}{'打平需要':>11}{'超出':>8}"
          f"{'平均R':>9}{'PF':>8}{'淨損益':>13}{'最大回撤':>12}   出場組成")
    print("-" * 96)
    best = max(rows, key=lambda x: x["profit_factor"]) if rows else None
    for row in rows:
        mark = " <" if row is best else ""
        print(f"{'1:'+format(row['r'],'g'):>6}{row['trades']:>8}"
              f"{row['win_rate']:>8.1%}{row['breakeven']:>11.1%}"
              f"{row['edge']:>+8.1%}{row['avg_r']:>+9.3f}"
              f"{row['profit_factor']:>8.3f}{row['total_pnl']:>+13,.0f}"
              f"{row['max_dd']:>+12,.0f}   {row['exits']}{mark}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=500.0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    cfg = LTFSweepConfig()
    exec_cfg = BacktestConfig(risk_per_trade_usd=args.risk)
    print("loading 1-minute data ...")
    df1 = D.load("1m")

    payload, roll_src = {}, {}
    for period, (start, end) in PERIODS.items():
        bars = df1[(df1.ts >= start) & (df1.ts < end)].reset_index(drop=True)
        for cap, label in ((True, "一天一單"), (False, "不限筆數")):
            print(f"generating {period} · {label} ...")
            orders = generate_orders(bars, cfg, one_per_day=cap)
            rows = sweep(orders, bars, exec_cfg, R_VALUES)
            show(f"{period.upper().replace('_','-')}  ·  {label}  ·  {start} → {end}", rows)
            key = f"{period}_{'capped' if cap else 'uncapped'}"
            payload[key] = [{k: v for k, v in r.items() if k != "_trades"} for r in rows]
            if cap:
                roll_src[period] = {r["r"]: r["_trades"] for r in rows}

    # Recent windows, capped only — the variant that survives the fill test.
    end_ts = df1.ts.max()
    for months in (6, 3):
        start = (end_ts - pd.DateOffset(months=months)).normalize()
        bars = df1[df1.ts >= start].reset_index(drop=True)
        orders = generate_orders(bars, cfg, one_per_day=True)
        rows = sweep(orders, bars, exec_cfg, HEADLINE)
        show(f"近 {months} 個月  ·  一天一單  ·  {start.date()} → {end_ts.date()}", rows)
        payload[f"last_{months}m_capped"] = [
            {k: v for k, v in r.items() if k != "_trades"} for r in rows]

    # Rolling-window context at the headline multiples.
    print(f"\n{'=' * 96}\n滾動視窗檢定 · 一天一單 · 全歷史\n{'=' * 96}")
    print(f"{'R:R':>6}{'3個月視窗中位PF':>18}{'超過1.0':>10}{'當前視窗':>11}{'百分位':>9}")
    print("-" * 96)
    payload["rolling"] = {}
    full = df1[df1.ts >= "2016-01-01"].reset_index(drop=True)
    full_orders = generate_orders(full, cfg, one_per_day=True)
    for r in HEADLINE:
        trades = simulate(retarget(full_orders, r), full, exec_cfg)
        w = metrics.rolling_windows(trades, months=3)
        if w.empty:
            continue
        cur = float(w.profit_factor.iloc[-1])
        pct = metrics.window_percentile(w, cur)
        share = float((w.profit_factor > 1).mean())
        print(f"{'1:'+format(r,'g'):>6}{w.profit_factor.median():>18.3f}"
              f"{share:>10.0%}{cur:>11.3f}{pct:>8.0f}位")
        payload["rolling"][str(r)] = {
            "median_pf": float(w.profit_factor.median()), "share_above_1": share,
            "current_pf": cur, "percentile": pct,
            "pf": [None if pd.isna(v) or v == float("inf") else round(float(v), 3)
                   for v in w.profit_factor],
        }

    # Fill-assumption sensitivity at the headline multiples, out-of-sample.
    print(f"\n{'=' * 96}\n成交假設敏感度 · 樣本外 · 一天一單\n{'=' * 96}")
    oos = df1[df1.ts >= "2024-01-01"].reset_index(drop=True)
    oos_orders = generate_orders(oos, cfg, one_per_day=True)
    payload["fill_sensitivity"] = {}
    for r in HEADLINE:
        line = f"  1:{r:g}   "
        entry = {}
        for mode in ("touch", "through"):
            s = metrics.summarize(simulate(retarget(oos_orders, r), oos,
                BacktestConfig(risk_per_trade_usd=args.risk, entry_fill_mode=mode)))
            line += f"{mode:8} PF {s['profit_factor']:.3f} ${s['total_pnl']:>+9,.0f}   "
            entry[mode] = {"pf": s["profit_factor"], "pnl": s["total_pnl"]}
        print(line)
        payload["fill_sensitivity"][str(r)] = entry

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(payload, fh, indent=2, default=str)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
