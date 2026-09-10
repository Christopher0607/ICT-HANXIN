"""The one place a sized order is checked and sent.

Two callers reach the broker: the webhook server in ``server.py`` and the local
signal engine in ``live.py``. They arrive with different things in hand -- one
has a parsed alert, the other an order row out of the research engine -- but
from the moment a size and three prices exist, the sequence is identical:

    roll the trading day -> clamp to the contract ceiling -> ask the guards
    -> send -> write the journal line

Writing that twice would put the account rules in two places that have to be
kept in step by hand. The Pine port is already one such duplication, and it is
only safe because a test reads both and compares them. This one does not need
to exist at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Config
from .guards import REASONS, AccountState, block_reason, clamp_size, scaling_cap
from .journal import Journal
from .topstepx import BrokerError, Order

PLACED = "placed"
BLOCKED = "blocked"
SKIPPED = "skipped"
ERROR = "broker_error"


@dataclass
class Decision:
    """What happened to one order, and enough detail to say why out loud."""

    status: str
    detail: str
    qty: int = 0
    order_id: int | None = None
    result: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == PLACED


def place_sized(
    *, broker, journal: Journal, state: AccountState, cfg: Config,
    side: str, qty: int, entry: float, stop: float, target: float,
    day: str, key: str, tag: str,
) -> Decision:
    """Check one order against the account rules and send it if it passes.

    Every outcome writes a journal line, including the ones that send nothing.
    A month where the guards declined fourteen signals looks identical to a
    quiet month in a broker statement; it has to be legible somewhere.
    """
    state.roll_day(day, cfg)

    sized = clamp_size(qty, cfg, realized=state.realized)
    if sized < 1:
        journal.write(SKIPPED, key=key, why="size clamps to zero", qty=qty)
        return Decision(SKIPPED, f"size {qty} clamps to {sized}", qty=sized)

    planned_risk = abs(entry - stop) * cfg.point_value * sized
    reason = block_reason(state, planned_risk, cfg)
    if reason:
        journal.write(BLOCKED, key=key, reason=REASONS[reason], code=reason,
                      qty=sized, entry=entry, stop=stop, target=target,
                      equity=round(state.equity(cfg), 2),
                      floor=round(state.mll_floor, 2),
                      planned_risk=round(planned_risk, 2))
        return Decision(
            BLOCKED,
            f"{REASONS[reason]} (equity ${state.equity(cfg):,.0f}, "
            f"floor ${state.mll_floor:,.0f}, this trade risks ${planned_risk:,.0f})",
            qty=sized,
        )

    order = Order(side=side, size=sized, entry=entry, stop=stop, target=target, tag=tag)
    try:
        result = broker.place(order)
    except BrokerError as exc:
        journal.write(ERROR, key=key, error=str(exc), qty=sized,
                      entry=entry, stop=stop, target=target)
        return Decision(ERROR, str(exc), qty=sized)

    state.trades_today += 1
    journal.write(PLACED, key=key, side=side, qty=sized, requested_qty=qty,
                  entry=entry, stop=stop, target=target,
                  planned_risk=round(planned_risk, 2), day=day,
                  dry_run=bool(result.get("dryRun")), broker=result)
    return Decision(PLACED, f"placed {side} {sized} @ {entry:.2f}", qty=sized,
                    order_id=result.get("orderId"), result=result)


def clamp_note(qty: int, sized: int, state: AccountState, cfg: Config) -> str | None:
    """A line worth logging when the ceiling cut the size, else None."""
    if sized == qty:
        return None
    return (f"size {qty} over the {scaling_cap(state.realized, cfg)} ceiling, "
            f"cut to {sized}")
