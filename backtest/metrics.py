"""Performance statistics for a trade log.

Deliberately reports the numbers that reveal a weak edge rather than hide one:
fill rate (unfilled signals are not free), expectancy in R (scale-independent),
profit factor, max drawdown in both dollars and R, and the share of trades whose
exit was ambiguous at 1-minute resolution.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def summarize(trades: pd.DataFrame) -> dict:
    """Headline statistics for a completed run."""
    total_signals = len(trades)
    filled = trades[trades["filled"]] if "filled" in trades else trades
    n = len(filled)
    if n == 0:
        return {"signals": total_signals, "trades": 0, "fill_rate": 0.0}

    r = filled["r_multiple"].dropna()
    pnl = filled["net_pnl"]
    wins = filled[pnl > 0]
    losses = filled[pnl < 0]
    gross_win = float(wins["net_pnl"].sum())
    gross_loss = float(-losses["net_pnl"].sum())
    # The opening balance has to be in the running peak. Without it a series
    # that starts with losses is measured from its own first low rather than
    # from where the account began, understating the drawdown -- over the full
    # 2016-2026 history that was $552 of a $70,556 figure.
    drawdown = _drawdown(pnl)
    r_drawdown = _drawdown(r)

    return {
        "signals": total_signals,
        "trades": n,
        "fill_rate": n / total_signals if total_signals else 0.0,
        "win_rate": len(wins) / n,
        "avg_r": float(r.mean()) if len(r) else float("nan"),
        "median_r": float(r.median()) if len(r) else float("nan"),
        "expectancy_usd": float(pnl.mean()),
        "total_pnl": float(pnl.sum()),
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf"),
        "max_drawdown_usd": float(drawdown.min()) if len(drawdown) else 0.0,
        "max_drawdown_r": float(r_drawdown.min()) if len(r_drawdown) else 0.0,
        "avg_win_usd": float(wins["net_pnl"].mean()) if len(wins) else 0.0,
        "avg_loss_usd": float(losses["net_pnl"].mean()) if len(losses) else 0.0,
        "avg_bars_held": float(filled["bars_held"].mean()),
        "ambiguous_trades": int(filled["ambiguous"].sum()),
        "ambiguous_pct": float(filled["ambiguous"].mean()),
        "exit_reasons": filled["exit_reason"].value_counts().to_dict(),
        "sharpe_per_trade": _sharpe(pnl),
    }


def _drawdown(pnl: pd.Series) -> pd.Series:
    """Running peak-to-valley, measured from an opening balance of zero."""
    equity = pd.concat([pd.Series([0.0]), pnl.reset_index(drop=True).cumsum()],
                       ignore_index=True)
    return equity - equity.cummax()


def _sharpe(pnl: pd.Series) -> float:
    """Per-trade Sharpe. Not annualised — trade frequency varies too much."""
    if len(pnl) < 2 or pnl.std(ddof=1) == 0:
        return float("nan")
    return float(pnl.mean() / pnl.std(ddof=1))


def by_period(trades: pd.DataFrame, freq: str = "YE") -> pd.DataFrame:
    """Break results down by calendar period to expose regime dependence.

    A strategy that made all its money in one year is not a strategy.
    """
    filled = trades[trades["filled"]].copy()
    if filled.empty:
        return pd.DataFrame()
    # Periods carry no timezone; convert explicitly to keep pandas quiet and
    # to make the calendar boundary (UTC) an intentional choice.
    entry = pd.DatetimeIndex(filled["entry_ts"]).tz_convert("UTC").tz_localize(None)
    filled["period"] = entry.to_period({"YE": "Y", "ME": "M", "QE": "Q"}.get(freq, freq))
    grouped = filled.groupby("period").agg(
        trades=("net_pnl", "size"),
        win_rate=("net_pnl", lambda s: float((s > 0).mean())),
        avg_r=("r_multiple", "mean"),
        total_pnl=("net_pnl", "sum"),
    )
    grouped["cumulative_pnl"] = grouped["total_pnl"].cumsum()
    return grouped.reset_index()


def equity_curve(trades: pd.DataFrame) -> pd.DataFrame:
    """Cumulative net P&L and drawdown, indexed by exit time."""
    filled = trades[trades["filled"]].sort_values("exit_ts")
    if filled.empty:
        return pd.DataFrame(columns=["exit_ts", "equity", "drawdown"])
    equity = filled["net_pnl"].cumsum()
    # _drawdown prepends the opening balance, so drop that first row to keep
    # one value per trade.
    return pd.DataFrame({
        "exit_ts": filled["exit_ts"].to_numpy(),
        "equity": equity.to_numpy(),
        "drawdown": _drawdown(filled["net_pnl"]).to_numpy()[1:],
    })


def monte_carlo(trades: pd.DataFrame, runs: int = 2000, seed: int = 0) -> dict:
    """Reshuffle trade order to estimate the drawdown distribution.

    The realised max drawdown is one draw from a distribution; the sequence
    could easily have come out worse. This reports how much worse.
    """
    filled = trades[trades["filled"]]
    pnl = filled["net_pnl"].to_numpy()
    if len(pnl) < 2:
        return {}
    rng = np.random.default_rng(seed)
    worst = np.empty(runs)
    finals = np.empty(runs)
    for k in range(runs):
        shuffled = rng.permutation(pnl)
        equity = np.cumsum(shuffled)
        worst[k] = (equity - np.maximum.accumulate(equity)).min()
        finals[k] = equity[-1]
    return {
        "runs": runs,
        "median_max_dd": float(np.median(worst)),
        "p05_max_dd": float(np.percentile(worst, 5)),
        "p95_max_dd": float(np.percentile(worst, 95)),
        "median_final_pnl": float(np.median(finals)),
        "prob_negative": float((finals < 0).mean()),
    }


def rolling_windows(trades: pd.DataFrame, months: int, step_months: int = 1) -> pd.DataFrame:
    """Performance of every ``months``-long window across the trade history.

    A single recent window looking good proves very little on its own: some
    window always looks best, and the most recent one is the one you were most
    likely to go looking at. Scoring *every* window puts the current one in a
    distribution, so "the last three months had a profit factor of 2.2" can be
    answered with "and so did N% of all three-month windows".

    Windows are stepped by ``step_months`` and scored on the trades that closed
    inside them, reusing :func:`summarize`. Windows with fewer than five trades
    are dropped: a profit factor computed on two trades is noise with a decimal
    point.
    """
    filled = trades[trades["filled"]].copy() if "filled" in trades else trades.copy()
    if filled.empty:
        return pd.DataFrame(columns=["start", "end", "trades", "win_rate",
                                     "profit_factor", "total_pnl", "avg_r"])

    exits = pd.DatetimeIndex(filled["exit_ts"]).tz_convert("UTC")
    filled = filled.assign(_exit=exits).sort_values("_exit")
    first, last = exits.min(), exits.max()

    rows, start = [], first
    while start + pd.DateOffset(months=months) <= last + pd.DateOffset(days=1):
        end = start + pd.DateOffset(months=months)
        window = filled[(filled["_exit"] >= start) & (filled["_exit"] < end)]
        if len(window) >= 5:
            s = summarize(window)
            rows.append({
                "start": start, "end": end, "trades": s["trades"],
                "win_rate": s["win_rate"], "profit_factor": s["profit_factor"],
                "total_pnl": s["total_pnl"], "avg_r": s["avg_r"],
            })
        start = start + pd.DateOffset(months=step_months)

    return pd.DataFrame(rows)


def window_percentile(windows: pd.DataFrame, value: float, column: str = "profit_factor") -> float:
    """Share of windows scoring at or below ``value``, as a percentage.

    A current window at the 95th percentile is genuinely unusual; one at the
    60th is an ordinary good patch.
    """
    if windows.empty:
        return float("nan")
    series = windows[column].replace([float("inf")], float("nan")).dropna()
    if series.empty:
        return float("nan")
    return float((series <= value).mean() * 100.0)
