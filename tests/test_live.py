"""Tests for the local signal engine.

Nothing here touches the network: ``DryRunBroker`` hands back bars from a frame
and records what it would have sent.

The replay tests use real 1-minute data because what they check is a property of
the engine over a real session -- that a setup, once confirmed, never changes
its mind. A hand-built fixture would only prove the property on bars chosen to
have it.
"""

from __future__ import annotations

import itertools

import pandas as pd
import pytest

from bridge.config import Config
from bridge.journal import Journal
from bridge.live import (LIVE_CONFIG, LiveEngine, day_start, et_minute, order_key,
                         position_size, run)
from bridge.topstepx import DryRunBroker, bars_payload, parse_bars
from ict import data as D
from strategies.ltf_sweep import LTFSweepConfig, generate_orders

NY = "America/New_York"

#: A day whose signal is known to the minute from docs/ltf_signals_recent.csv:
#: swept 09:50 ET, CHoCH confirmed 09:57, short at 29127.88.
KNOWN_DAY = "2026-09-01"
KNOWN_CONFIRM_ET = "09:57"
KNOWN_ENTRY = 29127.875

needs_data = pytest.mark.skipif(
    not (D.PROCESSED_DIR / "nq_1m.parquet").exists(),
    reason="run `uv run python scripts/ingest.py` first",
)


def cfg(**over) -> Config:
    base = dict(
        username="u", api_key="k", base_url="http://example.invalid", account_id=1,
        contract_id="CON.F.US.MNQ.Z26", tick_size=0.25, point_value=2.0,
        webhook_secret="", live=False, live_data=False, cutoff_minute=15 * 60 + 30, flat_minute=16 * 60,
        max_bar_age_s=150.0, preset="Topstep 50K", account_start=50000.0,
        profit_target=3000.0, max_loss_limit=2000.0, daily_loss_limit=1000.0,
        max_contracts=50, safety_mult=1.5, use_guard=False, scaling_plan=False,
    )
    base.update(over)
    return Config(**base)


@pytest.fixture(scope="module")
def day() -> pd.DataFrame:
    """One trading day of real 1-minute bars.

    Filtered on ``ts`` at read time rather than after: the CME day rolls at
    18:00 ET so the window opens the evening before, and doing it this way round
    means ``add_time_columns`` runs over a thousand rows instead of the file's
    several million -- seconds instead of minutes, on every run of the suite.
    """
    edge = pd.Timestamp(f"{KNOWN_DAY} 00:00", tz="UTC")
    frame = pd.read_parquet(
        D.PROCESSED_DIR / "nq_1m.parquet",
        filters=[("ts", ">=", edge - pd.Timedelta(days=1)),
                 ("ts", "<", edge + pd.Timedelta(days=1))],
    )
    out = D.add_time_columns(frame)
    out = out[out["trading_date"] == pd.Timestamp(KNOWN_DAY).date()]
    return out.reset_index(drop=True)


def et(hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"{KNOWN_DAY} {hhmm}", tz=NY).tz_convert("UTC")


def engine(day: pd.DataFrame, **over) -> LiveEngine:
    conf = cfg(**over)
    broker = DryRunBroker(conf, bars=day[["ts", "open", "high", "low", "close", "volume"]])
    return LiveEngine(cfg=conf, broker=broker, journal=Journal(None), risk_usd=1000.0)


# --------------------------------------------------------------------------
# the session guard: a backtest sanity check that would cripple live trading

@needs_data
def test_the_live_config_emits_a_setup_the_minute_it_confirms(day):
    """The default 60-bar floor holds every signal back until 10:29.

    In the backtest that threshold drops 1 day in 3,625 -- it is there to skip
    broken data. Replayed minute by minute it means something completely
    different, because a real session legitimately has 30 bars at 10:00. The
    setup below confirms at 09:57; under the default it does not appear until
    the 60th bar of the session closes, by which time the limit price is half
    an hour stale.
    """
    sofar = day[day["ts"] <= et(KNOWN_CONFIRM_ET)]

    assert generate_orders(sofar, LIVE_CONFIG).shape[0] == 1
    assert generate_orders(sofar, LTFSweepConfig()).empty

    late = day[day["ts"] <= et("10:29")]
    assert generate_orders(late, LTFSweepConfig()).shape[0] == 1, (
        "the default should catch up at the 60th session bar"
    )


