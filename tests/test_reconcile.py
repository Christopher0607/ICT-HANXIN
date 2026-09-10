"""The reconciler, tested on its ability to find faults rather than to be quiet.

A checker that only gets exercised on clean input is a checker nobody knows
works. Each bucket here is built from a known fault and asserted to land in
the right one, because the buckets carry the instruction: data means carry on,
settings and logic mean stop.
"""

from __future__ import annotations

import json
import pathlib

import pandas as pd
import pytest

from bridge.journal import Journal
from scripts.reconcile import bridge_trades, compare


def frame(rows):
    return pd.DataFrame(rows, columns=["date", "side", "qty", "entry", "stop", "target"])


MATCHED = frame([("2026-09-01", "sell", 8, 29127.88, 29159.0, 29096.75)])


def test_identical_trades_produce_no_disagreement():
    assert compare(MATCHED, MATCHED, {}) == []


def test_a_size_mismatch_is_a_settings_fault():
    """Every position is mis-scaled; this is the one that costs money quietly."""
    engine = frame([("2026-09-01", "sell", 4, 29127.88, 29159.0, 29096.75)])
    buckets = [b for b, _, _ in compare(MATCHED, engine, {})]
    assert buckets == ["settings"]


def test_a_trade_only_the_bridge_took_is_a_logic_fault():
    out = compare(MATCHED, frame([]), {})
    assert [b for b, _, _ in out] == ["logic"]
    assert "the engine has no trade" in out[0][2]


def test_a_trade_only_the_engine_took_is_a_logic_fault():
    out = compare(frame([]), MATCHED, {})
    assert [b for b, _, _ in out] == ["logic"]
    assert "placed nothing and logged no reason" in out[0][2]


def test_a_missing_trade_the_guard_explains_is_not_a_fault():
    """The guard declining a signal is the guard working, not a divergence."""
    assert compare(frame([]), MATCHED, {"2026-09-01": ["too close to the loss limit"]}) == []


def test_a_small_entry_difference_is_only_a_data_difference():
    """TradingView's feed is not Databento's; a tick or two is expected."""
    engine = frame([("2026-09-01", "sell", 8, 29129.00, 29159.0, 29096.75)])
    assert compare(MATCHED, engine, {}) == []

    far = frame([("2026-09-01", "sell", 8, 29180.00, 29159.0, 29096.75)])
    assert [b for b, _, _ in compare(MATCHED, far, {})] == ["data"]


def test_opposite_sides_are_a_logic_fault():
    engine = frame([("2026-09-01", "buy", 8, 29127.88, 29159.0, 29096.75)])
    assert "logic" in [b for b, _, _ in compare(MATCHED, engine, {})]


def test_several_faults_are_all_reported_not_just_the_first():
    engine = frame([("2026-09-01", "buy", 4, 29900.00, 0.0, 0.0)])
    buckets = sorted(b for b, _, _ in compare(MATCHED, engine, {}))
    assert buckets == ["data", "logic", "settings"]


# ---------------------------------------------------------------------------
# journal round trip

def test_journal_round_trip(tmp_path):
    j = Journal(tmp_path / "j.jsonl")
    j.write("placed", key="k1", side="sell", qty=8, entry=1.0, stop=2.0,
            target=0.5, day="2026-09-01")
    j.write("blocked", key="k2", reason="too close to the loss limit", day="2026-09-02")
    rows = j.read()
    assert [r["event"] for r in rows] == ["placed", "blocked"]
    assert all("ts" in r for r in rows)

    trades = bridge_trades(rows)
    assert len(trades) == 1 and trades.iloc[0]["qty"] == 8
    assert trades.iloc[0]["date"] == "2026-09-01"


def test_a_half_written_final_line_does_not_lose_the_rest(tmp_path):
    """Killing the process mid-write should cost one row, not the file."""
    path = tmp_path / "j.jsonl"
    j = Journal(path)
    j.write("placed", key="k1", day="2026-09-01", side="sell", qty=1,
            entry=1.0, stop=2.0, target=0.5)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"event": "placed", "qty": ')      # torn write
    assert len(j.read()) == 1


def test_a_journal_with_no_path_writes_nothing_and_still_returns_the_row():
    row = Journal(None).write("placed", key="k")
    assert row["event"] == "placed" and Journal(None).read() == []
