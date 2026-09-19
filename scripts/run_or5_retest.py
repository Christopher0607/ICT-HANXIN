"""Backtest the opening-range break-and-retest model.

    uv run python scripts/run_or5_retest.py

Rules and every disambiguation are in ``docs/or5_retest_spec.md``, committed
before this script first ran. Reports the same development / out-of-sample
split as the other strategies so the numbers are comparable, and reports both
ambiguity policies because signals and fills are both on 1-minute bars: a bar
holding the stop and the first target cannot be resolved from OHLC at any
price, and a 1R target sits close enough to the stop for that to be common.

Three figures here do not appear for any other strategy in this project:

``tp1 rate``  the share of filled trades that reached the first target. This is
    the number the rules were sold on -- "60-68%" -- and it is not the win rate,
    because a trade can reach 1R on half the position and still lose money on
    the rest.
``narrow``  days where the opening range was shorter than the stop distance, so
    the runner's target sat nearer than the first target. The rules as written
    put no floor under the range; this counts what that costs.
``one lot``  positions too small to halve, which take the first target whole.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from backtest import metrics
from backtest.engine import BacktestConfig, simulate
from ict import data as D
from strategies.or5_retest import OR5Config, generate_orders

PERIODS = {"development": ("2016-01-01", "2024-01-01"),
           "out_of_sample": ("2024-01-01", "2027-01-01")}


def execution(risk: float, policy: str, cfg: OR5Config) -> BacktestConfig:
    """The engine settings this model needs, and why each differs.

    A stop entry is a market order once triggered, so it slips like one -- the
    repo's other strategies rest limits and pay nothing on entry.
    """
    return BacktestConfig(
        risk_per_trade_usd=risk,
        ambiguity=policy,
        entry_side="stop",
        entry_slippage_ticks=1.0,
        scale_out_fraction=0.5,
        runner_stop_offset=cfg.runner_stop_offset,
    )


def summarise(orders: pd.DataFrame, bars: pd.DataFrame, risk: float,
              cfg: OR5Config) -> dict:
    out = {}
    for policy in ("pessimistic", "optimistic"):
        t = simulate(orders, bars, execution(risk, policy, cfg))
        out[policy] = metrics.summarize(t)
        out[policy + "_trades"] = t
    return out


def show(title: str, orders: pd.DataFrame, res: dict, days: int) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    if orders.empty:
        print("  no signals")
        return
    pess, opt = res["pessimistic"], res["optimistic"]
    tp, to = res["pessimistic_trades"], res["optimistic_trades"]
    filled = tp[tp["filled"]]

    print(f"  qualifying days      {days:,}")
    print(f"  signals / filled     {pess['signals']} / {pess['trades']}"
          f"   ({pess['trades'] / max(pess['signals'], 1):.0%} fill rate)")
    if filled.empty:
        return
    print(f"  signal rate          {pess['signals'] / max(days, 1):.0%} of qualifying days")
    print(f"  tp1 rate             {filled['tp1_hit'].mean():.1%}"
          f"   <- the '60-68%' the rules claim")
    # scaled_out is false both when the first target was missed and when the
    # position was too small to halve. Reporting the two together reads as a
    # size problem that is not there.
    hit = filled[filled["tp1_hit"]]
    whole = 1 - hit["scaled_out"].mean() if len(hit) else 0.0
    print(f"  one-lot exits        {whole:.0%} of the trades that reached tp1")
    if "height_under_r" in orders.columns:
        narrow = orders["height_under_r"].mean()
        print(f"  narrow range days    {narrow:.0%}"
              f"   (runner target inside tp1)")
    print(f"  ambiguous bars       {pess['ambiguous_pct']:.1%}")
    print(f"\n  {'':<20}{'pessimistic':>14}{'optimistic':>14}")
    for key, label, fmt in (("win_rate", "win rate", "{:.1%}"),
                            ("profit_factor", "profit factor", "{:.2f}"),
                            ("avg_r", "avg R", "{:+.3f}"),
                            ("total_pnl", "net P&L", "${:,.0f}"),
                            ("max_drawdown_usd", "max drawdown", "${:,.0f}")):
        print(f"  {label:<20}{fmt.format(pess[key]):>14}{fmt.format(opt[key]):>14}")


def split_by_narrow(orders: pd.DataFrame, bars: pd.DataFrame, risk: float,
                    cfg: OR5Config) -> None:
    """What the missing floor under the range actually cost."""
    if orders.empty or not orders["height_under_r"].any():
        return
    print(f"\n  {'':<20}{'narrow':>12}{'normal':>12}   (the B2 consequence)")
    for flag, label in ((True, "narrow"), (False, "normal")):
        sub = orders[orders["height_under_r"] == flag]
        if sub.empty:
            continue
        t = simulate(sub, bars, execution(risk, "pessimistic", cfg))
        s = metrics.summarize(t)
        if label == "narrow":
            row = {k: s[k] for k in ("trades", "win_rate", "avg_r", "total_pnl")}
            narrow_row = row
        else:
            for key, fmt in (("trades", "{:,}"), ("win_rate", "{:.1%}"),
                             ("avg_r", "{:+.3f}"), ("total_pnl", "${:,.0f}")):
                print(f"  {key:<20}{fmt.format(narrow_row[key]):>12}"
                      f"{fmt.format(s[key]):>12}")


def tp1_sweep(orders: pd.DataFrame, bars: pd.DataFrame, risk: float,
              cfg: OR5Config) -> None:
    """The author's own suggestion: move the first target and see.

    Reported as a row per setting rather than as a best pick. The point of a
    sweep is whether the model survives its own parameters, not which cell of
    it wins.
    """
    if orders.empty:
        return
    print(f"\n  {'tp1':<8}{'hit rate':>10}{'avg R':>10}{'profit factor':>15}"
          f"{'net P&L':>12}")
    for r in (0.5, 0.8, 1.0, 1.5, 2.0):
        moved = orders.copy()
        moved["target_price"] = (moved["entry_price"]
                                 + moved["direction"] * r * moved["risk_points"])
        t = simulate(moved, bars, execution(risk, "pessimistic", cfg))
        f = t[t["filled"]]
        if f.empty:
            continue
        m = metrics.summarize(t)
        print(f"  {r:<8.1f}{f['tp1_hit'].mean():>9.1%}{m['avg_r']:>10.3f}"
              f"{m['profit_factor']:>15.2f}{m['total_pnl']:>12,.0f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--risk", type=float, default=500.0,
                    help="dollars per trade (default matches the other models)")
    args = ap.parse_args(argv)

    cfg = OR5Config()
    bars = D.add_time_columns(pd.read_parquet(
        D.PROCESSED_DIR / "nq_1m.parquet",
        filters=[("ts", ">=", pd.Timestamp("2015-12-01", tz="UTC"))])
    ).reset_index(drop=True)

    orders = generate_orders(bars, cfg)
    print(f"{len(orders)} signals from {bars['trading_date'].nunique():,} sessions"
          f"   risk ${args.risk:,.0f}/trade")
    print("NOTE: the news filter (F2) is NOT applied -- no calendar in the repo.")

    for name, (start, end) in PERIODS.items():
        lo, hi = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
        window = bars[(bars["ts"] >= lo) & (bars["ts"] < hi)]
        o = orders[(orders["valid_from"] >= lo) & (orders["valid_from"] < hi)]
        days = window["trading_date"].nunique()
        res = summarise(o, bars, args.risk, cfg)
        show(f"{name.upper()}  {start} -> {end}", o, res, days)
        split_by_narrow(o, bars, args.risk, cfg)
        tp1_sweep(o, bars, args.risk, cfg)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
