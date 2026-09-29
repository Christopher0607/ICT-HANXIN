"""Append newly arrived data to the forward-test log and score it.

    uv run python scripts/fetch_databento.py --start ... --end ...
    uv run python scripts/ingest.py
    uv run python scripts/forward_log.py

Every backtest number in this repo was computed on data I had already seen.
The forward log is the one record that cannot be, because each entry is
written before the next bar exists. It is the only evidence that can
distinguish "the market changed after 2023" from "this is the best stretch in
a decade and we are looking at it because it is the best".

Which means it is worth very little until there is a lot of it. The first
entry covers five trading days; a five-day window has a standard deviation of
about $1,024 against a median of $336, so almost any result sits inside the
noise. Six to twelve months is where this starts to mean something. The
summary below therefore leads with how much evidence has accumulated, not
with the running total, and refuses to call a difference significant until it
clears two standard errors.

Rows are appended, never rewritten: a log that gets recomputed is not a
forward log. Re-running only adds trades whose exit is past the last entry.

The log was started at $500 per trade and has to stay there for the same
reason. ``--restate-risk`` prints the same signals at another size without
touching the file, for reading the dollars at whatever the account is actually
running; ``r_multiple`` is the column that does not care either way.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

from backtest import metrics
from backtest.engine import BacktestConfig, simulate
from ict import data as D
from strategies.ltf_sweep import LTFSweepConfig, generate_orders

NY = "America/New_York"
DEFAULT_LOG = pathlib.Path("docs/forward_log.csv")

#: First bar the research never saw. Everything up to here went into the
#: backtests, so it cannot be part of a forward test.
FORWARD_START = pd.Timestamp("2026-08-29", tz="UTC")

#: The out-of-sample period the forward result is compared against.
BASELINE_START = pd.Timestamp("2024-01-01", tz="UTC")

COLUMNS = ["date", "entry_et", "exit_et", "side", "entry", "stop", "target",
           "contracts", "risk_points", "r_multiple", "net_pnl", "exit_reason"]


def trade_rows(trades: pd.DataFrame) -> pd.DataFrame:
    """One row per filled trade, in New York time."""
    # A window with no signals comes back as a bare frame with none of these
    # columns, which is the normal case on a re-run that finds nothing new.
    if trades.empty or "entry_ts" not in trades.columns:
        return pd.DataFrame(columns=COLUMNS)
    f = trades[trades["filled"]].sort_values("entry_ts")
    if f.empty:
        return pd.DataFrame(columns=COLUMNS)
    ent = f["entry_ts"].dt.tz_convert(NY)
    out = pd.DataFrame({
        "date": ent.dt.strftime("%Y-%m-%d"),
        "entry_et": ent.dt.strftime("%H:%M"),
        "exit_et": f["exit_ts"].dt.tz_convert(NY).dt.strftime("%H:%M"),
        "side": np.where(f["direction"] > 0, "LONG", "SHORT"),
        "entry": f["entry_fill"].round(2), "stop": f["stop_price"].round(2),
        "target": f["target_price"].round(2), "contracts": f["contracts"].astype(int),
        "risk_points": f["risk_points"].round(2), "r_multiple": f["r_multiple"].round(3),
        "net_pnl": f["net_pnl"].round(2), "exit_reason": f["exit_reason"],
    })
    return out[COLUMNS].reset_index(drop=True)


def baseline(df1: pd.DataFrame, cfg: LTFSweepConfig, risk: float) -> pd.DataFrame:
    """The out-of-sample trades the forward result is measured against."""
    bars = df1[(df1.ts >= BASELINE_START) & (df1.ts < FORWARD_START)].reset_index(drop=True)
    t = simulate(generate_orders(bars, cfg, one_per_day=True), bars,
                 BacktestConfig(risk_per_trade_usd=risk))
    return t[t["filled"]]


def report(log: pd.DataFrame, base: pd.DataFrame) -> None:
    """Say how much evidence there is before saying what it shows."""
    n = len(log)
    days = log["date"].nunique()
    total = float(log["net_pnl"].sum())
    print(f"\n{'=' * 70}\nFORWARD LOG\n{'=' * 70}")
    print(f"  {log['date'].min()} -> {log['date'].max()}   "
          f"{days} trading days, {n} trades")
    print(f"  net P&L              ${total:+,.2f}")
    print(f"  mean per trade       ${log['net_pnl'].mean():+,.2f}")
    print(f"  win rate             {(log['net_pnl'] > 0).mean():.1%}")

    exp = float(base["net_pnl"].mean())
    sd = float(base["net_pnl"].std())
    se = sd / np.sqrt(n) if n else float("nan")
    print(f"\n  out-of-sample baseline   ${exp:+,.2f}/trade  (sd ${sd:,.0f}, "
          f"{len(base):,} trades)")
    print(f"  expected over {n} trades  ${exp * n:+,.2f}")

    if n < 30:
        print(f"\n  NOT ENOUGH DATA. {n} trades cannot separate a real change from "
              f"noise.\n  One standard error is still ${se:,.0f} per trade "
              f"(${se * n:,.0f} over the log).")
    else:
        z = (log["net_pnl"].mean() - exp) / se if se else 0.0
        verdict = ("consistent with the baseline" if abs(z) < 2
                   else "BELOW the baseline by more than 2 SE" if z < 0
                   else "ABOVE the baseline by more than 2 SE")
        print(f"\n  z vs baseline        {z:+.2f}   -> {verdict}")

    daily = log.groupby("date")["net_pnl"].sum().to_numpy()
    if len(daily) >= 5:
        w = np.array([daily[i:i + 5].sum() for i in range(len(daily) - 4)])
        print(f"\n  5-day windows in the log: median ${np.median(w):+,.0f}, "
              f"{(w > 0).mean():.0%} positive")


def resume_from(existing: pd.DataFrame | None) -> pd.Timestamp:
    """The first bar a re-run may score: the day after the log's last entry.

    This is the whole of the append-only guarantee, so it lives in one named
    place. Scoring from any earlier bar would recompute rows that are already
    written, and a log that gets recomputed is not a forward log.
    """
    if existing is None or existing.empty:
        return FORWARD_START
    last = pd.Timestamp(existing["date"].max(), tz=NY)
    return last.tz_convert("UTC") + pd.Timedelta(days=1)


def assert_same_trades(log: pd.DataFrame, rows: pd.DataFrame, risk: float) -> None:
    """Fail unless both frames hold the same trades, ignoring size.

    Split out from ``restate`` so it can be tested on the disagreement it
    exists to catch, rather than only on data where it happens to be quiet.
    """
    key = ["date", "entry_et"]
    odd = log[key].merge(rows[key], on=key, how="outer", indicator=True)
    odd = odd[odd["_merge"] != "both"]
    if odd.empty:
        return
    first = odd.iloc[0]
    raise SystemExit(
        f"the log and the ${risk:,.0f} run disagree about {len(odd)} trade(s), "
        f"first {first['date']} {first['entry_et']}. Size must not change "
        "which signals fire; something upstream of sizing is reading the risk."
    )


def restate(log: pd.DataFrame, df1: pd.DataFrame, cfg: LTFSweepConfig,
            risk: float) -> pd.DataFrame:
    """The trades already in the log, re-sized at ``risk``. Never written.

    The log is append-only and was started at one risk per trade, so it cannot
    be re-sized in place: the earlier rows would come to mean something
    different from the later ones and the record would be worth nothing. This
    re-runs the same signals at a different size and reports the dollars,
    leaving the file alone.

    Position size must not reach back into signal generation, so the restated
    run has to produce the *same* trades. A trade one side has and the other
    does not is that leak, and raises rather than being averaged over.
    """
    bars = df1[df1.ts >= FORWARD_START].reset_index(drop=True)
    trades = simulate(generate_orders(bars, cfg, one_per_day=True), bars,
                      BacktestConfig(risk_per_trade_usd=risk))
    rows = trade_rows(trades)
    # The log stops at its last entry; data past it is not restated, it is
    # simply not logged yet.
    rows = rows[rows["date"] <= log["date"].max()].reset_index(drop=True)

    assert_same_trades(log, rows, risk)
    return rows


def report_restated(log: pd.DataFrame, rows: pd.DataFrame, risk: float) -> None:
    """Same signals, different size. R is the part that carries over."""
    print(f"\n  RESTATED AT ${risk:,.0f}/TRADE  (printed, never logged)")
    print(f"    net P&L            ${rows['net_pnl'].sum():+,.2f}   "
          f"(logged ${log['net_pnl'].sum():+,.2f})")
    print(f"    mean per trade     ${rows['net_pnl'].mean():+,.2f}")
    print(f"    contracts          {log['contracts'].tolist()} -> "
          f"{rows['contracts'].tolist()}")
    print(f"    total R            {log['r_multiple'].sum():+.2f} -> "
          f"{rows['r_multiple'].sum():+.2f}   "
          "(size does not move R; only the integer lot rounding does)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--risk", type=float, default=500.0,
                    help="must match the risk the log was started with")
    ap.add_argument("--restate-risk", type=float, default=None,
                    help="also print the logged trades re-sized at this risk; "
                         "reporting only, the log is still written at --risk")
    ap.add_argument("--dry-run", action="store_true", help="score but do not write")
    args = ap.parse_args(argv)

    path = pathlib.Path(args.log)
    existing = pd.read_csv(path, dtype={"date": str}) if path.exists() else None
    if existing is not None and not existing.empty:
        print(f"log has {len(existing)} trades through {existing['date'].max()}")
    else:
        print(f"no log yet; starting at {FORWARD_START.date()} "
              "(the first bar the research never saw)")
    start = resume_from(existing)

    df1 = D.load("1m")
    bars = df1[df1.ts >= start].reset_index(drop=True)
    if bars.empty:
        print(f"no bars after {start.date()}; nothing to add. "
              "Fetch newer data first.")
        return 0

    print(f"scoring {start.date()} -> {df1.ts.max().date()} "
          f"({bars.trading_date.nunique()} trading days) ...")
    cfg = LTFSweepConfig()
    trades = simulate(generate_orders(bars, cfg, one_per_day=True), bars,
                      BacktestConfig(risk_per_trade_usd=args.risk))
    fresh = trade_rows(trades)
    print(f"  {len(fresh)} new filled trades")

    log = pd.concat([existing, fresh], ignore_index=True) if existing is not None else fresh
    if log.empty:
        print("nothing logged yet.")
        return 0
    log = log.drop_duplicates(subset=["date", "entry_et"], keep="first")

    report(log, baseline(df1, cfg, args.risk))
    if args.restate_risk:
        report_restated(log, restate(log, df1, cfg, args.restate_risk),
                        args.restate_risk)

    if args.dry_run:
        print("\n--dry-run: not written")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    log.to_csv(path, index=False)
    print(f"\nwrote {path} ({len(log)} trades)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
