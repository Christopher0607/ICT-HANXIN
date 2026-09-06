"""Control baselines: is the edge in the concepts, or in the timing?

A strategy result means nothing on its own.  NQ has a strong upward drift and a
pronounced intraday profile, so a model that simply gets long at the New York
open during a bull market will look profitable without containing any insight.

Two controls answer the question directly:

* **Random direction** — keep the setups, the entry prices, the stops, the
  targets and the exit times exactly as the strategy produced them, and only
  randomise the *direction*.  If this scores like the real thing, then the ICT
  reasoning about which way price will go is contributing nothing and the
  results are coming from the risk geometry alone.
* **Always long at the open** — buy the New York open every day with the same
  stop distance.  This is the drift benchmark any intraday long-biased model
  has to beat to justify its complexity.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import BacktestConfig, simulate
from .metrics import summarize


def random_direction_control(
    orders: pd.DataFrame, bars: pd.DataFrame,
    config: BacktestConfig | None = None, runs: int = 200, seed: int = 0,
) -> dict:
    """Re-run the same setups with randomised direction, ``runs`` times.

    Stops and targets are reflected around the entry so the risk and reward
    distances are preserved when a trade flips side.
    """
    if orders.empty:
        return {}
    rng = np.random.default_rng(seed)
    entry = orders["entry_price"].to_numpy()
    risk = np.abs(entry - orders["stop_price"].to_numpy())
    reward = np.abs(orders["target_price"].to_numpy() - entry)

    totals, win_rates, avg_rs = [], [], []
    for _ in range(runs):
        flipped = orders.copy()
        sign = rng.choice([-1, 1], size=len(orders))
        flipped["direction"] = sign
        flipped["stop_price"] = entry - sign * risk
        flipped["target_price"] = entry + sign * reward
        stats = summarize(simulate(flipped, bars, config))
        if stats.get("trades"):
            totals.append(stats["total_pnl"])
            win_rates.append(stats["win_rate"])
            avg_rs.append(stats["avg_r"])

    if not totals:
        return {}
    totals = np.array(totals)
    return {
        "runs": len(totals),
        "median_total_pnl": float(np.median(totals)),
        "p05_total_pnl": float(np.percentile(totals, 5)),
        "p95_total_pnl": float(np.percentile(totals, 95)),
        "median_win_rate": float(np.median(win_rates)),
        "median_avg_r": float(np.median(avg_rs)),
    }


def percentile_of(value: float, control: dict) -> float | None:
    """Where the strategy's result falls inside the control distribution.

    Roughly 50 means the strategy is indistinguishable from coin-flip direction.
    """
    if not control:
        return None
    lo, mid, hi = control["p05_total_pnl"], control["median_total_pnl"], control["p95_total_pnl"]
    if value <= lo:
        return 5.0
    if value >= hi:
        return 95.0
    if value <= mid:
        return 5.0 + 45.0 * (value - lo) / (mid - lo) if mid > lo else 50.0
    return 50.0 + 45.0 * (value - mid) / (hi - mid) if hi > mid else 50.0


def randomized_totals(
    orders: pd.DataFrame, bars: pd.DataFrame,
    config: BacktestConfig | None = None, runs: int = 100, seed: int = 0,
) -> np.ndarray:
    """Total P&L of ``runs`` direction-randomised copies of these setups.

    Same setups, same entries, same risk and reward distances — only the
    direction is a coin flip. Each value is therefore a draw from "what this
    strategy's machinery produces with no directional edge at all".
    """
    if orders.empty:
        return np.array([])
    rng = np.random.default_rng(seed)
    entry = orders["entry_price"].to_numpy()
    risk = np.abs(entry - orders["stop_price"].to_numpy())
    reward = np.abs(orders["target_price"].to_numpy() - entry)

    totals = []
    for _ in range(runs):
        flipped = orders.copy()
        sign = rng.choice([-1, 1], size=len(orders))
        flipped["direction"] = sign
        flipped["stop_price"] = entry - sign * risk
        flipped["target_price"] = entry + sign * reward
        stats = summarize(simulate(flipped, bars, config))
        totals.append(stats.get("total_pnl", 0.0))
    return np.array(totals)


def best_of_n_null(null_totals: dict[str, np.ndarray], actual: dict[str, float]) -> dict:
    """Is the best strategy better than the best of N strategies with no edge?

    Testing seven models and reporting the winner is itself a form of
    overfitting: the maximum of seven noisy draws is biased upward even when
    every draw has zero expectation. This quantifies that bias directly.

    For each simulation round, take the best of the seven randomised
    strategies. That yields the distribution of "how good the apparent winner
    looks when nothing has an edge". If the real winner does not clear it, the
    winner is selection noise rather than a discovery.
    """
    names = [n for n, arr in null_totals.items() if len(arr)]
    if not names or not actual:
        return {}

    depth = min(len(null_totals[n]) for n in names)
    stacked = np.vstack([null_totals[n][:depth] for n in names])
    per_round_best = stacked.max(axis=0)

    best_name = max(actual, key=actual.get)
    best_value = actual[best_name]

    return {
        "n_strategies": len(names),
        "rounds": int(depth),
        "best_strategy": best_name,
        "best_actual_pnl": float(best_value),
        "null_best_median": float(np.median(per_round_best)),
        "null_best_p95": float(np.percentile(per_round_best, 95)),
        "null_single_median": float(np.median(stacked)),
        # Share of rounds where pure noise produced a "winner" at least this good.
        "p_value": float((per_round_best >= best_value).mean()),
        "survives": bool((per_round_best >= best_value).mean() < 0.05),
    }
