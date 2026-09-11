"""Local signal engine: bars in from TopstepX, orders out to TopstepX.

    uv run python -m bridge.live              # dry run, the default
    uv run python -m bridge.live --live       # actually send orders

Topstep's written answer of 2026-09-10 says every order must originate from the
personal device, and that VPS, VPNs and remote servers are prohibited from
placing, modifying, cancelling or routing them. That rules out running this on a
hosted box, and it also removes the reason to involve TradingView: a chart on
someone else's server pushing a webhook to a tunnel is a lot of moving parts to
end up back on this machine. The bars come from the same connection the orders
go out on, and ``strategies/ltf_sweep.py`` -- the code the backtest actually ran
-- decides. There is no second implementation to keep in step.

Once a minute:

    1. pull the trading day's 1-minute bars, closed ones only
    2. run the research engine over them
    3. send any order whose confirmation has passed and that is not already out
    4. at 15:30 stop placing and cancel what has not filled; at 16:00 flatten
    5. stop as soon as nothing of ours is open, whatever the clock says

**It stops when the day's work is finished, not at a chosen hour.** Every fixed
cutoff considered was a number picked by looking at its own P&L, and the two
halves of the history disagreed about which was best -- so the model runs its
full declared session, and the process simply exits once the setup is resolved
and no order or position is left. Measured over 697 days that is a median of
11:16 ET and an average of 2.8 hours awake against 6.6 for sitting until the
close. ``main`` returns 0 only on a clean finish, so a shutdown belongs in a
wrapper -- ``python -m bridge.live --live && shutdown /h`` -- and never in here:
a shutdown that fires from inside trading code takes the screen with it, and
you never find out why.

Four things here are load-bearing, and each fails silently if it is wrong:

**Closed bars only.** ``includePartialBar`` is false in ``bars_payload``. A bar
still forming has no close, and every detector in ``ict/`` keys off the close of
the last bar it depended on.

**The engine must be replay-stable.** Step 2 re-runs over a growing frame, so an
order that changed its entry after being sent would leave a live order at a
price nothing will ever cancel -- the orphan order that made trades appear at
identical prices hours outside the session in Pine v4. ``confirmed_at`` is what
makes this safe, and ``tests/test_live.py`` replays a real day minute by minute
to prove it rather than trusting the argument.

**Bars must be fresh.** A feed running ten minutes behind still draws, still
produces signals, and still fills orders -- at prices the market left. Stale
bars stop the engine instead.

**Unfilled orders must be cancelled at the deadline.** A resting limit left
behind when this process exits will sit at the broker until the session ends and
can fill hours later, against a stop and target computed for a market that no
longer exists.
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass, field

import pandas as pd

from ict import data as D
from strategies.ltf_sweep import LTFSweepConfig, generate_orders

from .config import Config
from .execute import ERROR, Decision, clamp_note, place_sized
from .guards import AccountState
from .journal import DEFAULT_PATH, Journal
from .topstepx import BrokerError, DryRunBroker, TopstepXBroker

log = logging.getLogger("bridge.live")

NY = "America/New_York"

#: The session guard in LTFSweepConfig is a data sanity check for the backtest,
#: where it drops 1 of 3,625 days. Replayed minute by minute it means something
#: else: a real session has 30 bars at 10:00, and the default of 60 would hold
#: every signal until 10:29 -- 32 minutes late, measured on 2026-09-01.
LIVE_CONFIG = LTFSweepConfig(min_session_bars=0)

#: How far back to ask for bars. The 15-minute liquidity pools are built from
#: candles before the open, so starting at 09:30 would price the first sweeps
#: against pools that do not exist yet. The CME day rolls at 18:00 ET.
SESSION_ROLL_HOUR = 18

#: Seconds past the minute to wake. The backtest opens its fill window at the
#: confirmation instant -- the close of one bar and the open of the next -- so
#: every second spent waiting is a second of that bar given away. Fills inside
#: the confirming bar are 14.3% of trades and $4,209 of $16,409 over 2024-2026,
#: which is what this offset is protecting. It used to be a whole bar, not a
#: couple of seconds, because three separate places read a deadline off the
#: bars in hand rather than off the session's schedule.
POLL_OFFSET_S = 2.0

#: If the bar that just closed has not been published yet, try again inside the
#: same minute rather than waiting for the next one -- which would be worse than
#: never having tightened the offset at all.
RETRY_AFTER_S = (3.0, 6.0)

#: Consecutive ticks that must agree the day is over before the engine believes
#: it. One is not enough: for a few seconds after an order is accepted, the
#: broker can report neither a resting order nor a position, and a single clean
#: look there would end the session on top of a live order. The costs are not
#: symmetric -- an extra hour awake is electricity, an hour short is an
#: unattended position.
CLEAN_CHECKS_REQUIRED = 2


def day_start(now: pd.Timestamp) -> pd.Timestamp:
    """18:00 ET on the evening this trading day began."""
    ny = now.tz_convert(NY)
    start = ny.normalize() + pd.Timedelta(hours=SESSION_ROLL_HOUR)
    if ny.hour < SESSION_ROLL_HOUR:
        start -= pd.Timedelta(days=1)
    return start.tz_convert("UTC")


def et_minute(ts: pd.Timestamp) -> int:
    ny = ts.tz_convert(NY)
    return ny.hour * 60 + ny.minute


def order_key(row) -> str:
    """Stable identity for one setup, so a re-run never sends it twice."""
    return f"{row.valid_from.isoformat()}|{'buy' if row.direction > 0 else 'sell'}"


def position_size(entry: float, stop: float, cfg: Config, risk_usd: float) -> int:
    """Contracts, from a fixed dollar risk and the stop distance."""
    points = abs(entry - stop)
    if points <= 0:
        return 0
    return int(risk_usd / (points * cfg.point_value))


@dataclass
class LiveEngine:
    """One trading day's worth of state. Constructed fresh each session."""

    cfg: Config
    broker: object
    journal: Journal
    risk_usd: float
    state: AccountState = field(default_factory=AccountState)
    #: order key -> broker order id, for the cancel sweep at the cutoff.
    outstanding: dict[str, int] = field(default_factory=dict)
    handled: set[str] = field(default_factory=set)
    closed: bool = False
    #: True only when the day ended with nothing of ours left open. The exit
    #: code follows this, and a shutdown wrapper follows the exit code.
    finished_clean: bool = False
    past_deadline: bool = False
    flattened: bool = False
    clean_checks: int = 0
    #: Newest bar seen on the last tick, for the late-publication retry.
    last_bar_ts: pd.Timestamp | None = None

    def bars(self, now: pd.Timestamp) -> pd.DataFrame:
        raw = self.broker.retrieve_bars(day_start(now), now)
        return D.add_time_columns(raw) if not raw.empty else raw

    def stale_reason(self, bars: pd.DataFrame, now: pd.Timestamp) -> str | None:
        """Why these bars cannot be traded on, or None if they are fine."""
        if bars.empty:
            return "no bars returned"
        age = (now - bars["ts"].iloc[-1]).total_seconds()
        if age > self.cfg.max_bar_age_s:
            return f"last bar is {age / 60:.1f} min old (limit {self.cfg.max_bar_age_s / 60:.1f})"
        if age < 60:
            # A closed 1-minute bar stamped at its open can never be less than
            # 60s old. Younger means the feed stamps bars at the close, or a
            # partial bar came through -- either way every signal would be
            # judged one bar out of place, so stop rather than guess.
            return (f"last bar is only {age:.0f}s old; bars look stamped at the "
                    "close, not the open, which shifts every signal by a bar")
        return None

    def tick(self, now: pd.Timestamp) -> list[Decision]:
        """One minute's work. Returns whatever was decided, for the caller to log."""
        if self.closed:
            return []
        minute = et_minute(now)

        # Deadlines are handled before the feed is consulted. A dead or delayed
        # feed is exactly when an order must not be left resting unattended, so
        # a broken feed has to drive the session to its end rather than postpone
        # the steps that end it.
        if minute >= self.cfg.cutoff_minute and not self.past_deadline:
            self.close_out(now)
        if minute >= self.cfg.flat_minute and not self.flattened:
            self.flatten(now)

        # Finishing is a statement about the account, not about the clock: the
        # day's setup is resolved and nothing of ours is left open. Most days
        # that is true well before noon -- median 11:16 ET over 2024-2026 --
        # which is why the engine can run the model's full session without
        # keeping the machine awake for all of it.
        if self.day_done(now):
            self.finish(now)
            return []
        if self.past_deadline:
            return []          # nothing left to place; only waiting to be done

        bars = self.bars(now)
        self.last_bar_ts = None if bars.empty else bars["ts"].iloc[-1]
        stale = self.stale_reason(bars, now)
        if stale:
            self.journal.write("stale_feed", why=stale, at=now.isoformat())
            log.warning("not trading: %s", stale)
            return []

        orders = generate_orders(bars, LIVE_CONFIG)
        out = []
        for row in orders.itertuples():
            key = order_key(row)
            if key in self.handled or row.valid_from > now:
                continue
            out.append(self.send(row, key, now))
        return out

    def ours(self, rows) -> list:
        """Rows on the contract this engine trades."""
        return [r for r in rows if r.get("contractId") == self.cfg.contract_id]

    def day_done(self, now: pd.Timestamp) -> bool:
        """Is there anything left to do today?

        Every branch that cannot answer confidently answers "no". A wrong "yes"
        ends the process with a position nobody is watching; a wrong "no" costs
        an hour of electricity.
        """
        # One trade per day, so once the day's setup has been dealt with --
        # placed, or skipped for size -- no other can appear. Before that, only
        # the entry deadline settles it.
        if not self.handled and not self.past_deadline:
            return False

        try:
            resting = self.ours(self.broker.open_orders())
            holding = self.ours(self.broker.open_positions())
        except (BrokerError, KeyError, TypeError, ValueError) as exc:
            self.journal.write("done_check_failed", error=str(exc))
            log.warning("cannot tell whether the day is finished (%s); staying up", exc)
            self.clean_checks = 0
            return False

        if resting or holding:
            self.clean_checks = 0
            return False

        self.clean_checks += 1
        return self.clean_checks >= CLEAN_CHECKS_REQUIRED

    def finish(self, now: pd.Timestamp) -> None:
        self.closed = True
        self.finished_clean = True
        self.journal.write("session_done", at=now.isoformat(),
                           et_minute=et_minute(now),
                           nothing_outstanding=not self.outstanding)
        log.info("nothing left open; done for the day")

    def flatten(self, now: pd.Timestamp) -> None:
        """The model is flat at 16:00, and only the broker bracket enforces the
        rest. Five of 550 out-of-sample trades reach this; they are also the
        ones with nobody watching."""
        self.flattened = True
        try:
            holding = self.ours(self.broker.open_positions())
        except (BrokerError, KeyError, TypeError, ValueError) as exc:
            self.journal.write("flatten_check_failed", error=str(exc))
            log.error("cannot tell whether a position is open at the flat time: %s", exc)
            return
        if not holding:
            return
        try:
            result = self.broker.close_position()
        except BrokerError as exc:
            self.journal.write("flatten_failed", error=str(exc))
            log.error("could not flatten at the close: %s", exc)
            return
        self.journal.write("flattened", at=now.isoformat(), broker=result,
                           size=sum(int(p.get("size", 0)) for p in holding))
        log.info("flat at the close")

    def send(self, row, key: str, now: pd.Timestamp) -> Decision:
        side = "buy" if row.direction > 0 else "sell"
        qty = position_size(row.entry_price, row.stop_price, self.cfg, self.risk_usd)
        day = str(row.trading_date)
        if qty < 1:
            self.handled.add(key)
            self.journal.write("skipped", key=key, why="risk buys less than one contract",
                               entry=row.entry_price, stop=row.stop_price)
            return Decision("skipped", "risk buys less than one contract")

        note = clamp_note(qty, min(qty, self.cfg.max_contracts), self.state, self.cfg)
        if note:
            log.warning("%s", note)

        decision = place_sized(
            broker=self.broker, journal=self.journal, state=self.state, cfg=self.cfg,
            side=side, qty=qty, entry=float(row.entry_price), stop=float(row.stop_price),
            target=float(row.target_price), day=day, key=key, tag=f"ltf_sweep {day}",
        )
        # Retried next minute only on a broker error: a guard block is a
        # decision, not a failure, and re-asking every minute would fill the
        # journal with the same refusal sixty times an hour.
        if decision.status != ERROR:
            self.handled.add(key)
        if decision.ok and decision.order_id is not None:
            self.outstanding[key] = decision.order_id
        log.info("%s -> %s", key, decision.detail)
        return decision

    def close_out(self, now: pd.Timestamp) -> None:
        """Entry deadline: cancel what has not filled. A position may still be
        open, and its bracket is still the broker's to manage, so this does not
        end the session -- ``day_done`` does."""
        self.past_deadline = True

        # Ask what is actually still resting rather than cancelling everything
        # sent. An entry that filled has a live position behind it, and its id
        # would refuse the cancel -- which is indistinguishable, from the error
        # alone, from a cancel that genuinely failed and left an order out
        # there. The first is routine, the second is the thing to shout about.
        resting: set[int] | None
        try:
            resting = {int(o["id"]) for o in self.broker.open_orders()}
        except (BrokerError, KeyError, TypeError, ValueError) as exc:
            # Blind, then: a redundant cancel on a filled order is harmless, a
            # skipped one on a resting order is what this step exists to stop.
            self.journal.write("open_orders_failed", error=str(exc))
            log.warning("could not list open orders (%s); cancelling everything sent", exc)
            resting = None

        for key, order_id in list(self.outstanding.items()):
            if resting is not None and order_id not in resting:
                self.journal.write("no_longer_resting", key=key, order_id=order_id,
                                   why="filled or already gone")
                self.outstanding.pop(key, None)
                continue
            try:
                result = self.broker.cancel(order_id)
            except BrokerError as exc:
                self.journal.write("cancel_failed", key=key, order_id=order_id,
                                   error=str(exc))
                log.error("could not cancel %s (order %s): %s", key, order_id, exc)
                continue
            self.journal.write("cancelled", key=key, order_id=order_id,
                               why="cutoff", broker=result)
            log.info("cancelled %s at the cutoff", key)
            self.outstanding.pop(key, None)

        self.journal.write("entry_deadline", at=now.isoformat(),
                           cutoff_minute=self.cfg.cutoff_minute,
                           still_outstanding=len(self.outstanding))


