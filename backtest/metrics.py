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
    equity = pnl.cumsum()
    drawdown = equity - equity.cummax()
    r_equity = r.cumsum()
    r_drawdown = r_equity - r_equity.cummax()

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
    return pd.DataFrame({
        "exit_ts": filled["exit_ts"].to_numpy(),
        "equity": equity.to_numpy(),
        "drawdown": (equity - equity.cummax()).to_numpy(),
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
