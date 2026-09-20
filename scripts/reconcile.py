"""Compare what the bridge actually did against what the research engine says.

    uv run python scripts/reconcile.py --start 2026-09-01 --end 2026-09-10

This is the only thing that catches "I thought I was running A and I was
running B". A broker statement shows what happened; the backtest shows what
should have happened; nothing else puts them side by side.

Differences are sorted into three buckets, because they mean very different
things:

**data**      -- same setup, entry price off by a tick or two. TopstepX's feed
                 is not Databento's, so a handful of these is normal and
                 expected. Nothing to do.
**settings**  -- the trade was taken but at the wrong SIZE. That means the
                 risk figures on the chart and in the backtest disagree, so
                 every position is mis-scaled. Stop and fix before trading.
**logic**     -- a trade the engine has and the bridge does not, or the other
                 way round, with no guard entry explaining it. Something is
                 running different code. Stop.

A blocked or skipped signal is not a difference: the guard is supposed to do
that, and the journal records why, so those are reported separately. The same
goes for a signal missed because the feed went stale -- the engine refusing to
trade on ten-minute-old bars is it working, not failing.

The engine and the journal should now agree on timing: three places that used
to read a deadline off the bars in hand rather than off the session's schedule
have been fixed, so an order goes out at the confirmation instant rather than a
bar later. A systematic one-bar difference here means one of them has regressed
-- see ``tests/test_live.py::test_the_order_goes_out_the_instant_the_setup_confirms``.
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

#: Evaluations to buy without passing before stopping to re-examine.
#:
#: Measured rather than chosen. Replaying 2016-2026 at $900 a trade, the
#: evaluations bought per funded account earned were 2 at the median, 3 at the
#: 75th percentile and 6 at the 90th. A run past six is therefore the worst
#: tenth of the distribution rather than ordinary bad luck, and the point at
#: which "one more reset" stops being a reasonable thing to say.
#: ``scripts/account_lifetime.py`` recomputes it.
STOPPING_RULE = 6


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


def engine_trades(start: str, end: str, risk: float,
                  cutoff_minute: int | None = None) -> pd.DataFrame:
    """What the research engine says should have been traded.

    Run with the live engine's own session rule, not the backtest's. Two things
    would otherwise show up as logic differences on every single day:
    ``min_session_bars=0`` (the backtest's 60-bar floor is a data sanity check
    that would hold every live signal back to 10:29), and the cutoff after which
    ``bridge/live.py`` stops placing and cancels what has not filled.
    """
    df1 = D.load("1m")
    bars = df1[(df1.ts >= start) & (df1.ts < end)].reset_index(drop=True)
    if bars.empty:
        return pd.DataFrame(columns=["date", "side", "qty", "entry", "stop", "target"])
    t = simulate(generate_orders(bars, LTFSweepConfig(min_session_bars=0),
                                 one_per_day=True), bars,
                 BacktestConfig(risk_per_trade_usd=risk))
    f = t[t["filled"]]
    if cutoff_minute is not None and not f.empty:
        # The engine places until the cutoff and cancels the rest, so a trade
        # confirmed or filled after it is not one the live run could have had.
        ny_valid = f["valid_from"].dt.tz_convert(NY)
        ny_entry = f["entry_ts"].dt.tz_convert(NY)
        within = ((ny_valid.dt.hour * 60 + ny_valid.dt.minute <= cutoff_minute)
                  & (ny_entry.dt.hour * 60 + ny_entry.dt.minute <= cutoff_minute))
        f = f[within]
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


def account_ledger(rows: list[dict]) -> list[dict]:
    """Every account the journal has seen, oldest first.

    ``session_start`` already carries enough to tell them apart, so nothing new
    has to be written: ``live_data`` is false only on a Practice account, and
    ``use_guard`` is on for a funded account and off for an evaluation -- the
    two-stage settings in docs/GO_LIVE.md. What has been spent is therefore
    already in the file, and was before anyone thought to count it.
    """
    seen: dict = {}
    for r in rows:
        if r.get("event") != "session_start":
            continue
        account = r.get("account_id")
        if account is None:
            continue
        when = str(r.get("ts", ""))[:10]
        phase = ("practice" if not r.get("live_data")
                 else "funded" if r.get("use_guard") else "evaluation")
        if account not in seen:
            seen[account] = {"account_id": account, "phase": phase,
                             "first": when, "last": when, "sessions": 0}
        seen[account]["sessions"] += 1
        seen[account]["last"] = when
        # An account that is reconfigured mid-life keeps its latest reading:
        # switching the guard on is how an evaluation becomes funded.
        seen[account]["phase"] = phase
    return sorted(seen.values(), key=lambda a: (a["first"], str(a["account_id"])))


def print_progress(ledger: list[dict], rule: int = STOPPING_RULE) -> None:
    """Where the plan stands against the stopping rule, every time this runs.

    The rule is only worth setting if it is in front of you on the days it
    matters, and the day it matters is the one where buying another reset feels
    obviously right.
    """
    if not ledger:
        return
    practice = [a for a in ledger if a["phase"] == "practice"]
    real = [a for a in ledger if a["phase"] != "practice"]
    if not real:
        print(f"\naccounts: {len(practice)} practice, none live yet")
        return

    # Evaluations bought since the last one that reached funded.
    streak, funded = 0, 0
    for account in real:
        if account["phase"] == "funded":
            funded, streak = funded + 1, 0
        else:
            streak += 1

    print(f"\nACCOUNTS   ({len(practice)} practice, not counted)")
    for account in real:
        mark = "funded" if account["phase"] == "funded" else "evaluation"
        span = (account["first"] if account["first"] == account["last"]
                else f'{account["first"]} to {account["last"]}')
        print(f"  {str(account['account_id']):<12} {mark:<11} {span}"
              f"   {account['sessions']} sessions")

    left = rule - streak
    print(f"\n  evaluations since the last pass: {streak} (the current one"
          f" included), funded accounts earned: {funded}")
    if left > 0:
        print(f"  {left} more before the stopping rule at {rule}.")
    else:
        print(f"  *** {streak} evaluations without a pass, and the rule was {rule}."
              f" Measured, that is the worst tenth of the distribution. Stop and"
              f" re-read the forward log before buying another. ***")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--journal", default=str(DEFAULT_PATH))
    ap.add_argument("--start", required=True, help="inclusive UTC date")
    ap.add_argument("--end", required=True, help="exclusive UTC date")
    ap.add_argument("--risk", type=float, default=900.0,
                    help="must match the risk the bridge was configured with")
    ap.add_argument("--stopping-rule", type=int, default=STOPPING_RULE,
                    help="evaluations without a pass before stopping to "
                         "re-examine (default %(default)s, measured)")
    ap.add_argument("--cutoff-minute", type=int, default=15 * 60 + 30,
                    help="must match BRIDGE_CUTOFF_MINUTE (default 930 = 15:30 ET)")
    args = ap.parse_args(argv)

    rows = Journal(args.journal).read()
    if not rows:
        print(f"no journal at {args.journal}; nothing to reconcile.")
        return 0

    bridge = bridge_trades(rows)
    guarded: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r.get("event") in ("blocked", "skipped", "stale_feed"):
            day = r.get("day") or str(r.get("ts", ""))[:10]
            guarded[day].append(r.get("reason") or r.get("why") or r["event"])

    bridge = bridge[(bridge.date >= args.start) & (bridge.date < args.end)]
    engine = engine_trades(args.start, args.end, args.risk,
                           cutoff_minute=args.cutoff_minute)

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

    print_progress(account_ledger(rows), args.stopping_rule)

    serious = [d for d in diffs if d[0] in ("logic", "settings")]
    if serious:
        print(f"\n{len(serious)} disagreement(s) that are not a feed difference. "
              "Stop trading and find out why before the next session.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
