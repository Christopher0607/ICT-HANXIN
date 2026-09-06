"""Guards against the bug class that test_no_lookahead.py cannot catch.

The truncation test proves each detector is *self-consistent*: it produces the
same events whether or not it can see the future. A detector that uniformly
claims confirmation one bar too early passes that test perfectly — it is
consistently wrong, which is exactly what consistency checks miss.

This module tests the thing itself: that ``confirmed_at`` lands at the CLOSE of
the last bar the pattern depends on, never at its open. The distinction is not
academic. Every one of these detectors reads a bar's ``close`` (or its extreme),
so an order valid at that bar's open can be filled by 1-minute bars *inside*
the very bar that generated the signal, using a close that had not printed yet.
The first run of this strategy set produced a 100% fill rate that way.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ict import data as D
from ict.fvg import find_fvgs
from ict.liquidity import find_level_sweeps, find_sfp, find_sweeps
from ict.orderblocks import find_breakers, find_order_blocks
from ict.patterns import find_ftr, find_range_deviations, find_sr_flips
from ict.po3 import find_po3
from ict.structure import find_msb
from ict.swings import find_swings

pytestmark = pytest.mark.skipif(
    not (D.PROCESSED_DIR / "nq_5m.parquet").exists(),
    reason="run `uv run python scripts/ingest.py` first",
)

DETECTORS = {
    "swings": find_swings, "msb": find_msb, "fvg": find_fvgs,
    "sweeps": find_sweeps, "sfp": find_sfp,
    "order_blocks": find_order_blocks, "breakers": find_breakers,
    "sr_flips": find_sr_flips, "range_deviations": find_range_deviations,
    "ftr": find_ftr, "po3": find_po3,
}


@pytest.fixture(scope="module")
def window():
    df = D.load("5m")
    return df[df.ts >= "2026-01-01"].head(2000).reset_index(drop=True)


@pytest.mark.parametrize("name", sorted(DETECTORS))
def test_confirmation_lands_on_a_bar_close_not_a_bar_open(name, window):
    events = DETECTORS[name](window)
    if events.empty:
        pytest.skip(f"{name} produced no events")

    step = D.bar_duration(window)
    opens = set(window["ts"])

    # Every confirmation must be at least one full bar after the anchor bar's
    # open, because forming the pattern always requires that bar to complete.
    assert (events["confirmed_at"] >= events["ts"] + step).all(), (
        f"{name}: some events claim confirmation within their own anchor bar"
    )

    # And it must be a real bar boundary: the close of bar i is the open of
    # bar i+1, so subtracting one interval must land on a known bar open.
    shifted = events["confirmed_at"] - step
    assert shifted.isin(opens).all(), (
        f"{name}: confirmed_at is not aligned to a bar close"
    )


def test_sfp_confirms_only_after_its_reversal_bar_closes(window):
    """The specific regression: an SFP needs its own bar's close."""
    sfps = find_sfp(window, n=2)
    if sfps.empty:
        pytest.skip("no SFPs on this window")
    step = D.bar_duration(window)
    assert (sfps["confirmed_at"] == sfps["ts"] + step).all()


def test_level_sweeps_confirm_on_the_close_too(window):
    from ict.events import BEARISH
    from ict.sessions import prior_day_levels

    levels = prior_day_levels(window)
    sweeps = find_level_sweeps(window, levels, "pdh", BEARISH)
    if sweeps.empty:
        pytest.skip("no prior-day-high sweeps on this window")
    step = D.bar_duration(window)
    assert (sweeps["confirmed_at"] == sweeps["ts"] + step).all()
