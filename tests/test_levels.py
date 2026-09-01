"""Dealing range arithmetic: equilibrium, premium/discount and OTE."""

import pytest

from ict.levels import OTE_HIGH, OTE_LOW, DealingRange


@pytest.fixture
def rng():
    return DealingRange(low=100.0, high=200.0)


def test_equilibrium_is_the_midpoint(rng):
    assert rng.equilibrium == 150.0
    assert rng.size == 100.0


def test_premium_and_discount_split_at_equilibrium(rng):
    assert rng.is_premium(175.0) and not rng.is_discount(175.0)
    assert rng.is_discount(125.0) and not rng.is_premium(125.0)
    assert not rng.is_premium(150.0) and not rng.is_discount(150.0)


def test_ote_band_sits_in_discount_for_a_long(rng):
    low, high = rng.ote_zone(direction=1)
    # Retracing 62-79% of a low-to-high leg lands at 121-138.
    assert (low, high) == (100 + (1 - OTE_HIGH) * 100, 100 + (1 - OTE_LOW) * 100)
    assert high < rng.equilibrium, "a long OTE must be in discount"


def test_ote_band_sits_in_premium_for_a_short(rng):
    low, high = rng.ote_zone(direction=-1)
    assert low > rng.equilibrium, "a short OTE must be in premium"


def test_sweet_spot_lies_inside_the_band(rng):
    low, high = rng.ote_zone(1)
    assert low <= rng.sweet_spot(1) <= high


def test_position_maps_the_range_to_zero_one(rng):
    assert rng.position(100.0) == 0.0
    assert rng.position(200.0) == 1.0
    assert rng.position(150.0) == 0.5
