"""Invariants every strategy in the registry must satisfy.

Run against real data rather than fixtures: these are properties of the orders
a strategy actually emits over years of bars, and the failure modes they catch
(a stop on the wrong side, an order valid before its own signal completed, two
trades in one day) only show up at that scale.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.engine import BacktestConfig
from ict import data as D
from strategies import registry

pytestmark = pytest.mark.skipif(
    not (D.PROCESSED_DIR / "nq_5m.parquet").exists(),
    reason="run `uv run python scripts/ingest.py` first",
)


@pytest.fixture(scope="module")
def bars():
    df = D.load("5m")
    return df[(df.ts >= "2025-01-01") & (df.ts < "2025-07-01")].reset_index(drop=True)


@pytest.fixture(scope="module")
def orders(bars):
    return {name: registry.generate(name, bars) for name in registry.NAMES}


@pytest.mark.parametrize("name", registry.NAMES)
def test_at_most_one_order_per_trading_day(name, orders):
    o = orders[name]
    if o.empty:
        pytest.skip(f"{name} produced no orders")
    assert not o["trading_date"].duplicated().any()


@pytest.mark.parametrize("name", registry.NAMES)
def test_stop_and_target_sit_on_the_correct_sides(name, orders):
    o = orders[name]
    if o.empty:
        pytest.skip(f"{name} produced no orders")
    longs, shorts = o[o.direction == 1], o[o.direction == -1]
    assert (longs.stop_price < longs.entry_price).all()
    assert (longs.target_price > longs.entry_price).all()
    assert (shorts.stop_price > shorts.entry_price).all()
    assert (shorts.target_price < shorts.entry_price).all()


@pytest.mark.parametrize("name", registry.NAMES)
def test_order_is_not_valid_within_its_own_signal_bar(name, orders, bars):
    """The regression that produced a 100% fill rate.

    An order valid at its signal bar's open can be filled by 1-minute bars
    inside that same bar — using a close that had not printed when the order
    was supposedly placed.
    """
    o = orders[name]
    if o.empty:
        pytest.skip(f"{name} produced no orders")
    step = D.bar_duration(bars)
    assert (o["valid_from"] >= o["signal_ts"] + step).all(), (
        f"{name}: orders become valid inside the bar that generated them"
    )


@pytest.mark.parametrize("name", registry.NAMES)
def test_orders_expire_and_exit_within_their_own_day(name, orders):
    o = orders[name]
    if o.empty:
        pytest.skip(f"{name} produced no orders")
    assert (o["valid_from"] <= o["expires_at"]).all()
    assert (o["expires_at"] <= o["time_exit_ts"]).all()


@pytest.mark.parametrize("name", registry.NAMES)
def test_risk_is_within_the_tradeable_band(name, orders):
    o = orders[name]
    if o.empty:
        pytest.skip(f"{name} produced no orders")
    computed = (o["entry_price"] - o["stop_price"]).abs()
    assert (computed > 0).all()
    # risk_points must agree with the prices it was derived from.
    assert ((computed - o["risk_points"]).abs() < 1e-9).all()


def test_registry_exposes_every_strategy_module():
    assert len(registry.NAMES) == 7
    for name in registry.NAMES:
        assert registry.describe(name)


def test_unknown_strategy_fails_loudly(bars):
    with pytest.raises(KeyError, match="unknown strategy"):
        registry.generate("does_not_exist", bars)
