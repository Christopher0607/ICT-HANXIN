"""The forward log, tested on the two things that would destroy its value.

Its worth rests entirely on never having been recomputed, so what needs
guarding is not the arithmetic but the file: a re-run must resume past the last
entry, and writing the log back must not reformat a single byte of it. Both are
checked here against the real ``docs/forward_log.csv``.

The third property -- that restating the log at a different risk changes only
the dollars -- is checked inside the code itself by ``assert_same_trades``,
because the run it would need takes a minute and a half. The guard is tested
here on the disagreement it exists to catch.
"""

from __future__ import annotations

import pathlib

import pandas as pd
import pytest

from scripts import forward_log
from scripts.forward_log import (
    COLUMNS,
    FORWARD_START,
    assert_same_trades,
    resume_from,
    trade_rows,
)

LOG = pathlib.Path("docs/forward_log.csv")


def keys(*pairs):
    return pd.DataFrame(list(pairs), columns=["date", "entry_et"])


# --- the append-only guarantee -------------------------------------------

def test_a_rerun_resumes_the_day_after_the_last_entry():
    existing = pd.DataFrame({"date": ["2026-08-31", "2026-09-07"]})
    assert resume_from(existing) == pd.Timestamp("2026-09-08T04:00Z")


def test_resuming_uses_the_last_date_not_the_last_row():
    """The log is sorted, but nothing enforces it; max() is the honest read."""
    out_of_order = pd.DataFrame({"date": ["2026-09-07", "2026-08-31"]})
    assert resume_from(out_of_order) == resume_from(
        pd.DataFrame({"date": ["2026-08-31", "2026-09-07"]}))


@pytest.mark.parametrize("empty", [None, pd.DataFrame({"date": []})])
def test_no_log_starts_at_the_first_unseen_bar(empty):
    assert resume_from(empty) == FORWARD_START


def test_rewriting_the_log_does_not_change_a_byte_of_it():
    """The read/write round trip main() performs on every run.

    If pandas reformatted a float here, every re-run would silently rewrite
    history while reporting that it had only appended.
    """
    raw = LOG.read_bytes()
    assert pd.read_csv(LOG, dtype={"date": str}).to_csv(index=False) == raw.decode()


def test_the_log_on_disk_has_the_columns_the_writer_produces():
    assert list(pd.read_csv(LOG, nrows=0).columns) == COLUMNS


# --- the restatement guard ------------------------------------------------

def test_the_same_trades_at_two_sizes_pass():
    both = keys(("2026-08-31", "10:18"), ("2026-09-01", "10:00"))
    assert assert_same_trades(both, both, 900.0) is None


def test_a_trade_only_the_log_has_raises():
    log = keys(("2026-08-31", "10:18"), ("2026-09-01", "10:00"))
    with pytest.raises(SystemExit, match="2026-09-01 10:00"):
        assert_same_trades(log, keys(("2026-08-31", "10:18")), 900.0)


def test_a_trade_only_the_restated_run_has_raises():
    """Sizing reaching into signal generation shows up as an extra trade."""
    rows = keys(("2026-08-31", "10:18"), ("2026-09-02", "10:05"))
    with pytest.raises(SystemExit, match="upstream of sizing"):
        assert_same_trades(keys(("2026-08-31", "10:18")), rows, 900.0)


def test_the_same_day_at_a_different_minute_is_a_different_trade():
    """One per day, so a shifted entry time means the signal itself moved."""
    with pytest.raises(SystemExit):
        assert_same_trades(keys(("2026-08-31", "10:18")),
                           keys(("2026-08-31", "10:19")), 900.0)


# --- the quiet re-run path ------------------------------------------------

def test_a_window_with_no_signals_still_has_the_log_columns():
    """The normal case on a re-run that finds nothing new."""
    assert list(trade_rows(pd.DataFrame()).columns) == COLUMNS
    assert trade_rows(pd.DataFrame({"filled": []})).empty


def test_nothing_new_to_append_still_reports(tmp_path, monkeypatch, capsys):
    """An early return here once swallowed the summary and the restatement.

    Having nothing to append is the ordinary state between fetches, and it is
    exactly when someone runs this to read the log they already have.
    """
    log = tmp_path / "log.csv"
    log.write_text(LOG.read_text())
    rows = len(pd.read_csv(log))

    # Data that stops well before the log does, so there is nothing to score.
    stale = pd.DataFrame({"ts": pd.to_datetime(["2020-01-02T14:30Z"])})
    monkeypatch.setattr(forward_log.D, "load", lambda *a, **k: stale)
    monkeypatch.setattr(forward_log, "baseline",
                        lambda *a, **k: pd.DataFrame({"net_pnl": [1.0, -1.0]}))
    seen = {}
    monkeypatch.setattr(forward_log, "report",
                        lambda log_, base: seen.update(n=len(log_)))

    assert forward_log.main(["--log", str(log), "--dry-run"]) == 0
    assert seen["n"] == rows, "the report must see every logged trade"
    assert "nothing to add" in capsys.readouterr().out
