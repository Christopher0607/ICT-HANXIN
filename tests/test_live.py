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
        webhook_secret="", live=False, live_data=False, cutoff_minute=11 * 60,
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
    for minute in pd.date_range(et("09:30"), et("09:57"), freq="1min"):
        eng.tick(minute)
    assert eng.broker.sent == []


@needs_data
def test_the_order_lands_one_bar_after_confirmation_and_that_costs_money(day):
    """The engine cannot act until the confirming bar has closed.

    The setup confirms at 09:57, which is the close of the 09:56 bar and the
    open of the 09:57 one. ``backtest/engine.py`` starts its fill window right
    there, so the backtest may fill inside the 09:57 bar -- legitimate, but only
    for an order resting from the first instant, which means zero latency. This
    engine sees the 09:57 bar once it closes and places at 09:58.

    Measured over 2024-2026 under the 11:00 rule, fills inside the confirming
    bar are 14.3% of trades and $4,209 of $16,409. That is the difference
    between the backtest figure and the $12,200 this can actually reach, so the
    lag is asserted here rather than left to be optimised away by someone who
    does not know what it is holding up.
    """
    eng = engine(day)
    eng.tick(et("09:57"))
    assert eng.broker.sent == [], "the 09:57 bar has not closed at 09:57"

    eng.tick(et("09:58"))
    assert len(eng.broker.sent) == 1, "and it must go out as soon as it has"


# --------------------------------------------------------------------------
# the cutoff

@needs_data
def test_the_cutoff_cancels_what_has_not_filled(day):
    """An order left resting past the cutoff can fill hours later, unattended.

    Cancelling is the whole point of stopping at a fixed time rather than just
    walking away from the machine.
    """
    eng = engine(day)
    for minute in pd.date_range(et("09:30"), et("10:55"), freq="1min"):
        eng.tick(minute)
    assert eng.outstanding, "expected a resting order before the cutoff"

    eng.tick(et("11:00"))

    assert eng.closed
    assert eng.broker.cancelled, "the resting order was not cancelled"
    assert eng.outstanding == {}


@needs_data
def test_no_new_orders_after_the_cutoff(day):
    eng = engine(day, cutoff_minute=9 * 60 + 45)
    for minute in pd.date_range(et("09:30"), et("10:55"), freq="1min"):
        eng.tick(minute)
    assert eng.broker.sent == [], "the setup confirms at 09:57, after this cutoff"
    assert eng.closed


@needs_data
def test_an_order_that_already_filled_is_not_cancelled(day, tmp_path):
    """A filled entry has a live position behind it; its id is not ours to cancel.

    Cancelling blind would make every filled trade log an error at the cutoff,
    and once errors there are routine, a cancel that really did fail -- leaving
    an order resting overnight -- stops being visible.
    """
    journal = Journal(tmp_path / "j.jsonl")
    eng = engine(day)
    eng.journal = journal
    for minute in pd.date_range(et("09:30"), et("10:55"), freq="1min"):
        eng.tick(minute)
    (order_id,) = eng.outstanding.values()

    eng.broker.filled.add(order_id)          # the entry got hit
    eng.tick(et("11:00"))

    assert eng.broker.cancelled == [], "a filled order must not be cancelled"
    assert eng.outstanding == {}
    events = [row["event"] for row in journal.read()]
    assert "no_longer_resting" in events and "cancel_failed" not in events


@needs_data
def test_an_unreadable_order_book_cancels_everything_sent(day, tmp_path):
    """When the engine cannot see what is resting, it errs towards cancelling."""
    from bridge.topstepx import BrokerError

    journal = Journal(tmp_path / "j.jsonl")
    eng = engine(day)
    eng.journal = journal
    for minute in pd.date_range(et("09:30"), et("10:55"), freq="1min"):
        eng.tick(minute)

    def boom():
        raise BrokerError("searchOpen is unavailable")

    eng.broker.open_orders = boom
    eng.tick(et("11:00"))

    assert eng.broker.cancelled, "an unreadable book must not mean doing nothing"
    events = [row["event"] for row in journal.read()]
    assert "open_orders_failed" in events


@needs_data
def test_a_failed_cancel_is_recorded_rather_than_swallowed(day, tmp_path):
    """An order still live past the cutoff is the thing to shout about."""
    journal = Journal(tmp_path / "j.jsonl")
    eng = engine(day)
    eng.journal = journal
    for minute in pd.date_range(et("09:30"), et("10:55"), freq="1min"):
        eng.tick(minute)

    from bridge.topstepx import BrokerError

    def boom(order_id):
        raise BrokerError("no")

    eng.broker.cancel = boom
    eng.tick(et("11:00"))

    events = [row["event"] for row in journal.read()]
    assert "cancel_failed" in events
    assert eng.outstanding, "a cancel that failed must not be forgotten"


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

    assert eng.closed, "a stale feed must not hold the session open"
    assert broker.cancelled == [placed["orderId"]]


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
