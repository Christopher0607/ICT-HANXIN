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

#: All-in round-turn cost per contract (commission + exchange + NFA fees).
#: These must be paired with the matching tick value. Charging the E-mini rate
#: on micro contracts is a silent and severe error: fixed-risk sizing buys ten
#: times as many micros as e-minis for the same risk, so the per-contract fee is
#: multiplied by ten while the tick value is divided by ten -- a hundred-fold
#: overstatement of costs that falls hardest on tight-stop, high-frequency
#: models and can turn a profitable one into a loser.
COMMISSION_NQ = 4.00
COMMISSION_MNQ = 1.24


@dataclass(frozen=True)
class BacktestConfig:
    """Execution assumptions. Defaults are deliberately conservative."""

    #: Micro (MNQ) by default. Fixed-risk sizing needs granularity: a $500
    #: budget against a 69-point stop is 0.36 E-mini contracts (untradeable)
    #: but 3 micros. Set TICK_VALUE_NQ to size in E-minis instead.
    tick_value: float = TICK_VALUE_MNQ
    #: Paired with tick_value above. Use COMMISSION_NQ with TICK_VALUE_NQ.
    commission_per_round_turn: float = COMMISSION_MNQ
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
    #: How a resting limit order is deemed filled.
    #:
    #: "touch" counts the order filled the moment price reaches the limit. That
    #: is optimistic: at the limit price you are last in the queue, and in a
    #: fast market the level can be tagged and abandoned with your order unfilled.
    #: "through" requires price to trade a tick beyond the limit, which is the
    #: conservative reading. The gap between them is a high-frequency model's
    #: single largest execution risk, so it is worth running both.
    entry_fill_mode: str = "touch"

    #: Which way price has to move to fill the entry.
    #:
    #: Every strategy here until now entered on a retracement, so a long fills
    #: when price falls to the level -- a limit. A breakout model does the
    #: opposite: it buys above the market and fills when price rises through
    #: the level, which is a stop. The two are not interchangeable. Filling a
    #: stop order with the limit rule would have it fill on days price never
    #: went there, and every one of those is a trade that did not happen.
    #:
    #: A stop entry that gaps is filled at the bar's open rather than at the
    #: trigger, because a market order cannot be filled better than the first
    #: price available after it triggers.
    entry_side: str = "limit"

    #: Refuse a resting limit the market has already passed.
    #:
    #: A sell limit below the market is not a limit order -- it is marketable,
    #: and the exchange fills it at the prevailing price, not at the level the
    #: setup was priced from. Filling it at the limit invents a price the
    #: market had already left behind, and sizes the trade off a stop distance
    #: that no longer applies.
    #:
    #: In the LTF sweep data every one of these was already marketable on the
    #: order's FIRST live bar -- none was a gap. That is not an execution
    #: detail, it is the setup's premise failing: the model enters on a
    #: retracement into the gap, and price was already through it.
    #: 5.3% of out-of-sample fills and 6.6% of development fills, and they lost
    #: at a 21% win rate in development against 50% for the rest.
    #:
    #: Set False to reproduce the old numbers.
    skip_marketable_entries: bool = True

    #: The same question for the profit target, which is also a resting limit.
    #: It matters most exactly where the entry assumption matters least: a tight
    #: target is touched often and briefly, so a sweep that concludes "smaller
    #: targets are better" may be measuring the fill assumption rather than the
    #: market. The stop is deliberately excluded -- it is a market order, and a
    #: touch really does trigger it.
    exit_fill_mode: str = "touch"

    #: Take this share of the position off at ``target_price`` and let the rest
    #: run to ``runner_target`` with its stop moved to
    #: ``entry + runner_stop_offset``. Zero -- the default -- is the single
    #: exit every existing strategy uses, and leaves their results untouched.
    #:
    #: Contracts are whole, so the first leg is rounded DOWN and the runner
    #: keeps the remainder. A one-contract position cannot be halved, and comes
    #: off in one piece at the first target instead; ``scaled_out`` in the
    #: results says which trades that happened to rather than hiding it inside
    #: an average.
    scale_out_fraction: float = 0.0
    #: Points from the entry, in the trade's direction, where the runner's stop
    #: sits once the first target is hit.
    runner_stop_offset: float = 0.0

    sizing: str = "fixed_risk"
    risk_per_trade_usd: float = 500.0
    contracts: int = 1
    max_contracts: int = 200

    def __post_init__(self) -> None:
        if self.ambiguity not in ("pessimistic", "optimistic"):
            raise ValueError("ambiguity must be 'pessimistic' or 'optimistic'")
        if self.sizing not in ("fixed_risk", "fixed_contracts"):
            raise ValueError("sizing must be 'fixed_risk' or 'fixed_contracts'")
        if self.entry_fill_mode not in ("touch", "through"):
            raise ValueError("entry_fill_mode must be 'touch' or 'through'")
        if self.exit_fill_mode not in ("touch", "through"):
            raise ValueError("exit_fill_mode must be 'touch' or 'through'")
        if self.entry_side not in ("limit", "stop"):
            raise ValueError("entry_side must be 'limit' or 'stop'")
        if not 0.0 <= self.scale_out_fraction < 1.0:
            raise ValueError("scale_out_fraction must be in [0, 1)")

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

    # An optional column: strategies without a second leg never carry it.
    has_runner = "runner_target" in orders.columns

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

        runner_target = (float(order["runner_target"])
                         if has_runner and pd.notna(order["runner_target"])
                         else None)

        size = config.size_for(abs(entry - stop))
        if size <= 0:
            # Stop too wide to fit the risk budget at even one contract.
            results.append(_unfilled(order, reason="oversized"))
            continue

        # Only a limit can be marketable in the sense this guard means. A stop
        # sitting below the market is simply a breakout that already happened,
        # and the fill price below handles it by paying the open.
        if (config.skip_marketable_entries and config.entry_side == "limit"
                and start < expiry):
            opened = open_[start]
            marketable = (opened < entry) if direction > 0 else (opened > entry)
            if marketable:
                # Price is already through the level this setup was priced
                # from, so the retracement it waits for never happened.
                results.append(_unfilled(order, reason="premise_failed"))
                continue

        fill_index = _find_fill(low, high, start, expiry, entry, direction,
                                config.entry_fill_mode, config.entry_side)
        if fill_index is None:
            results.append(_unfilled(order))
            continue

        entry_price = entry + direction * slip_in
        if config.entry_side == "stop":
            # A triggered market order cannot be filled better than the first
            # price after it triggers. When the bar opens beyond the trigger,
            # that price is the open, not the level.
            opened = open_[fill_index]
            entry_price = (max(entry_price, opened) if direction > 0
                           else min(entry_price, opened))
        target_edge = TICK_SIZE if config.exit_fill_mode == "through" else 0.0
        # Whole contracts: half of one is nothing, so a one-lot position cannot
        # be scaled and comes off in one piece at the first target.
        first_size = (int(size * config.scale_out_fraction)
                      if config.scale_out_fraction > 0 and runner_target is not None
                      else 0)

        runner = None
        if first_size > 0:
            runner_stop = entry_price + direction * config.runner_stop_offset
            (first_index, first_price, first_reason), runner = _resolve_scaled(
                open_, high, low, close, fill_index, final, direction,
                stop, target, runner_stop, runner_target, slip_out,
                pessimistic, target_edge)
        else:
            first_index, first_price, first_reason = _resolve_exit(
                open_, high, low, close, fill_index, final,
                direction, stop, target, slip_out, pessimistic, target_edge)

        ambiguous = _bar_contains_both(high, low, fill_index, first_index, stop, target)
        first_points = (first_price - entry_price) * direction

        if runner is not None:
            exit_index, runner_price, runner_reason = runner
            runner_size = size - first_size
            runner_points = (runner_price - entry_price) * direction
            # Weighted so that points * size is still the total points won, and
            # every figure downstream keeps meaning what it meant.
            points = (first_points * first_size
                      + runner_points * runner_size) / size
            exit_price, reason = runner_price, runner_reason
        else:
            exit_index, exit_price, reason = first_index, first_price, first_reason
            runner_points, runner_reason = float("nan"), None
            points = first_points

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
            # The claimed win rate for a scale-out model is the first target's
            # hit rate, so it has to be visible on its own rather than blended
            # into an average that no strategy description ever quotes.
            "tp1_hit": first_reason.startswith("target"),
            "scaled_out": runner is not None,
            "runner_points": runner_points,
            "runner_exit_reason": runner_reason,
        })

    # Strategies already carry their own risk_points; keep theirs and drop the
    # duplicate rather than emitting two identically named columns, which makes
    # every downstream lookup return a DataFrame instead of a value.
    computed = pd.DataFrame(results)
    overlap = [c for c in computed.columns if c in orders.columns]
    return pd.concat(
        [orders.reset_index(drop=True), computed.drop(columns=overlap)], axis=1
    )


