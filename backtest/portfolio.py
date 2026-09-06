"""Combining strategies into a single account.

Running seven strategies side by side answers "which idea is best".  It does
not answer "what would I have made", because one person with one account cannot
take every signal: two strategies firing on the same morning are one trade, not
two.

This module builds the account you could actually have traded — one position at
a time, the earliest signal of the day wins — and measures how much genuine
diversification the set provides.  If the strategies mostly fire on the same
days, the apparent spread of seven models is an illusion and the portfolio
curve will look like whichever one triggers earliest.
"""

from __future__ import annotations

import pandas as pd


def combine(orders_by_strategy: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One account, one trade per day: the earliest-triggering signal wins.

    Ties are broken by strategy name so the result is deterministic rather than
    dependent on dictionary ordering.
    """
    frames = [df.assign(strategy=name)
              for name, df in orders_by_strategy.items() if not df.empty]
    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(
        ["trading_date", "valid_from", "strategy"], kind="mergesort"
    )
    return combined.groupby("trading_date", as_index=False, sort=True).first()


def overlap_matrix(orders_by_strategy: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Fraction of trading days on which each pair of strategies both signal.

    Read the diagonal as each strategy's own active-day count. High off-diagonal
    values mean the models are re-describing the same setups.
    """
    day_sets = {
        name: set(df["trading_date"]) for name, df in orders_by_strategy.items()
        if not df.empty
    }
    names = sorted(day_sets)
    out = pd.DataFrame(index=names, columns=names, dtype="float64")
    for a in names:
        for b in names:
            union = day_sets[a] | day_sets[b]
            out.loc[a, b] = len(day_sets[a] & day_sets[b]) / len(union) if union else 0.0
    return out


def contribution(portfolio_trades: pd.DataFrame) -> pd.DataFrame:
    """Which strategies actually got to trade, and what they contributed."""
    if portfolio_trades.empty:
        return pd.DataFrame()
    filled = portfolio_trades[portfolio_trades["filled"]]
    if filled.empty:
        return pd.DataFrame()
    return (
        filled.groupby("strategy")
        .agg(trades=("net_pnl", "size"),
             win_rate=("net_pnl", lambda s: float((s > 0).mean())),
             avg_r=("r_multiple", "mean"),
             total_pnl=("net_pnl", "sum"))
        .sort_values("total_pnl", ascending=False)
        .reset_index()
    )
