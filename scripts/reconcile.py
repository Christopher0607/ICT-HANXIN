"""Compare what the bridge actually did against what the research engine says.

    uv run python scripts/reconcile.py --start 2026-09-01 --end 2026-09-10

This is the only thing that catches "I thought I was running A and I was
running B". A broker statement shows what happened; the backtest shows what
should have happened; nothing else puts them side by side.

Differences are sorted into three buckets, because they mean very different
things:

**data**      -- same setup, entry price off by a tick or two. TradingView's
                 feed is not Databento's, so a handful of these is normal and
                 expected. Nothing to do.
**settings**  -- the trade was taken but at the wrong SIZE. That means the
                 risk figures on the chart and in the backtest disagree, so
                 every position is mis-scaled. Stop and fix before trading.
**logic**     -- a trade the engine has and the bridge does not, or the other
                 way round, with no guard entry explaining it. Something is
                 running different code. Stop.

A blocked or skipped signal is not a difference: the guard is supposed to do
that, and the journal records why, so those are reported separately.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

import pandas as pd

from backtest.engine import BacktestConfig, simulate
from bridge.journal import DEFAULT_PATH, Journal
from ict import data as D
from strategies.ltf_sweep import LTFSweepConfig, generate_orders

NY = "America/New_York"
#: Entry prices closer than this are a feed difference, not a fault.
PRICE_TOLERANCE = 2.0


def bridge_trades(rows: list[dict]) -> pd.DataFrame:
    """Placed orders from the journal, one row per trade."""
    placed = [r for r in rows if r.get("event") == "placed"]
    if not placed:
        return pd.DataFrame(columns=["date", "side", "qty", "entry", "stop", "target"])
    return pd.DataFrame([{
        "date": r.get("day") or str(r.get("ts", ""))[:10],
        "side": r.get("side"), "qty": int(r.get("qty", 0)),
        "entry": float(r.get("entry", "nan")), "stop": float(r.get("stop", "nan")),
        "target": float(r.get("target", "nan")),
    } for r in placed])


def engine_trades(start: str, end: str, risk: float) -> pd.DataFrame:
    """What the research engine says should have been traded."""
    df1 = D.load("1m")
    bars = df1[(df1.ts >= start) & (df1.ts < end)].reset_index(drop=True)
    if bars.empty:
        return pd.DataFrame(columns=["date", "side", "qty", "entry", "stop", "target"])
    t = simulate(generate_orders(bars, LTFSweepConfig(), one_per_day=True), bars,
                 BacktestConfig(risk_per_trade_usd=risk))
    f = t[t["filled"]]
    if f.empty:
        return pd.DataFrame(columns=["date", "side", "qty", "entry", "stop", "target"])
    return pd.DataFrame({
        "date": f["entry_ts"].dt.tz_convert(NY).dt.strftime("%Y-%m-%d"),
        "side": ["buy" if d > 0 else "sell" for d in f["direction"]],
        "qty": f["contracts"].astype(int).to_numpy(),
        "entry": f["entry_fill"].round(2).to_numpy(),
        "stop": f["stop_price"].round(2).to_numpy(),
        "target": f["target_price"].round(2).to_numpy(),
    }).reset_index(drop=True)


def compare(bridge: pd.DataFrame, engine: pd.DataFrame,
            guarded: dict[str, list[str]]) -> list[tuple[str, str, str]]:
    """One (bucket, date, detail) per disagreement."""
    out = []
    b = {r.date: r for r in bridge.itertuples()}
    e = {r.date: r for r in engine.itertuples()}

    for day in sorted(set(b) | set(e)):
        bt, et = b.get(day), e.get(day)
        if bt and not et:
            out.append(("logic", day,
                        f"bridge traded {bt.side} {bt.qty} @ {bt.entry}, "
                        "the engine has no trade"))
        elif et and not bt:
            why = guarded.get(day)
            if why:
                continue          # the guard explains it; reported separately
            out.append(("logic", day,
                        f"engine has {et.side} {et.qty} @ {et.entry}, "
                        "the bridge placed nothing and logged no reason"))
        else:
            if bt.side != et.side:
                out.append(("logic", day, f"side differs: bridge {bt.side}, engine {et.side}"))
            if bt.qty != et.qty:
                out.append(("settings", day,
                            f"size differs: bridge {bt.qty}, engine {et.qty} "
                            "-- the risk settings disagree, every position is mis-scaled"))
            if abs(bt.entry - et.entry) > PRICE_TOLERANCE:
                out.append(("data", day,
                            f"entry differs by {abs(bt.entry - et.entry):.2f}: "
                            f"bridge {bt.entry}, engine {et.entry}"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--journal", default=str(DEFAULT_PATH))
    ap.add_argument("--start", required=True, help="inclusive UTC date")
    ap.add_argument("--end", required=True, help="exclusive UTC date")
    ap.add_argument("--risk", type=float, default=1000.0,
                    help="must match the risk the bridge was configured with")
    args = ap.parse_args(argv)

    rows = Journal(args.journal).read()
    if not rows:
        print(f"no journal at {args.journal}; nothing to reconcile.")
        return 0

    bridge = bridge_trades(rows)
    guarded: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r.get("event") in ("blocked", "skipped"):
            day = r.get("day") or str(r.get("ts", ""))[:10]
            guarded[day].append(r.get("reason") or r.get("why") or r["event"])

    bridge = bridge[(bridge.date >= args.start) & (bridge.date < args.end)]
    engine = engine_trades(args.start, args.end, args.risk)

    print(f"{args.start} -> {args.end}   bridge {len(bridge)} trades, "
          f"engine {len(engine)} trades, {sum(len(v) for v in guarded.values())} "
          "signals the guard stopped")

    diffs = compare(bridge, engine, guarded)
    if not diffs:
        print("\nno disagreements.")
    else:
        for bucket in ("logic", "settings", "data"):
            rows_ = [d for d in diffs if d[0] == bucket]
            if not rows_:
                continue
            print(f"\n{bucket.upper()}  ({len(rows_)})")
            for _, day, detail in rows_:
                print(f"  {day}  {detail}")

    if guarded:
        print("\nstopped by the guard (working as intended, listed for the record)")
        for day in sorted(guarded):
            print(f"  {day}  {', '.join(guarded[day])}")

    serious = [d for d in diffs if d[0] in ("logic", "settings")]
    if serious:
        print(f"\n{len(serious)} disagreement(s) that are not a feed difference. "
              "Stop trading and find out why before the next session.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