_RESULT_COLUMNS = [
    "filled", "entry_ts", "entry_fill", "exit_ts", "exit_price", "exit_reason",
    "points", "risk_points", "r_multiple", "gross_pnl", "net_pnl",
    "bars_held", "ambiguous", "contracts",
    "tp1_hit", "scaled_out", "runner_points", "runner_exit_reason",
]


def _unfilled(order: pd.Series, reason: str = "expired") -> dict:
    return {
        "filled": False, "entry_ts": pd.NaT, "entry_fill": np.nan,
        "exit_ts": pd.NaT, "exit_price": np.nan, "exit_reason": reason,
        "points": np.nan, "risk_points": np.nan, "r_multiple": np.nan,
        "gross_pnl": 0.0, "net_pnl": 0.0, "bars_held": 0, "ambiguous": False,
        "contracts": 0, "tp1_hit": False, "scaled_out": False,
        "runner_points": np.nan, "runner_exit_reason": None,
    }


def _find_fill(low, high, start, expiry, entry, direction, mode="touch",
               side="limit") -> int | None:
    """First bar at which the resting order is deemed filled.

    A limit is reached by price coming back to it; a stop is reached by price
    going through it. Which one applies is the difference between a
    retracement model and a breakout model, and using the wrong one fills
    trades on days the market never offered them.
    """
    edge = 0.0 if mode == "touch" else TICK_SIZE
    for i in range(start, expiry):
        if side == "limit":
            reached = (low[i] <= entry - edge) if direction > 0 else (high[i] >= entry + edge)
        else:
            reached = (high[i] >= entry + edge) if direction > 0 else (low[i] <= entry - edge)
        if reached:
            return i
    return None


