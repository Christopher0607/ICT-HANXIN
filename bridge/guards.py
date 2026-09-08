"""The prop-firm rules again, on the server side.

This duplicates the ``propBlock`` function in the Pine scripts on purpose. The
chart is not a trustworthy gate: it may not have been reloaded after an input
change, its inputs may have been edited by hand, an alert may fire twice, and a
webhook can be replayed by anyone who learns the URL. Whatever reaches this
process has to be checked again by something that cannot be edited from a
phone.

The two implementations must agree. tests/test_bridge.py reads the numbers out
of the Pine source and compares them, so a change to one that is not made to
the other fails the suite rather than silently opening a hole.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config

OK = 0
BLOCK_LOSS_LIMIT = 1
BLOCK_DAILY_CAP = 2
BLOCK_TARGET_MADE = 3

REASONS = {
    OK: "ok",
    BLOCK_LOSS_LIMIT: "too close to the trailing loss limit",
    BLOCK_DAILY_CAP: "would breach the daily loss cap",
    BLOCK_TARGET_MADE: "profit target already made",
}


@dataclass
class AccountState:
    """What the guards need to know, in account currency.

    ``mll_floor`` trails the end-of-day balance upward and never moves down;
    ``equity`` includes open profit, because the firm's breach test does not
    wait for the position to close even though the threshold only ratchets at
    the close.
    """

    realized: float = 0.0
    open_pnl: float = 0.0
    day_open_equity: float | None = None
    mll_floor: float | None = None
    trades_today: int = 0
    day: str | None = None

    def start(self, cfg: Config) -> None:
        if self.mll_floor is None:
            self.mll_floor = cfg.account_start - cfg.max_loss_limit
        if self.day_open_equity is None:
            self.day_open_equity = cfg.account_start

    def equity(self, cfg: Config) -> float:
        return cfg.account_start + self.realized + self.open_pnl

    def roll_day(self, day: str, cfg: Config) -> None:
        """Session boundary: ratchet the floor, reset the day's counters."""
        self.start(cfg)
        if self.day == day:
            return
        if self.day is not None:
            closed = cfg.account_start + self.realized
            self.mll_floor = max(self.mll_floor, closed - cfg.max_loss_limit)
        self.day = day
        self.day_open_equity = self.equity(cfg)
        self.trades_today = 0


def block_reason(state: AccountState, planned_risk: float, cfg: Config) -> int:
    """0 to trade, otherwise why not. Same codes as the Pine ``propBlock``."""
    state.start(cfg)
    equity = state.equity(cfg)
    if equity - state.mll_floor < planned_risk * cfg.safety_mult:
        return BLOCK_LOSS_LIMIT
    if (equity - state.day_open_equity) - planned_risk <= -cfg.daily_loss_limit:
        return BLOCK_DAILY_CAP
    if state.realized >= cfg.profit_target:
        return BLOCK_TARGET_MADE
    return OK


def clamp_size(qty: int, cfg: Config) -> int:
    """Cut an oversized position down to the ceiling rather than skipping it."""
    return max(0, min(int(qty), cfg.max_contracts))