def run(engine: LiveEngine, *, sleep=time.sleep, now=None) -> None:
    """Poll once a minute until the cutoff. Interruptible with Ctrl-C."""
    clock = now or (lambda: pd.Timestamp.now(tz="UTC"))
    retries = 0
    while not engine.closed:
        started = clock()
        try:
            engine.tick(started)
        except BrokerError as exc:
            engine.journal.write("tick_error", error=str(exc), at=started.isoformat())
            log.error("tick failed: %s", exc)
        if engine.closed:
            break

        # The bar that closed at the top of this minute is the one carrying any
        # new signal. If the feed has not published it yet, come back in a few
        # seconds; waiting a whole minute would give away the bar this offset
        # exists to catch.
        want = started.floor("min") - pd.Timedelta(minutes=1)
        late = engine.last_bar_ts is not None and engine.last_bar_ts < want
        if late and retries < len(RETRY_AFTER_S):
            sleep(RETRY_AFTER_S[retries])
            retries += 1
            continue

        retries = 0
        nxt = started.ceil("min") + pd.Timedelta(seconds=POLL_OFFSET_S)
        sleep(max(1.0, (nxt - clock()).total_seconds()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="actually send orders (default is a dry run)")
    ap.add_argument("--preflight", action="store_true",
                    help="check everything that can fail, send nothing, and exit")
    ap.add_argument("--offline", action="store_true",
                    help="with --preflight, skip the checks that need the broker")
    ap.add_argument("--journal", default=str(DEFAULT_PATH),
                    help="where to append the decision journal")
    ap.add_argument("--risk", type=float, default=1000.0,
                    help="dollars risked per trade (50K Combine at 2%% is 1000)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = Config.from_env(live=args.live)
    except RuntimeError as exc:
        # Reporting what is missing IS the job here; a traceback tells the
        # reader to go and read the code instead.
        print(f"  [FAIL] configuration  {exc}")
        print("\nPREFLIGHT FAILED -- configuration incomplete"
              if args.preflight else "\nnot starting -- configuration incomplete")
        return 1

    if args.preflight:
        from .preflight import report, run as preflight
        print(f"preflight: {cfg.preset} on {cfg.base_url}\n")
        return 0 if report(preflight(cfg, reach_broker=not args.offline,
                                     need_webhook=False)) else 1

    broker = TopstepXBroker(cfg) if args.live else DryRunBroker(cfg)
    broker.authenticate()

    engine = LiveEngine(cfg=cfg, broker=broker, journal=Journal(args.journal),
                        risk_usd=args.risk)
    log.info("%s | %s | contract %s | risk $%.0f/trade | entries until %02d:%02d ET, "
             "flat %02d:%02d, exits when nothing is left open",
             "LIVE" if args.live else "dry run", cfg.preset, cfg.contract_id,
             args.risk, cfg.cutoff_minute // 60, cfg.cutoff_minute % 60,
             cfg.flat_minute // 60, cfg.flat_minute % 60)
    try:
        run(engine)
    except KeyboardInterrupt:
        log.info("interrupted; cancelling anything still resting")
        engine.close_out(pd.Timestamp.now(tz="UTC"))

    # The exit code is what a shutdown wrapper reads, so it has to mean exactly
    # one thing: nothing of ours is open. Anything else -- an error, an
    # interrupt, a cancel that failed -- must keep the machine awake.
    if engine.finished_clean:
        return 0
    log.error("finished with work outstanding; do NOT shut down unattended")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