@needs_data
def test_the_default_threshold_is_untouched(day):
    """Whatever live needs, the published backtest numbers must not move."""
    assert LTFSweepConfig().min_session_bars == 60


# --------------------------------------------------------------------------
# replay stability -- the property the whole design rests on

@needs_data
def test_a_confirmed_setup_never_changes_its_prices(day):
    """Re-running over a growing frame must not revise an order already sent.

    This is the guarantee that makes it safe to place on the first sighting. If
    an entry could still move, the engine would leave a live limit at a price
    nothing cancels -- the orphan order that produced fills hours outside the
    session in Pine v4.
    """
    seen: dict[str, tuple] = {}
    for minute in pd.date_range(et("09:30"), et("11:05"), freq="1min"):
        orders = generate_orders(day[day["ts"] <= minute], LIVE_CONFIG)
        for row in orders.itertuples():
            key = order_key(row)
            prices = (row.entry_price, row.stop_price, row.target_price,
                      row.direction, row.trading_date)
            if key in seen:
                assert prices == seen[key], f"{key} changed after it was confirmed"
            else:
                seen[key] = prices
    assert seen, "the replay produced no orders at all"


@needs_data
def test_the_engine_sends_a_setup_once_and_only_once(day):
    """Sixty ticks over the same confirmed setup must place one order."""
    eng = engine(day)
    for minute in pd.date_range(et("09:30"), et("10:55"), freq="1min"):
        eng.tick(minute)

    assert len(eng.broker.sent) == 1
    sent = eng.broker.sent[0]
    assert sent["limitPrice"] == pytest.approx(KNOWN_ENTRY, abs=0.01)
    assert sent["side"] == 1, "the known setup is a short"
    assert sent["stopLossBracket"]["ticks"] > 0
    assert sent["takeProfitBracket"]["ticks"] > 0


@needs_data
def test_nothing_is_sent_before_the_setup_confirms(day):
    eng = engine(day)
    for minute in pd.date_range(et("09:30"), et("09:56"), freq="1min"):
        eng.tick(minute)
    assert eng.broker.sent == []


@needs_data
def test_the_order_goes_out_the_instant_the_setup_confirms(day):
    """No bar of lag between the signal being knowable and the order resting.

    The setup confirms at 09:57 -- the close of the 09:56 bar. At 09:57:02 the
    engine has that bar, and ``backtest/engine.py`` opens its fill window at
    exactly 09:57:00, so the two now agree to within the poll offset.

    Getting here took fixing three separate places where a value was read off
    "the bars I happen to have" instead of off the session's schedule
    (``min_session_bars``, the CHoCH deadline, and ``minute_cutoff``). Each one
    cost a bar, and a bar is expensive: fills inside the confirming bar are
    14.3% of trades and $4,209 of $16,409 over 2024-2026. If this test starts
    failing at 09:57 and passing at 09:58, one of those three has regressed.
    """
    eng = engine(day)
    eng.tick(et("09:56"))
    assert eng.broker.sent == [], "the 09:56 bar has not closed at 09:56"

    eng.tick(et("09:57"))
    assert len(eng.broker.sent) == 1, "and it must go out the moment it has"

    order = eng.broker.sent[0]
    assert order["limitPrice"] == pytest.approx(KNOWN_ENTRY, abs=0.01)


# --------------------------------------------------------------------------
# the entry deadline, the flat, and finishing

#: A deadline early enough that the known 09:57 setup is placed before it.
EARLY = 10 * 60


def to_deadline(eng, first="09:30", last="10:05"):
    for minute in pd.date_range(et(first), et(last), freq="1min"):
        eng.tick(minute)


@needs_data
def test_the_deadline_cancels_what_has_not_filled(day):
    """An order left resting when the process exits can fill hours later,
    unattended, against a stop and target priced for a different market."""
    eng = engine(day, cutoff_minute=EARLY)
    to_deadline(eng, last="09:59")
    assert eng.outstanding, "expected a resting order before the deadline"

    eng.tick(et("10:00"))

    assert eng.past_deadline
    assert eng.broker.cancelled, "the resting order was not cancelled"
    assert eng.outstanding == {}


