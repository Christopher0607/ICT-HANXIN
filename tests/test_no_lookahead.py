"""The guarantee that makes every backtest number in this repo meaningful.

For each detector, take a slice of real data, run it on the full slice, then run
it again on the data truncated at bar ``i``.  Every event the truncated run
produces must appear identically in the full run, and the set of events with
``confirmed_at <= ts[i]`` must match between the two.

If a detector peeks ahead, the truncated run cannot reproduce it and the test
fails.  This catches the failure mode that unit tests on hand-built bars miss:
a detector that is correct in isolation but whose ``confirmed_at`` is optimistic
by a bar or two, which is enough to turn a losing model into a winning one.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ict import data as D
from ict.fvg import find_bpr, find_fvgs
from ict.liquidity import find_equal_levels, find_sfp, find_sweeps
from ict.orderblocks import find_breakers, find_mitigation_blocks, find_order_blocks
from ict.patterns import find_ftr, find_qml, find_range_deviations, find_sr_flips, find_three_taps
from ict.po3 import find_po3
from ict.structure import find_msb
from ict.swings import find_swings

pytestmark = pytest.mark.skipif(
    not (D.PROCESSED_DIR / "nq_5m.parquet").exists(),
    reason="run `uv run python scripts/ingest.py` first",
)

#: Detectors that take a bar frame and return an event frame.
DETECTORS = {
    "swings": find_swings,
    "msb": find_msb,
    "fvg": find_fvgs,
    "sweeps": find_sweeps,
    "sfp": find_sfp,
    "order_blocks": find_order_blocks,
    "breakers": find_breakers,
    "mitigation_blocks": find_mitigation_blocks,
    "qml": find_qml,
    "sr_flips": find_sr_flips,
    "three_taps": find_three_taps,
    "range_deviations": find_range_deviations,
    "ftr": find_ftr,
    "po3": find_po3,
}

#: Columns compared between runs. Bar indices are position-dependent and are
#: expected to differ only when the slice start differs, which it does not here.
COMPARE = ["ts", "confirmed_at", "kind", "direction"]

TRUNCATE_AT = 1500
FULL_BARS = 2000


@pytest.fixture(scope="module")
def window() -> pd.DataFrame:
    df = D.load("5m")
    # A recent stretch with complete session coverage.
    return df[df.ts >= "2026-01-01"].head(FULL_BARS).reset_index(drop=True)


@pytest.mark.parametrize("name", sorted(DETECTORS))
def test_detector_output_is_identical_on_truncated_data(name, window):
    detector = DETECTORS[name]
    full = detector(window)
    truncated = detector(window.iloc[:TRUNCATE_AT].reset_index(drop=True))

    cutoff = window["ts"].iloc[TRUNCATE_AT - 1]
    expected = full[full["confirmed_at"] <= cutoff][COMPARE].reset_index(drop=True)
    actual = truncated[truncated["confirmed_at"] <= cutoff][COMPARE].reset_index(drop=True)

    # Sort identically: ordering within a timestamp is not part of the contract.
    expected = expected.sort_values(COMPARE).reset_index(drop=True)
    actual = actual.sort_values(COMPARE).reset_index(drop=True)

    pd.testing.assert_frame_equal(
        expected, actual, check_dtype=False,
        obj=f"{name}: events visible at {cutoff} differ between full and truncated runs",
    )


@pytest.mark.parametrize("name", sorted(DETECTORS))
def test_no_event_confirms_before_it_occurs(name, window):
    events = DETECTORS[name](window)
    if events.empty:
        pytest.skip(f"{name} produced no events on this window")
    assert (events["confirmed_at"] >= events["ts"]).all()


@pytest.mark.parametrize("name", sorted(DETECTORS))
def test_confirmation_never_precedes_the_window(name, window):
    events = DETECTORS[name](window)
    if events.empty:
        pytest.skip(f"{name} produced no events on this window")
    assert (events["confirmed_at"] >= window["ts"].iloc[0]).all()
    assert (events["confirmed_at"] <= window["ts"].iloc[-1]).all()


def test_bpr_composition_also_holds(window):
    gaps = find_fvgs(window)
    full = find_bpr(gaps)
    if full.empty:
        pytest.skip("no BPRs on this window")
    assert (full["confirmed_at"] >= full["ts"]).all()


def test_equal_levels_confirm_with_their_last_member(window):
    pools = find_equal_levels(find_swings(window))
    if pools.empty:
        pytest.skip("no pools on this window")
    assert (pools["confirmed_at"] >= pools["ts"]).all()
