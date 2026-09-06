"""Bar-by-bar execution simulator with 1-minute fill resolution.

Signals are generated on 5-minute bars but executed against 1-minute bars, and
that asymmetry is the reason this engine exists.

Consider a 5-minute bar whose range spans both your stop and your target.  On
5-minute data there is no way to know which came first, and the usual silent
default — assume the target — turns losing trades into winners across the whole
backtest.  Dropping to 1-minute data resolves the ordering for the overwhelming
majority of cases.

Where a single *1-minute* bar still contains both levels, the ambiguity is real
and cannot be resolved from OHLC data at all.  Rather than pick an answer and
hide it, the engine:

* resolves per ``ambiguity`` policy (``"pessimistic"`` assumes the stop),
* counts the affected trades in ``ambiguous``,

so a run can be repeated under both policies and reported as a band.  If the
two ends disagree materially, the edge is an artifact of the fill assumption.

Costs are charged in ticks and dollars, not ignored: a model that only works
gross of fees is not a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

#: NQ contract specification.
TICK_SIZE = 0.25
TICK_VALUE_NQ = 5.00   # $ per tick, E-mini NQ
TICK_VALUE_MNQ = 0.50  # $ per tick, Micro MNQ


@dataclass(frozen=True)
class BacktestConfig:
    """Execution assumptions. Defaults are deliberately conservative."""

    #: Micro (MNQ) by default. Fixed-risk sizing needs granularity: a $500
    #: budget against a 69-point stop is 0.36 E-mini contracts (untradeable)
    #: but 3 micros. Set TICK_VALUE_NQ to size in E-minis instead.
    tick_value: float = TICK_VALUE_MNQ
    commission_per_round_turn: float = 4.00
    entry_slippage_ticks: float = 0.0   # limit orders fill at their price or better
    exit_slippage_ticks: float = 1.0    # stops are market orders and do slip
    ambiguity: str = "pessimistic"      # or "optimistic"

    #: "fixed_risk" sizes each trade so a stop-out costs roughly
    #: ``risk_per_trade_usd``; "fixed_contracts" always trades ``contracts``.
    #:
    #: Fixed contracts is the wrong default for comparing models. With stops
    #: ranging from 10 to 200 points, a fixed size gives the widest-stop trades
    #: twenty times the weight of the tightest, so the equity curve measures
    #: stop placement rather than edge. That is precisely what sank the first
    #: PO3 run: a 60% win rate with an average loss larger than the average win.
    sizing: str = "fixed_risk"
    risk_per_trade_usd: float = 500.0
    contracts: int = 1
    max_contracts: int = 200

    def __post_init__(self) -> None:
        if self.ambiguity not in ("pessimistic", "optimistic"):
            raise ValueError("ambiguity must be 'pessimistic' or 'optimistic'")
        if self.sizing not in ("fixed_risk", "fixed_contracts"):
            raise ValueError("sizing must be 'fixed_risk' or 'fixed_contracts'")

    def size_for(self, risk_points: float) -> int:
        """Contracts to trade given the stop distance, or 0 to skip the trade.

        Under fixed risk this rounds *down*, so realised risk never exceeds the
        budget. A stop so wide that even one contract breaches the budget
        returns 0 and the setup is recorded as skipped rather than silently
        taken at the wrong size.
        """
        if self.sizing == "fixed_contracts":
            return self.contracts
        if risk_points <= 0:
            return 0
        risk_per_contract = risk_points / TICK_SIZE * self.tick_value
        if risk_per_contract <= 0:
            return 0
        return int(min(self.risk_per_trade_usd // risk_per_contract, self.max_contracts))


ORDER_COLUMNS = [
    "signal_ts", "valid_from", "expires_at", "direction",
    "entry_price", "stop_price", "target_price", "time_exit_ts",
]


def simulate(orders: pd.DataFrame, bars: pd.DataFrame, config: BacktestConfig | None = None) -> pd.DataFrame:
    """Execute limit orders against 1-minute bars.

    Args:
        orders: one row per signal, with the columns in ``ORDER_COLUMNS``.
            ``valid_from`` must be at or after the signal's confirmation time —
            the engine trusts it and does not re-derive causality.
        bars: 1-minute OHLC bars covering the order window, sorted by ``ts``.
        config: execution assumptions.

    Returns:
        One row per order: unfilled orders are retained with ``exit_reason``
        ``"expired"`` so that fill rate stays visible rather than being silently
        dropped from the statistics.
    """
    config = config or BacktestConfig()
    missing = [c for c in ORDER_COLUMNS if c not in orders.columns]
    if missing:
        raise ValueError(f"orders missing columns: {missing}")
    if orders.empty:
        return pd.DataFrame(columns=ORDER_COLUMNS + _RESULT_COLUMNS)

    ts = pd.DatetimeIndex(bars["ts"])  # tz-aware; searchsorted needs the index, not ndarray
    high = bars["high"].to_numpy()
    low = bars["low"].to_numpy()
    open_ = bars["open"].to_numpy()
    close = bars["close"].to_numpy()

    pessimistic = config.ambiguity == "pessimistic"
    slip_in = config.entry_slippage_ticks * TICK_SIZE
    slip_out = config.exit_slippage_ticks * TICK_SIZE

    results = []
    for _, order in orders.iterrows():
        direction = int(order["direction"])
        entry = float(order["entry_price"])
        stop = float(order["stop_price"])
        target = float(order["target_price"])

        start = int(ts.searchsorted(order["valid_from"], side="left"))
        expiry = int(ts.searchsorted(order["expires_at"], side="right"))
        final = int(ts.searchsorted(order["time_exit_ts"], side="right"))
        expiry, final = min(expiry, len(ts)), min(final, len(ts))

        size = config.size_for(abs(entry - stop))
        if size <= 0:
            # Stop too wide to fit the risk budget at even one contract.
            results.append(_unfilled(order, reason="oversized"))
            continue

        fill_index = _find_fill(low, high, start, expiry, entry, direction)
        if fill_index is None:
            results.append(_unfilled(order))
            continue

        entry_price = entry + direction * slip_in
        exit_index, exit_price, reason = _resolve_exit(
            open_, high, low, close, fill_index, final,
            direction, stop, target, slip_out, pessimistic,
        )
        ambiguous = _bar_contains_both(high, low, fill_index, exit_index, stop, target)

        points = (exit_price - entry_price) * direction
        risk = abs(entry_price - stop)
        gross = points / TICK_SIZE * config.tick_value * size
        commission = config.commission_per_round_turn * size
        results.append({
            "filled": True,
            "entry_ts": ts[fill_index],
            "entry_fill": entry_price,
            "exit_ts": ts[exit_index],
            "exit_price": exit_price,
            "exit_reason": reason,
            "points": points,
            "risk_points": risk,
            "r_multiple": points / risk if risk > 0 else float("nan"),
            "gross_pnl": gross,
            "net_pnl": gross - commission,
            "bars_held": exit_index - fill_index,
            "ambiguous": ambiguous,
            "contracts": size,
        })

    out = pd.concat(
        [orders.reset_index(drop=True), pd.DataFrame(results)], axis=1
    )
    return out


_RESULT_COLUMNS = [
    "filled", "entry_ts", "entry_fill", "exit_ts", "exit_price", "exit_reason",
    "points", "risk_points", "r_multiple", "gross_pnl", "net_pnl",
    "bars_held", "ambiguous", "contracts",
]


def _unfilled(order: pd.Series, reason: str = "expired") -> dict:
    return {
        "filled": False, "entry_ts": pd.NaT, "entry_fill": np.nan,
        "exit_ts": pd.NaT, "exit_price": np.nan, "exit_reason": reason,
        "points": np.nan, "risk_points": np.nan, "r_multiple": np.nan,
        "gross_pnl": 0.0, "net_pnl": 0.0, "bars_held": 0, "ambiguous": False,
        "contracts": 0,
    }


def _find_fill(low, high, start, expiry, entry, direction) -> int | None:
    """First bar whose range reaches the limit price."""
    for i in range(start, expiry):
        touched = low[i] <= entry if direction > 0 else high[i] >= entry
        if touched:
            return i
    return None


def _resolve_exit(open_, high, low, close, fill_index, final,
                  direction, stop, target, slip_out, pessimistic):
    """Walk forward from the fill until stop, target or the time exit."""
    for i in range(fill_index, final):
        if direction > 0:
            hit_stop = low[i] <= stop
            hit_target = high[i] >= target
        else:
            hit_stop = high[i] >= stop
            hit_target = low[i] <= target

        if hit_stop and hit_target:
            # Both levels inside one 1-minute bar: unresolvable from OHLC.
            if pessimistic:
                return i, stop - direction * slip_out, "stop_ambiguous"
            return i, target, "target_ambiguous"
        if hit_stop:
            # A gap through the stop fills at the open, not at the stop price.
            gapped = open_[i] < stop if direction > 0 else open_[i] > stop
            price = open_[i] if gapped else stop - direction * slip_out
            return i, price, "stop"
        if hit_target:
            return i, target, "target"

    # Neither level was reached before the session cutoff: flatten at the close
    # of the last bar in the window, with the same slippage a market order pays.
    last = max(fill_index, final - 1)
    return last, float(close[last]) - direction * slip_out, "time_exit"


def _bar_contains_both(high, low, fill_index, exit_index, stop, target) -> bool:
    """Whether any held bar spanned both stop and target."""
    lo = np.minimum(stop, target)
    hi = np.maximum(stop, target)
    seg_high = high[fill_index : exit_index + 1]
    seg_low = low[fill_index : exit_index + 1]
    return bool(((seg_high >= hi) & (seg_low <= lo)).any())
