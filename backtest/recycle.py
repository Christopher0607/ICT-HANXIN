"""Buying evaluations, passing them, getting paid, and losing the account.

``scripts/quarterly_report.py`` answers "what does it cost to reach a funded
account": it walks the real sequence of trades, buys a fresh evaluation after
every breach, and stops at the first pass. That is where the money starts, not
where it ends, so it cannot say whether any of this is a business.

This module keeps going. An account that passes becomes an Express Funded
Account, trades on, pays out when it is eligible, and eventually dies; then the
cycle starts again. What comes out is cash actually withdrawn, against cash
actually spent.

**Money left in a funded account is not money.** It is the single thing this
model exists to say: a run that ends with $2,800 of unrealised balance and an
account that then breaches has earned nothing. Every figure here is either
withdrawn or spent.

The rule that decides most of it
--------------------------------
An XFA's loss limit trails the *highest end-of-day balance* and never moves
down -- and once the balance reaches the limit's own size, the limit locks at
$0 permanently. A 50K XFA that reaches +$2,000 can never again fall below
breakeven; it can only give back its profits. Evaluations have no such rule:
there the target and the limit race each other and one of them always wins.

So an XFA's whole life turns on whether it reaches +$2,000 before it falls
$2,000 from its peak, and a withdrawal taken before that point pushes the
balance back down while leaving the high-water mark where it was. Taking money
early is not free: it is paid for in the insurance you then never buy. Both
orderings are modelled -- see :class:`Policy`.

Rules are Topstep's 50K figures, read from their help centre on 2026-09-14 and
recorded with their sources in :class:`XFARules`. They change; the dataclass is
there so a change is one edit rather than a hunt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from bridge.guards import scaling_tier


class Policy(Enum):
    """When to take money out of a funded account.

    The choice is not cosmetic. A payout lowers the balance but not the
    high-water mark the loss limit trails, so withdrawing before the limit
    locks keeps the account killable for longer -- possibly forever, if every
    payout resets the climb. LOCK_FIRST buys the insurance before taking
    anything out; IMMEDIATE takes the cash and stays exposed.
    """

    IMMEDIATE = "immediate"
    LOCK_FIRST = "lock_first"


@dataclass(frozen=True)
class XFARules:
    """Topstep 50K, from their help centre on 2026-09-14.

    Sources:
      - help.topstep.com/en/articles/8284233-topstep-payout-policy
      - topstep.com/express-funded-account-rules
      - help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit
    """

    #: Evaluation: reach this and the account passes.
    profit_target: float = 3000.0
    #: Evaluation: fall this far from the peak and it is over.
    loss_limit: float = 2000.0
    #: Evaluation contract ceiling for a 50K, matching bridge PRESETS.
    combine_max_contracts: int = 50

    #: XFA: same size of trailing limit, but it locks (see below).
    funded_loss_limit: float = 2000.0
    #: Balance at which the trailing limit locks at $0 and stops following.
    #: Topstep states this as "once your balance reaches $2,000, the MLL locks
    #: at $0 permanently" for a 50K.
    mll_locks_at: float = 2000.0

    #: A winning day needs this much net profit to count toward a payout.
    winning_day_min: float = 150.0
    #: Winning days needed per payout. They need not be consecutive, and the
    #: count restarts at zero after each payout.
    winning_days_required: int = 5
    #: A payout may take at most this share of the balance...
    payout_share: float = 0.50
    #: ...and at most this many dollars. Topstep's 50K cap is $2,000; adding
    #: the optional Daily Loss Limit at purchase doubles it, and this plan
    #: keeps the Responsible Trading Advantage on, so $4,000 applies.
    payout_cap: float = 4000.0
    #: Trader's share. The "100% of your first $10,000" arrangement is limited
    #: to accounts on the new dashboard before 2026-01-12, which this one will
    #: not be.
    profit_split: float = 0.90

    #: Stop trading rather than breach -- the live guard in bridge/guards.py,
    #: which blocks a trade when the room left is under ``safety_mult`` times
    #: the risk about to be taken. docs/GO_LIVE.md runs it OFF on an evaluation
    #: and ON once funded, because stopping short of a target neither passes
    #: nor busts while the monthly fee runs either way.
    #:
    #: A stopped account is stuck, not saved: it takes no more trades, so its
    #: balance never moves, so the condition that stopped it stays true. The
    #: model retires it and buys another rather than pretending it recovers.
    combine_guard: bool = False
    funded_guard: bool = False
    safety_mult: float = 1.5

    #: Standard path pricing, matching PATHS["Standard"] in the quarterly
    #: report. Every monthly rebill banks one free reset credit.
    monthly: float = 49.0
    reset: float = 49.0
    #: Charged once per funded account EARNED -- so it is charged again every
    #: time a dead XFA is replaced by a new pass.
    activation: float = 149.0
    api_monthly: float = 14.50


@dataclass
class _Account:
    """One evaluation or funded account, while it is alive."""

    balance: float = 0.0
    peak: float = 0.0
    winning_days: int = 0

    def floor(self, limit: float, locks_at: float | None) -> float:
        """The balance at which this account is closed.

        For an evaluation the limit simply trails the peak. For a funded
        account the same expression already produces the lock: once the peak
        reaches the limit's size the floor would rise above zero, and the rule
        is that it never does.
        """
        trailing = self.peak - limit
        return trailing if locks_at is None else min(trailing, 0.0)


def _months(days: float) -> int:
    """Billing months covered by a run of this many days."""
    return max(1, int(math.ceil(days / 30.4)))


def costs(rules: XFARules, days: float, resets: int, activations: int) -> dict:
    """Itemised spend. ``scripts/quarterly_report.py``'s ``path_cost`` is this
    same rule for a run that passes once; tests/test_recycle.py asserts the two
    agree on that case so the duplication cannot drift.

    Every monthly rebill banks one free reset credit, so the first month earns
    none and each later month covers one reset.
    """
    months = _months(days)
    free = max(0, months - 1)
    paid = max(0, resets - free)
    lines = {
        "subscription": months * rules.monthly,
        "paid_resets": paid * rules.reset,
        "api_access": months * rules.api_monthly,
        "activation": activations * rules.activation,
    }
    return {"months": months, "free_credits": free, "reset_count": paid,
            **lines, "total": float(sum(lines.values()))}


def recycle(trades: pd.DataFrame, rules: XFARules = XFARules(),
            policy: Policy = Policy.LOCK_FIRST,
            funded_scale: float = 1.0, risk_usd: float | None = None) -> dict:
    """Run one real sequence of trades through buy / pass / earn / lose.

    ``trades`` must already be simulated at the evaluation's risk per trade and
    carry ``net_pnl``, ``contracts``, ``exit_ts`` and ``trading_date``.

    ``funded_scale`` re-sizes the funded phase relative to the evaluation --
    0.28 puts a $900 evaluation onto $250 a trade once funded, which is the
    two-stage plan in docs/GO_LIVE.md. P&L and commission both scale with
    contracts, so scaling net_pnl is the same thing as re-running the sizing.

    ``risk_usd`` is the dollars risked per evaluation trade, and is required
    only when a guard is on: the guard's test is about the room left against
    the size of the next trade, so it cannot be evaluated without knowing it.

    Returns cash withdrawn, money spent, and the counts behind both.
    """
    if trades.empty:
        raise ValueError("no trades to recycle")
    # The loss limit trails the highest END-OF-DAY balance. Walking trade by
    # trade is only the same thing while a day holds at most one trade, which
    # is what this strategy generates. If that ever stops being true the whole
    # model needs rewriting, so it is an assertion rather than a comment.
    per_day = trades.groupby("trading_date").size().max()
    if per_day > 1:
        raise ValueError(
            f"{per_day} trades on one day: the end-of-day loss limit cannot be "
            "walked trade by trade")

    def mark(ts, event, at):
        """Record where the money stands, not just that something happened.

        A total says the run made money; this says when. The two are different
        questions and only the second one is survivable or not.

        ``at`` is the index of the trade that caused the event, which is what
        lets a caller reconstruct how many trades each account lived for --
        recoverable from nothing else, since accounts are not objects that
        outlive the loop.
        """
        spent = costs(rules, (ts - first).total_seconds() / 86400,
                      resets, activations)["total"]
        timeline.append({"ts": ts, "event": event, "trade": at, "cash": cash,
                         "cost": spent, "net": cash - spent})

    if (rules.combine_guard or rules.funded_guard) and risk_usd is None:
        raise ValueError("a guard needs risk_usd: its test compares the room "
                         "left against the size of the next trade")

    account, funded = _Account(), False
    combine_busts = funded_busts = resets = activations = payouts = 0
    retired_combine = retired_funded = 0
    bust_streak = worst_streak = 0
    cash = gross = 0.0
    first = trades["entry_ts"].iloc[0]
    last_cash_ts, worst_dry = first, 0.0
    funded_days = 0.0
    funded_since = None
    timeline: list[dict] = []

    for at, (pnl, contracts, exit_ts) in enumerate(
            zip(trades["net_pnl"], trades["contracts"], trades["exit_ts"])):
        # The backtest sizes on risk alone; neither ceiling exists there.
        cap = (scaling_tier(account.balance) if funded
               else rules.combine_max_contracts)
        scale = min(int(contracts), cap) / contracts if contracts else 0.0
        day = float(pnl) * scale * (funded_scale if funded else 1.0)

        # The guard looks at the room left BEFORE the trade, which is the
        # only order in which it could ever prevent one.
        guarded = rules.funded_guard if funded else rules.combine_guard
        if guarded:
            limit = rules.funded_loss_limit if funded else rules.loss_limit
            locks = rules.mll_locks_at if funded else None
            planned = risk_usd * (funded_scale if funded else 1.0)
            if account.balance - account.floor(limit, locks) < planned * rules.safety_mult:
                if funded:
                    retired_funded += 1
                    funded_days += (exit_ts - funded_since).total_seconds() / 86400
                    funded, funded_since = False, None
                else:
                    retired_combine += 1
                resets += 1
                bust_streak += 1
                worst_streak = max(worst_streak, bust_streak)
                account = _Account()
                mark(exit_ts, "retired", at)
                continue

        account.balance += day
        account.peak = max(account.peak, account.balance)

        if not funded:
            if account.balance >= rules.profit_target:
                activations += 1
                funded, account, funded_since = True, _Account(), exit_ts
                mark(exit_ts, "passed", at)
                continue
            if account.balance <= account.floor(rules.loss_limit, None):
                combine_busts += 1
                resets += 1
                bust_streak += 1
                worst_streak = max(worst_streak, bust_streak)
                account = _Account()
                mark(exit_ts, "combine_bust", at)
            continue

        # --- funded ---
        if day >= rules.winning_day_min:
            account.winning_days += 1

        if account.balance <= account.floor(rules.funded_loss_limit,
                                            rules.mll_locks_at):
            funded_busts += 1
            funded_days += (exit_ts - funded_since).total_seconds() / 86400
            # A dead XFA sends you back through an evaluation, which is a
            # reset against the same subscription.
            resets += 1
            bust_streak += 1
            worst_streak = max(worst_streak, bust_streak)
            funded, account, funded_since = False, _Account(), None
            mark(exit_ts, "funded_bust", at)
            continue

        locked = account.peak >= rules.mll_locks_at
        eligible = (account.winning_days >= rules.winning_days_required
                    and account.balance > 0
                    and (locked or policy is Policy.IMMEDIATE))
        if eligible:
            amount = min(account.balance * rules.payout_share, rules.payout_cap)
            # The balance falls; the high-water mark the limit trails does not.
            # That is what makes an early payout expensive.
            account.balance -= amount
            account.winning_days = 0
            gross += amount
            cash += amount * rules.profit_split
            payouts += 1
            bust_streak = 0
            worst_dry = max(worst_dry,
                            (exit_ts - last_cash_ts).total_seconds() / 86400)
            last_cash_ts = exit_ts
            mark(exit_ts, "payout", at)

    end = trades["exit_ts"].iloc[-1]
    if funded and funded_since is not None:
        funded_days += (end - funded_since).total_seconds() / 86400
    worst_dry = max(worst_dry, (end - last_cash_ts).total_seconds() / 86400)

    days = (end - first).total_seconds() / 86400
    spend = costs(rules, days, resets, activations)
    net = cash - spend["total"]
    return {
        "days": days,
        # One evaluation is bought up front; every bust buys another.
        "accounts_bought": (1 + combine_busts + funded_busts
                            + retired_combine + retired_funded),
        "combine_busts": combine_busts,
        # Stopped by the guard rather than breached: no bust, no pass, and the
        # reset still bought.
        "retired_combine": retired_combine,
        "retired_funded": retired_funded,
        "funded_earned": activations,
        "funded_busts": funded_busts,
        "funded_days": funded_days,
        "payouts": payouts,
        "gross_payout": gross,
        "cash": cash,
        "cost": spend["total"],
        "cost_lines": spend,
        "net": net,
        "roi": net / spend["total"] if spend["total"] else float("nan"),
        "longest_bust_streak": worst_streak,
        "longest_dry_days": worst_dry,
        # What is still sitting in the account at the end, and therefore what
        # the run did NOT earn.
        "unrealised": account.balance if funded else 0.0,
        "timeline": timeline,
    }
