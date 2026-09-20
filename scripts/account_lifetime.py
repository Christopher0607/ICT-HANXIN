"""How long an account lives, and whether running several changes anything.

    uv run python scripts/account_lifetime.py

Three questions, one script:

**How long until an account dies?** Separately for evaluations and for funded
accounts, because they die of different things -- an evaluation races a target
against a limit, while a funded account's limit locks at $0 once it is $2,000
up and after that can only be emptied.

**Does running several accounts help?** Only if they do not die together. The
same strategy sent to N accounts takes the same trades on the same days, so
they breach on the same trade: that is not diversification, it is N times the
position with N times the fees. Starting them a month apart is the version of
the idea that might work, and the difference is measured here rather than
assumed.

**Which pricing path wins at N accounts?** Both paths scale with the number of
accounts, so the answer should not move. "Should not" is not "does not", so it
is computed.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

from backtest.engine import BacktestConfig, simulate
from backtest.recycle import Policy, XFARules, costs, recycle
from ict import data as D

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

RULES = XFARules()
#: Topstep's ceiling on simultaneous Express Funded Accounts, read from their
#: help centre on 2026-09-20. Trading Combines have no such limit.
MAX_XFA = 5
API_MONTHLY = RULES.api_monthly


def lives(trades: pd.DataFrame, timeline: list[dict]) -> pd.DataFrame:
    """Rebuild each account's span from the events that ended it.

    Accounts are not objects that outlive ``recycle``'s loop, so this works
    backwards from the timeline: every bust or pass closes the account that was
    open, and the next one opens on the following trade.
    """
    exits = pd.DatetimeIndex(trades["exit_ts"])
    rows, start, funded, payouts = [], 0, False, 0

    for event in timeline:
        at, kind = event["trade"], event["event"]
        if kind == "payout":
            payouts += 1
            continue
        rows.append({
            "kind": "funded" if funded else "combine",
            "outcome": {"passed": "passed", "combine_bust": "busted",
                        "funded_bust": "busted"}[kind],
            "trades": at - start + 1,
            "days": (exits[at] - exits[start]).days,
            "payouts": payouts if funded else 0,
        })
        funded = kind == "passed"
        payouts, start = 0, at + 1

    if start < len(trades):          # the account still open when the data ends
        rows.append({"kind": "funded" if funded else "combine",
                     "outcome": "alive", "trades": len(trades) - start,
                     "days": (exits[-1] - exits[start]).days,
                     "payouts": payouts if funded else 0})

    out = pd.DataFrame(rows)
    # Every trade belongs to exactly one account. If that does not hold the
    # reconstruction is wrong and every number below it is meaningless.
    assert out["trades"].sum() == len(trades), (
        f"{out['trades'].sum()} trades across accounts, {len(trades)} traded")
    return out


def describe(group: pd.DataFrame, label: str) -> None:
    if group.empty:
        print(f"  {label:<22} none")
        return
    busted = group[group["outcome"] == "busted"]
    q = group["trades"].quantile([0.25, 0.5, 0.75])
    print(f"  {label:<22}{len(group):>5}{q[0.25]:>8.0f}{q[0.5]:>8.0f}"
          f"{q[0.75]:>8.0f}{group['trades'].max():>7.0f}"
          f"{group['days'].median():>9.0f}"
          f"{len(busted) / len(group):>9.0%}")


def multi_account_cost(single: dict, n: int) -> dict:
    """Cost and cash for ``n`` identical accounts run side by side.

    Subscriptions, resets and activations are per account; the API subscription
    is one key for one bridge however many accounts it copies to. Funded
    accounts are capped, so past the ceiling the evaluation side keeps costing
    and the payout side stops growing.
    """
    lines = single["cost_lines"]
    per_account = lines["subscription"] + lines["paid_resets"] + lines["activation"]
    funded_n = min(n, MAX_XFA)
    return {
        "n": n,
        "cost": per_account * n + lines["api_access"],
        "cash": single["cash"] * funded_n,
        "net": single["cash"] * funded_n - (per_account * n + lines["api_access"]),
        "capped": n > MAX_XFA,
    }


def staggered(trades: pd.DataFrame, n: int, gap_days: int = 30) -> dict:
    """``n`` accounts started a month apart, each run independently.

    The question is whether they still die together. Perfectly correlated
    accounts share a breach date; staggered ones only share it by coincidence.
    """
    exits = pd.DatetimeIndex(trades["exit_ts"])
    chains, bust_dates = [], []
    for k in range(n):
        begin = exits[0] + pd.Timedelta(days=gap_days * k)
        chunk = trades[exits >= begin].reset_index(drop=True)
        if len(chunk) < 50:
            continue
        out = recycle(chunk, policy=Policy.IMMEDIATE)
        chains.append(out)
        dates = {pd.Timestamp(e["ts"]).date() for e in out["timeline"]
                 if e["event"].endswith("bust")}
        bust_dates.append(dates)

    shared = set.intersection(*bust_dates) if bust_dates else set()
    union = set.union(*bust_dates) if bust_dates else set()
    return {"chains": len(chains),
            "net": sum(c["net"] for c in chains),
            "cost": sum(c["cost"] for c in chains),
            "shared_bust_days": len(shared),
            "any_bust_days": len(union),
            "overlap": len(shared) / len(union) if union else float("nan")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--orders", required=True, help="cached LTF sweep orders")
    args = ap.parse_args(argv)

    bars = D.add_time_columns(pd.read_parquet(
        D.PROCESSED_DIR / "nq_1m.parquet",
        filters=[("ts", ">=", pd.Timestamp("2015-12-01", tz="UTC"))])
    ).reset_index(drop=True)
    orders = pd.read_parquet(args.orders)

    runs = {}
    for risk in (250.0, 500.0, 900.0):
        t = simulate(orders, bars, BacktestConfig(risk_per_trade_usd=risk))
        f = t[t["filled"]].sort_values("exit_ts")
        f = f[pd.DatetimeIndex(f["exit_ts"]) >= pd.Timestamp("2016-01-01", tz="UTC")]
        runs[risk] = f.reset_index(drop=True)

    print("HOW LONG AN ACCOUNT LIVES   (trades, unless the column says days)")
    print(f"  {'':<22}{'n':>5}{'25th':>8}{'median':>8}{'75th':>8}{'max':>7}"
          f"{'days':>9}{'busted':>9}")
    for risk, trades in runs.items():
        out = recycle(trades, policy=Policy.IMMEDIATE)
        table = lives(trades, out["timeline"])
        print(f"\n  ${risk:,.0f} a trade "
              f"({out['accounts_bought']} accounts, {out['funded_earned']} XFA, "
              f"{out['payouts']} payouts, net ${out['net']:,.0f})")
        describe(table[table["kind"] == "combine"], "evaluation")
        describe(table[table["kind"] == "funded"], "funded account")
        runs[risk] = (trades, out, table)

    print("\n\nRUNNING SEVERAL AT ONCE   (identical accounts, same signals)")
    for risk in (500.0, 900.0):
        trades, single, _ = runs[risk]
        print(f"\n  ${risk:,.0f} a trade")
        print(f"  {'accounts':>9}{'cost':>11}{'cash':>12}{'net':>12}"
              f"{'per account':>13}")
        for n in (1, 2, 3, 5, 8, 10):
            m = multi_account_cost(single, n)
            note = "  <- XFA cap bites" if m["capped"] else ""
            print(f"  {n:>9}{m['cost']:>11,.0f}{m['cash']:>12,.0f}"
                  f"{m['net']:>12,.0f}{m['net'] / n:>13,.0f}{note}")

    print("\n\nSTARTING THEM A MONTH APART   (do they still die together?)")
    for risk in (500.0, 900.0):
        trades, single, _ = runs[risk]
        st = staggered(trades, 5)
        print(f"  ${risk:,.0f}: {st['chains']} chains, net ${st['net']:,.0f}, "
              f"cost ${st['cost']:,.0f}")
        print(f"      bust days shared by all five: {st['shared_bust_days']} "
              f"of {st['any_bust_days']} ({st['overlap']:.1%})")

    print("\n\nWHICH PRICING PATH")
    print("  Standard = 49(M+P) + 149A;  No-Activation = 85(M+P).")
    print("  So No-Activation wins when 36(M+P) < 149A, i.e. (M+P)/A < 4.14.")
    print("  The threshold is a RATIO. Quoting it as a count of months plus")
    print("  resets is only right when exactly one funded account is earned.\n")
    print(f"  {'risk':>7}{'XFA':>5}{'M+P':>6}{'ratio':>7}{'Standard':>11}"
          f"{'No-Activation':>15}{'cheaper':>16}")
    noaf = XFARules(monthly=85.0, reset=85.0, activation=0.0)
    for risk, (_, single, _) in runs.items():
        lines, earned = single["cost_lines"], single["funded_earned"]
        resets = lines["reset_count"] + lines["free_credits"]
        mp = lines["months"] + lines["reset_count"]
        a = costs(RULES, single["days"], resets, earned)["total"]
        b = costs(noaf, single["days"], resets, earned)["total"]
        ratio = mp / earned if earned else float("inf")
        print(f"  {risk:>7,.0f}{earned:>5}{mp:>6}{ratio:>7.2f}{a:>11,.0f}"
              f"{b:>15,.0f}{'Standard' if a < b else 'No-Activation':>16}")
    print("\n  Both paths scale per account, so N does not move the winner --")
    print("  only how many funded accounts the plan expects to earn does.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    main()