def _resolve_exit(open_, high, low, close, fill_index, final,
                  direction, stop, target, slip_out, pessimistic, target_edge=0.0):
    """Walk forward from the fill until stop, target or the time exit."""
    for i in range(fill_index, final):
        if direction > 0:
            hit_stop = low[i] <= stop
            hit_target = high[i] >= target + target_edge
        else:
            hit_stop = high[i] >= stop
            hit_target = low[i] <= target - target_edge

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


def _resolve_scaled(open_, high, low, close, fill_index, final, direction,
                    stop, target, runner_stop, runner_target, slip_out,
                    pessimistic, target_edge=0.0):
    """Two legs: part off at ``target``, the rest to ``runner_target``.

    The first leg is resolved exactly as a single exit would be. Only if it
    reaches the target does a second leg exist, and it starts on that same bar
    -- price is already there, so the runner's stop can be hit before the bar
    closes and pretending otherwise would give the runner a free bar.

    Returns ``(first_leg, runner)``, each ``(index, price, reason)``, with
    ``runner`` None when the position came off in one piece.
    """
    first = _resolve_exit(open_, high, low, close, fill_index, final,
                          direction, stop, target, slip_out, pessimistic,
                          target_edge)
    if not first[2].startswith("target"):
        return first, None

    tp1_index = first[0]
    runner = _resolve_exit(open_, high, low, close, tp1_index, final,
                           direction, runner_stop, runner_target, slip_out,
                           pessimistic, target_edge)
    return first, runner


def _bar_contains_both(high, low, fill_index, exit_index, stop, target) -> bool:
    """Whether any held bar spanned both stop and target."""
    lo = np.minimum(stop, target)
    hi = np.maximum(stop, target)
    seg_high = high[fill_index : exit_index + 1]
    seg_low = low[fill_index : exit_index + 1]
    return bool(((seg_high >= hi) & (seg_low <= lo)).any())
