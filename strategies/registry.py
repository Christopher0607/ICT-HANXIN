"""The pre-registered strategy set.

Everything in this table was specified before any of it was run against the
out-of-sample period, and the parameters are ICT's published numbers rather
than values found by search.  That matters: with seven strategies, whichever
one happens to look best in development is partly just the maximum of seven
noisy draws.  Freezing the list and the parameters in advance is what makes the
out-of-sample comparison meaningful, and reporting all seven — including the
ones that lose — is what stops the selection bias from being hidden.

Adding a strategy here after seeing results, or tuning one because its numbers
disappointed, would invalidate that. Mechanical fixes (a stop on the wrong side,
a setup that never fills) are legitimate; parameter changes are not.
"""

from __future__ import annotations

from . import (breaker_retest, ob_retest, ote_retracement, po3_judas,
               sfp_reversal, silver_bullet, turtle_soup)

#: name -> (module, human-readable description)
STRATEGIES = {
    "po3_judas": (po3_judas, "PO3 / Judas swing: Asian range raid, MSB, FVG entry"),
    "silver_bullet": (silver_bullet, "First aligned FVG in the 10:00-11:00 ET window"),
    "turtle_soup": (turtle_soup, "Raid on prior day high/low, then structure break"),
    "ob_retest": (ob_retest, "Retest of an order block left by displacement"),
    "breaker_retest": (breaker_retest, "Retest of a failed order block, polarity flipped"),
    "ote_retracement": (ote_retracement, "0.705 retracement of a post-break expansion leg"),
    "sfp_reversal": (sfp_reversal, "Swing failure pattern inside the NY morning"),
}

NAMES = list(STRATEGIES)


def generate(name: str, df5m):
    """Run one strategy by name and return its order frame."""
    if name not in STRATEGIES:
        raise KeyError(f"unknown strategy {name!r}; known: {NAMES}")
    module, _ = STRATEGIES[name]
    orders = module.generate_orders(df5m)
    if not orders.empty and "strategy" not in orders.columns:
        orders["strategy"] = name
    return orders


def describe(name: str) -> str:
    return STRATEGIES[name][1]