@needs_data
def test_no_new_orders_after_the_deadline(day):
    eng = engine(day, cutoff_minute=9 * 60 + 45)
    to_deadline(eng, last="10:55")
    assert eng.broker.sent == [], "the setup confirms at 09:57, after this deadline"


@needs_data
def test_an_order_that_already_filled_is_not_cancelled(day, tmp_path):
    """A filled entry has a live position behind it; its id is not ours to cancel.

    Cancelling blind would make every filled trade log an error at the
    deadline, and once errors there are routine, a cancel that really did fail
    -- leaving an order resting overnight -- stops being visible.
    """
    journal = Journal(tmp_path / "j.jsonl")
    eng = engine(day, cutoff_minute=EARLY)
    eng.journal = journal
    to_deadline(eng, last="09:59")
    (order_id,) = eng.outstanding.values()

    eng.broker.filled.add(order_id)          # the entry got hit
    eng.tick(et("10:00"))

    assert eng.broker.cancelled == [], "a filled order must not be cancelled"
    assert eng.outstanding == {}
    events = [row["event"] for row in journal.read()]
    assert "no_longer_resting" in events and "cancel_failed" not in events


@needs_data
def test_an_unreadable_order_book_cancels_everything_sent(day, tmp_path):
    """When the engine cannot see what is resting, it errs towards cancelling."""
    from bridge.topstepx import BrokerError

    journal = Journal(tmp_path / "j.jsonl")
    eng = engine(day, cutoff_minute=EARLY)
    eng.journal = journal
    to_deadline(eng, last="09:59")

    def boom():
        raise BrokerError("searchOpen is unavailable")

    eng.broker.open_orders = boom
    eng.tick(et("10:00"))

    assert eng.broker.cancelled, "an unreadable book must not mean doing nothing"
    assert "open_orders_failed" in [row["event"] for row in journal.read()]


@needs_data
def test_a_failed_cancel_is_recorded_rather_than_swallowed(day, tmp_path):
    """An order still live past the deadline is the thing to shout about."""
    from bridge.topstepx import BrokerError

    journal = Journal(tmp_path / "j.jsonl")
    eng = engine(day, cutoff_minute=EARLY)
    eng.journal = journal
    to_deadline(eng, last="09:59")

    def boom(order_id):
        raise BrokerError("no")

    eng.broker.cancel = boom
    eng.tick(et("10:00"))

    assert "cancel_failed" in [row["event"] for row in journal.read()]
    assert eng.outstanding, "a cancel that failed must not be forgotten"
    assert not eng.finished_clean, "and the exit code must not say all is well"


# --------------------------------------------------------------------------
# finishing: a statement about the account, not about the clock

@needs_data
def test_the_day_ends_when_nothing_is_left_open(day):
    """The whole point: stop on done, not at an hour someone chose."""
    eng = engine(day, cutoff_minute=EARLY)
    to_deadline(eng, last="09:59")
    assert not eng.closed, "a resting order is not a finished day"

    eng.tick(et("10:00"))          # deadline cancels it, and that is clean look 1
    assert not eng.closed, "one clean look is not enough"

    eng.tick(et("10:01"))          # clean look 2
    assert eng.closed and eng.finished_clean


@needs_data
def test_an_open_position_keeps_the_engine_up(day):
    """The bracket is the broker's, but the day is not over while it is live."""
    eng = engine(day, cutoff_minute=EARLY)
    to_deadline(eng, last="09:59")
    eng.broker.position_size = 4          # the entry filled

    for minute in pd.date_range(et("10:00"), et("10:10"), freq="1min"):
        eng.tick(minute)
    assert not eng.closed, "a live position must keep the process alive"

    eng.broker.position_size = 0          # the bracket took it out
    eng.tick(et("10:11"))
    eng.tick(et("10:12"))
    assert eng.closed and eng.finished_clean


