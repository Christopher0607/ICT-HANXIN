"""Dealing ranges, premium/discount and OTE (Lectures 006, 007, 008).

ICT never buys or sells at an arbitrary price — it buys in *discount* and sells
in *premium*, measured against the current dealing range.

The dealing range runs from a confirmed swing low to a confirmed swing high.
Its midpoint (equilibrium) splits premium from discount.  The **optimal trade
entry** is the 0.62-0.79 retracement band, with 0.705 as the midpoint ICT cites
most often.  Reverse OTE (Lecture 008) applies the same band to a projection in
the direction of the move rather than a retracement.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

OTE_LOW = 0.62
OTE_HIGH = 0.79
OTE_SWEET = 0.705


@dataclass(frozen=True)
class DealingRange:
    """A swing low to swing high range, with ICT's standard levels derived."""

    low: float
    high: float

    @property
    def size(self) -> float:
        return self.high - self.low

    @property
    def equilibrium(self) -> float:
        return (self.high + self.low) / 2.0

    def retracement(self, level: float, direction: int) -> float:
        """Price at a given retracement of the range.

        ``direction`` is the direction of the *expansion leg*: +1 means the
        range was made low-to-high, so a retracement measures down from the
        high; -1 mirrors it.
        """
        if direction >= 0:
            return self.high - self.size * level
        return self.low + self.size * level

    def ote_zone(self, direction: int) -> tuple[float, float]:
        """The (lower, upper) bounds of the optimal trade entry band."""
        a = self.retracement(OTE_LOW, direction)
        b = self.retracement(OTE_HIGH, direction)
        return (min(a, b), max(a, b))

    def sweet_spot(self, direction: int) -> float:
        return self.retracement(OTE_SWEET, direction)

    def is_premium(self, price: float) -> bool:
        return price > self.equilibrium

    def is_discount(self, price: float) -> bool:
        return price < self.equilibrium

    def position(self, price: float) -> float:
        """Where ``price`` sits in the range: 0.0 at the low, 1.0 at the high."""
        if self.size <= 0:
            return float("nan")
        return (price - self.low) / self.size


def range_from_session(session_row: pd.Series) -> DealingRange:
    """Build a dealing range from a ``sessions.session_ranges`` row."""
    return DealingRange(low=float(session_row["low"]), high=float(session_row["high"]))
