"""Export recent LTF-sweep signals for checking the Pine Script port.

    uv run python scripts/export_signals.py --months 3

The Pine Script cannot be compiled or run in this repo, so the only way to
verify the port is to put the two side by side: load the script on the same
symbol and dates, and check that it marks the same bars at the same prices.

TradingView's feed is not Databento's, so a handful of signals will differ —
a sweep that penetrates by exactly one tick on one feed may not on the other.
Wholesale disagreement means the port is wrong; a few edge cases do not.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from backtest.engine import BacktestConfig, simulate
from ict import data as D
from strategies.ltf_sweep import LTFSweepConfig, generate_orders

NY = "America/New_York"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--out", default="docs/ltf_signals_recent.csv")
    ap.add_argument("--risk", type=float, default=500.0)
    args = ap.parse_args(argv)

    df1 = D.load("1m")
    start = (df1.ts.max() - pd.DateOffset(months=args.months)).normalize()
    bars = df1[df1.ts >= start].reset_index(drop=True)
    print(f"generating signals {start.date()} -> {df1.ts.max().date()} ...")

    orders = generate_orders(bars, LTFSweepConfig(), one_per_day=True)
    trades = simulate(orders, bars, BacktestConfig(risk_per_trade_usd=args.risk))

    ny = lambda s: pd.DatetimeIndex(s).tz_convert(NY)
    out = pd.DataFrame({
        "date": ny(trades["signal_ts"]).strftime("%Y-%m-%d"),
        "sweep_et": ny(trades["signal_ts"]).strftime("%H:%M"),
        "choch_et": ny(trades["confirmation_ts"]).strftime("%H:%M"),
        "side": trades["direction"].map({1: "LONG", -1: "SHORT"}),
        "pool_level": trades["pool_level"].round(2),
        "sweep_extreme": trades["sweep_extreme"].round(2),
        "fvg_low": trades["fvg_bottom"].round(2),
        "fvg_high": trades["fvg_top"].round(2),
        "entry": trades["entry_price"].round(2),
        "stop": trades["stop_price"].round(2),
        "target": trades["target_price"].round(2),
        "risk_points": trades["risk_points"].round(2),
        # Planned size is what the Pine status table shows the moment the setup
        # confirms, so it is the comparable figure even when the limit never
        # filled. `contracts` is what was actually traded (0 if unfilled).
        "planned_contracts": [BacktestConfig(risk_per_trade_usd=args.risk).size_for(r)
                              for r in trades["risk_points"]],
        "contracts": trades["contracts"],
        "filled": trades["filled"],
        "fill_et": ny(trades["entry_ts"]).strftime("%H:%M"),
        "exit_et": ny(trades["exit_ts"]).strftime("%H:%M"),
        "exit_reason": trades["exit_reason"],
        "r_multiple": trades["r_multiple"].round(3),
        "net_pnl": trades["net_pnl"].round(2),
    })
    # Unfilled orders have no fill or exit; blank them rather than printing the
    # NaT-formatted placeholders pandas produces.
    unfilled = ~out["filled"].astype(bool)
    for col in ("fill_et", "exit_et", "r_multiple", "net_pnl"):
        out[col] = out[col].astype("object")
        out.loc[unfilled, col] = ""
    out.to_csv(args.out, index=False)

    filled = int(trades["filled"].sum())
    print(f"wrote {args.out}: {len(out)} signals, {filled} filled, "
          f"{int((trades['net_pnl'] > 0).sum())} winners")
    print("\nfirst five, for a quick eyeball against the chart:")
    print(out.head(5).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