def test_a_query_that_fails_is_never_a_finished_day():
    """Not knowing is not the same as nothing being there."""
    from bridge.topstepx import BrokerError

    conf = cfg(cutoff_minute=9 * 60)
    now = pd.Timestamp("2026-09-01 13:30", tz="UTC")      # 09:30 ET, past it
    broker = DryRunBroker(conf, bars=frame(now - pd.Timedelta(minutes=1)))
    eng = LiveEngine(cfg=conf, broker=broker, journal=Journal(None), risk_usd=1000.0)

    def boom():
        raise BrokerError("searchOpen is unavailable")

    broker.open_positions = boom
    for _ in range(5):
        eng.tick(now)
    assert not eng.closed, "an unanswered question must keep the machine awake"


def test_a_clean_run_of_checks_must_be_consecutive():
    """A position reappearing resets the count rather than topping it up."""
    conf = cfg(cutoff_minute=9 * 60)
    now = pd.Timestamp("2026-09-01 13:30", tz="UTC")
    broker = DryRunBroker(conf, bars=frame(now - pd.Timedelta(minutes=1)))
    eng = LiveEngine(cfg=conf, broker=broker, journal=Journal(None), risk_usd=1000.0)

    eng.tick(now)                                   # clean: 1
    broker.position_size = 2
    eng.tick(now)                                   # not clean: back to 0
    assert not eng.closed
    broker.position_size = 0
    eng.tick(now)                                   # clean: 1 again
    assert not eng.closed, "the earlier clean check must not still count"
    eng.tick(now)                                   # clean: 2
    assert eng.closed


def test_the_flat_time_closes_an_open_position(tmp_path):
    """Five of 550 out-of-sample trades reach 16:00 still open, and those are
    the ones with nobody watching."""
    journal = Journal(tmp_path / "j.jsonl")
    conf = cfg(cutoff_minute=9 * 60, flat_minute=9 * 60 + 30)
    now = pd.Timestamp("2026-09-01 13:30", tz="UTC")      # 09:30 ET
    broker = DryRunBroker(conf, bars=frame(now - pd.Timedelta(minutes=1)))
    broker.position_size = 3
    eng = LiveEngine(cfg=conf, broker=broker, journal=journal, risk_usd=1000.0)

    eng.tick(now)

    assert broker.closed_positions == 1
    assert broker.position_size == 0
    assert "flattened" in [row["event"] for row in journal.read()]


def test_the_flat_time_sends_nothing_when_flat(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    conf = cfg(cutoff_minute=9 * 60, flat_minute=9 * 60 + 30)
    now = pd.Timestamp("2026-09-01 13:30", tz="UTC")
    broker = DryRunBroker(conf, bars=frame(now - pd.Timedelta(minutes=1)))
    eng = LiveEngine(cfg=conf, broker=broker, journal=journal, risk_usd=1000.0)

    eng.tick(now)

    assert broker.closed_positions == 0
    assert "flattened" not in [row["event"] for row in journal.read()]


def test_the_journal_says_which_account_it_ran_on(tmp_path):
    """Practice and Combine are the same program writing the same file.

    The go-live sequence is "prove it on Practice, then point it at the
    Combine", so a journal that cannot say which side produced it is unreadable
    exactly when it matters -- when a reconciliation looks wrong.
    """
    journal = Journal(tmp_path / "j.jsonl")
    conf = cfg(account_id=778899, live=True)
    eng = LiveEngine(cfg=conf, broker=DryRunBroker(conf), journal=journal,
                     risk_usd=250.0)

    eng.announce()

    (row,) = journal.read()
    assert row["event"] == "session_start"
    assert row["account_id"] == 778899
    assert row["preset"] == "Topstep 50K"
    assert row["risk_usd"] == 250.0
    assert row["live"] is True
    assert row["contract_id"] == "CON.F.US.MNQ.Z26"


def test_a_dry_run_says_so(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    conf = cfg(live=False)
    LiveEngine(cfg=conf, broker=DryRunBroker(conf), journal=journal,
               risk_usd=1000.0).announce()
    assert journal.read()[0]["live"] is False


def test_the_session_header_carries_no_credentials(tmp_path):
    """A journal is a file that gets copied into issues and screenshots.

    Asserted rather than left to a reading of the code: the obvious way to write
    this line is ``asdict(cfg)``, which would put the API key and the webhook
    secret in every run.
    """
    journal = Journal(tmp_path / "j.jsonl")
    conf = cfg(api_key="SUPERSECRETKEY123", webhook_secret="hunter2-and-friends",
               username="alice")
    LiveEngine(cfg=conf, broker=DryRunBroker(conf), journal=journal,
               risk_usd=1000.0).announce()

    blob = (tmp_path / "j.jsonl").read_text()
    for secret in ("SUPERSECRETKEY123", "hunter2-and-friends", "alice"):
        assert secret not in blob, f"{secret} reached the journal"


def test_the_bridge_clock_matches_the_strategy(monkeypatch):
    """Two statements of one schedule are only safe while they agree."""
    for name in ("TOPSTEPX_USERNAME", "TOPSTEPX_API_KEY"):
        monkeypatch.setenv(name, "x")
    conf = Config.from_env()
    assert conf.cutoff_minute == LTFSweepConfig().entry_deadline
    assert conf.flat_minute == LTFSweepConfig().exit_minute


# --------------------------------------------------------------------------
# stale bars

def frame(last_ts: pd.Timestamp, n: int = 5) -> pd.DataFrame:
    ts = pd.date_range(end=last_ts, periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({"ts": ts, "open": 100.0, "high": 101.0, "low": 99.0,
                         "close": 100.5, "volume": 10.0})


def test_fresh_bars_are_accepted():
    now = pd.Timestamp("2026-09-01 14:00", tz="UTC")
    eng = LiveEngine(cfg=cfg(), broker=DryRunBroker(cfg()), journal=Journal(None),
                     risk_usd=1000.0)
    assert eng.stale_reason(frame(now - pd.Timedelta(minutes=1)), now) is None


def test_a_delayed_feed_stops_the_engine():
    """A ten-minute-delayed feed produces signals that all look normal."""
    now = pd.Timestamp("2026-09-01 14:00", tz="UTC")
    eng = LiveEngine(cfg=cfg(), broker=DryRunBroker(cfg()), journal=Journal(None),
                     risk_usd=1000.0)
    why = eng.stale_reason(frame(now - pd.Timedelta(minutes=10)), now)
    assert why and "old" in why


def test_bars_stamped_at_the_close_are_refused():
    """A closed 1-minute bar stamped at its open cannot be under a minute old.

    If it is, the feed stamps at the close and every signal sits one bar out of
    place. Guessing which convention is in use would be a silent one-bar shift
    in both directions.
    """
    now = pd.Timestamp("2026-09-01 14:00", tz="UTC")
    eng = LiveEngine(cfg=cfg(), broker=DryRunBroker(cfg()), journal=Journal(None),
                     risk_usd=1000.0)
    why = eng.stale_reason(frame(now), now)
    assert why and "close" in why


def test_a_dead_feed_still_ends_the_session():
    """The cutoff has to outrank the feed check, not queue behind it.

    A feed that stops publishing is precisely when an order must not be left
    resting with nobody watching. If staleness returned first, a broken feed
    would postpone the very step that cancels -- and the process would sit there
    holding a live limit order until someone noticed.
    """
    from bridge.topstepx import Order

    conf = cfg(cutoff_minute=9 * 60 + 30)
    now = pd.Timestamp("2026-09-01 13:30", tz="UTC")           # 09:30 ET
    broker = DryRunBroker(conf, bars=frame(now - pd.Timedelta(hours=2)))
    eng = LiveEngine(cfg=conf, broker=broker, journal=Journal(None), risk_usd=1000.0)

    # A real resting order, so the broker's book agrees it exists.
    placed = broker.place(Order(side="sell", size=1, entry=100.0, stop=110.0,
                                target=90.0))
    eng.outstanding["stuck"] = placed["orderId"]

    eng.tick(now)
    assert broker.cancelled == [placed["orderId"]], "stale bars must not block the deadline"

    eng.tick(now)
    assert eng.closed, "a stale feed must not hold the session open"


def test_no_bars_at_all_is_not_silence():
    now = pd.Timestamp("2026-09-01 14:00", tz="UTC")
    eng = LiveEngine(cfg=cfg(), broker=DryRunBroker(cfg()), journal=Journal(None),
                     risk_usd=1000.0)
    assert eng.stale_reason(pd.DataFrame(), now) == "no bars returned"


def test_a_stale_tick_places_nothing_and_says_why(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    conf = cfg()
    now = pd.Timestamp("2026-09-01 14:00", tz="UTC")
    broker = DryRunBroker(conf, bars=frame(now - pd.Timedelta(minutes=30)))
    eng = LiveEngine(cfg=conf, broker=broker, journal=journal, risk_usd=1000.0)

    assert eng.tick(now) == []
    assert broker.sent == []
    assert [row["event"] for row in journal.read()] == ["stale_feed"]


# --------------------------------------------------------------------------
# the request the bar feed actually sends

def test_partial_bars_are_never_requested():
    """``confirmed_at`` at the API boundary: a bar with no close has no signal."""
    body = bars_payload(cfg(), pd.Timestamp("2026-09-01 13:30", tz="UTC"),
                        pd.Timestamp("2026-09-01 15:00", tz="UTC"))
    assert body["includePartialBar"] is False
    assert body["unit"] == 2 and body["unitNumber"] == 1
    assert body["startTime"] == "2026-09-01T13:30:00Z"


def test_the_data_subscription_follows_the_account():
    """A practice account on the live feed gets an empty array, not an error."""
    now = pd.Timestamp.now(tz="UTC")
    assert bars_payload(cfg(live_data=False), now, now)["live"] is False
    assert bars_payload(cfg(live_data=True), now, now)["live"] is True


def test_bars_come_back_oldest_first_whatever_the_order_sent():
    out = parse_bars({"bars": [
        {"t": "2026-09-01T14:02:00Z", "o": 3, "h": 3, "l": 3, "c": 3, "v": 1},
        {"t": "2026-09-01T14:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
        {"t": "2026-09-01T14:01:00Z", "o": 2, "h": 2, "l": 2, "c": 2, "v": 1},
    ]})
    assert list(out["open"]) == [1.0, 2.0, 3.0]
    assert out["ts"].is_monotonic_increasing


def test_an_empty_response_is_an_empty_frame_not_a_crash():
    out = parse_bars({"bars": [], "success": True})
    assert out.empty and list(out.columns) == ["ts", "open", "high", "low", "close", "volume"]


# --------------------------------------------------------------------------
# small pieces

def test_the_trading_day_starts_at_the_previous_evening_roll():
    """The 15m pools are built before the open, so the fetch must reach back."""
    morning = pd.Timestamp("2026-09-01 09:35", tz=NY).tz_convert("UTC")
    assert day_start(morning).tz_convert(NY) == pd.Timestamp("2026-08-31 18:00", tz=NY)

    evening = pd.Timestamp("2026-09-01 19:10", tz=NY).tz_convert("UTC")
    assert day_start(evening).tz_convert(NY) == pd.Timestamp("2026-09-01 18:00", tz=NY)


def test_et_minute_reads_new_york_not_utc():
    assert et_minute(pd.Timestamp("2026-09-01 13:30", tz="UTC")) == 9 * 60 + 30


def test_size_comes_from_the_stop_distance():
    conf = cfg()
    assert position_size(100.0, 90.0, conf, 1000.0) == 50   # 10 pts x $2 = $20
    assert position_size(100.0, 100.0, conf, 1000.0) == 0   # no stop, no size


def test_the_loop_stops_once_the_session_closes():
    """The runner must exit at the cutoff rather than spin until killed."""
    conf = cfg(cutoff_minute=9 * 60 + 31)
    now = pd.Timestamp("2026-09-01 13:30", tz="UTC")
    broker = DryRunBroker(conf, bars=frame(now - pd.Timedelta(minutes=1)))
    eng = LiveEngine(cfg=conf, broker=broker, journal=Journal(None), risk_usd=1000.0)

    minutes = itertools.count()

    def clock():
        return now + pd.Timedelta(minutes=next(minutes))

    run(eng, sleep=lambda _s: None, now=clock)
    assert eng.closed
