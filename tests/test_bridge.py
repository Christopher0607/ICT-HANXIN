"""Tests for the webhook bridge.

Nothing here touches the network. The broker surface is small enough to fake,
and the point of these tests is the part that decides *whether* to send, not
the sending.
"""

from __future__ import annotations

import json
import pathlib
import re
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

from bridge.config import PRESETS, Config
from bridge.guards import (BLOCK_DAILY_CAP, BLOCK_LOSS_LIMIT, BLOCK_TARGET_MADE, OK,
                           AccountState, block_reason, clamp_size)
from bridge.server import Bridge, Rejected, alert_key, make_handler, parse_alert
from bridge.topstepx import (SIDE_BUY, SIDE_SELL, BrokerError, DryRunBroker, Order,
                             bracket_ticks, order_payload)

PINE = pathlib.Path(__file__).resolve().parent.parent / "tradingview" / "ltf_sweep_strategy.pine"


def cfg(**over) -> Config:
    base = dict(
        username="u", api_key="k", base_url="http://example.invalid", account_id=1,
        contract_id="CON.F.US.MNQ.Z26", tick_size=0.25, point_value=2.0,
        webhook_secret="s3cret", live=False, preset="Topstep 50K",
        account_start=50000.0, profit_target=3000.0, max_loss_limit=2000.0,
        daily_loss_limit=1000.0, max_contracts=50, safety_mult=1.5,
    )
    base.update(over)
    return Config(**base)


# --------------------------------------------------------------------------
# the two guard implementations must agree

def test_preset_table_matches_the_pine_scripts():
    """One rule, two implementations -- they are only safe while they agree.

    The Pine guard runs on the chart and this one runs on the server, and they
    are deliberately independent so a tampered chart cannot open a hole. That
    only helps if the numbers are the same, and nothing else would notice if
    they drifted.
    """
    src = PINE.read_text()
    names = re.search(r'array\.indexof\(array\.from\((.*?)\), firmPreset\)', src).group(1)
    order = [n.strip().strip('"') for n in names.split(",")]

    def row(var):
        nums = re.search(rf'var \w+ +{var} += .*?array\.from\(([^)]*)\), pIdx\)', src).group(1)
        return [float(x.strip().rstrip(".")) for x in nums.split(",")]

    targets, limits = row("profitTarget"), row("maxLossLimit")
    dailies, micros = row("dailyLossLimit"), row("maxMicros")
    assert order == list(PRESETS), "preset names differ between Pine and the bridge"
    for i, name in enumerate(order):
        assert PRESETS[name] == (targets[i], limits[i], dailies[i], int(micros[i])), (
            f"{name} differs: Pine has "
            f"{(targets[i], limits[i], dailies[i], int(micros[i]))}, "
            f"the bridge has {PRESETS[name]}"
        )


def test_pine_and_bridge_use_the_same_block_reason_codes():
    src = PINE.read_text()
    assert "plannedRisk * safetyMult ? 1 :" in src and BLOCK_LOSS_LIMIT == 1
    assert "-dailyLossLimit ? 2 :" in src and BLOCK_DAILY_CAP == 2
    assert "netP >= profitTarget ? 3 : 0" in src and BLOCK_TARGET_MADE == 3


# --------------------------------------------------------------------------
# guards

def test_a_healthy_account_is_allowed_to_trade():
    st = AccountState()
    st.roll_day("2026-09-01", cfg())
    assert block_reason(st, 200.0, cfg()) == OK


def test_blocks_when_the_loss_limit_is_within_reach():
    """Down 1,500, so a fresh day opens with only 500 of room to the floor.

    At 1.5x safety a 400 trade needs 600 and must be refused; a 200 trade
    needs 300 and is allowed.
    """
    c = cfg()
    st = AccountState()
    st.roll_day("2026-09-01", c)
    st.realized = -1500.0
    st.roll_day("2026-09-02", c)          # new day, so the daily cap is clear
    assert st.equity(c) - st.mll_floor == 500.0
    assert block_reason(st, 400.0, c) == BLOCK_LOSS_LIMIT
    assert block_reason(st, 200.0, c) == OK


def test_which_rule_binds_first_depends_on_the_account_state():
    """Worth pinning down, because it is not obvious from either limit alone.

    On a fresh Topstep 50K the daily cap (1,000) is tighter than the loss-limit
    guard (2,000 of room / 1.5 = 1,333), so an oversized trade is stopped by
    the daily rule. Once the floor has trailed up, room shrinks and the
    loss-limit rule becomes the binding one instead.
    """
    c = cfg()
    risk = 1200.0

    # Fresh: 2,000 of room, so 1,200 x 1.5 = 1,800 still fits. The daily cap
    # is what stops it.
    fresh = AccountState()
    fresh.roll_day("2026-09-01", c)
    assert fresh.equity(c) - fresh.mll_floor == 2000.0
    assert block_reason(fresh, risk, c) == BLOCK_DAILY_CAP

    # Up 1,800 then giving 800 back: the floor ratcheted to 49,800 and stayed
    # there, so room is down to 1,200 and the same trade now needs more room
    # than exists. The loss limit binds first.
    trailed = AccountState()
    trailed.roll_day("2026-09-01", c)
    trailed.realized = 1800.0
    trailed.roll_day("2026-09-02", c)
    trailed.realized = 1000.0
    trailed.roll_day("2026-09-03", c)
    assert trailed.mll_floor == 49800.0
    assert trailed.equity(c) - trailed.mll_floor == 1200.0
    assert block_reason(trailed, risk, c) == BLOCK_LOSS_LIMIT


def test_blocks_a_trade_that_would_breach_the_daily_cap():
    c = cfg()
    st = AccountState()
    st.roll_day("2026-09-01", c)
    st.realized = -900.0          # already down 900 against a 1,000 cap
    assert block_reason(st, 200.0, c) == BLOCK_DAILY_CAP


def test_stops_once_the_profit_target_is_made():
    c = cfg()
    st = AccountState()
    st.roll_day("2026-09-01", c)
    st.realized = 3000.0
    assert block_reason(st, 200.0, c) == BLOCK_TARGET_MADE


def test_the_loss_floor_ratchets_up_and_never_down():
    c = cfg()
    st = AccountState()
    st.roll_day("2026-09-01", c)
    assert st.mll_floor == 48000.0
    st.realized = 1200.0
    st.roll_day("2026-09-02", c)
    assert st.mll_floor == 49200.0          # trailed up with the closing balance
    st.realized = 400.0
    st.roll_day("2026-09-03", c)
    assert st.mll_floor == 49200.0, "the floor must never move back down"


def test_open_profit_counts_toward_the_breach_test():
    """Topstep checks the limit in real time, including unrealised P&L.

    Run on an Apex-shaped account (no daily cap) so the loss-limit rule is the
    only one that can fire and the open P&L is what is actually being tested.
    """
    c = cfg(daily_loss_limit=1e9)
    st = AccountState()
    st.roll_day("2026-09-01", c)
    assert block_reason(st, 200.0, c) == OK
    st.open_pnl = -1900.0                 # position under water, nothing closed
    assert st.equity(c) - st.mll_floor == 100.0
    assert block_reason(st, 200.0, c) == BLOCK_LOSS_LIMIT


def test_oversize_is_cut_down_not_dropped():
    assert clamp_size(80, cfg()) == 50
    assert clamp_size(12, cfg()) == 12
    assert clamp_size(0, cfg()) == 0


# --------------------------------------------------------------------------
# order construction

def test_brackets_are_expressed_in_ticks_not_prices():
    """The bracket fields are tick distances. Passing a price would place the
    stop thousands of ticks away and still look like a successful order."""
    assert bracket_ticks(29428.25, 29480.0, 0.25) == 207
    assert bracket_ticks(29428.25, 29376.5, 0.25) == 207


def test_a_bracket_inside_one_tick_is_refused():
    with pytest.raises(BrokerError, match="one tick"):
        bracket_ticks(29428.25, 29428.30, 0.25)


def test_order_payload_uses_the_documented_enums():
    c = cfg()
    buy = order_payload(Order("buy", 2, 100.0, 95.0, 105.0), c)
    sell = order_payload(Order("sell", 2, 100.0, 105.0, 95.0), c)
    assert buy["side"] == SIDE_BUY and sell["side"] == SIDE_SELL
    assert buy["type"] == 1 and buy["size"] == 2
    assert buy["limitPrice"] == 100.0
    assert buy["stopLossBracket"]["ticks"] == 20
    assert buy["takeProfitBracket"]["ticks"] == 20


def test_zero_size_orders_are_refused():
    with pytest.raises(BrokerError):
        order_payload(Order("buy", 0, 100.0, 95.0, 105.0), cfg())


# --------------------------------------------------------------------------
# alert handling

ALERT = {"strategy": "ltf_sweep", "action": "sell", "symbol": "MNQ1!", "qty": 4,
         "entry": 29428.25, "stop": 29480.0, "target": 29376.5,
         "risk_points": 51.75, "time": "2026-08-31T14:18:00Z"}


def test_a_good_alert_places_one_order():
    c = cfg()
    broker = DryRunBroker(c)
    out = Bridge(c, broker).handle(dict(ALERT))
    assert out["status"] == "placed"
    assert len(broker.sent) == 1
    assert broker.sent[0]["size"] == 4


def test_a_replayed_alert_is_refused():
    """TradingView resends alerts; a duplicate here is a duplicate position."""
    c = cfg()
    broker = DryRunBroker(c)
    b = Bridge(c, broker)
    b.handle(dict(ALERT))
    with pytest.raises(Rejected, match="duplicate"):
        b.handle(dict(ALERT))
    assert len(broker.sent) == 1


def test_a_later_signal_the_same_day_is_not_a_duplicate():
    c = cfg()
    b = Bridge(c, DryRunBroker(c))
    b.handle(dict(ALERT))
    second = dict(ALERT, time="2026-08-31T15:02:00Z")
    assert b.handle(second)["status"] == "placed"


def test_exit_alerts_place_nothing():
    """The stop and target ride on the bracket sent with the entry."""
    c = cfg()
    broker = DryRunBroker(c)
    out = Bridge(c, broker).handle(dict(ALERT, action="exit_target"))
    assert out["status"] == "noted"
    assert broker.sent == []


def test_guards_are_applied_to_incoming_alerts():
    c = cfg(max_loss_limit=200.0)      # floor sits just below the account
    broker = DryRunBroker(c)
    with pytest.raises(Rejected, match="loss limit"):
        Bridge(c, broker).handle(dict(ALERT))
    assert broker.sent == []


def test_oversized_alerts_are_cut_to_the_ceiling():
    c = cfg(max_contracts=3)
    broker = DryRunBroker(c)
    Bridge(c, broker).handle(dict(ALERT, qty=40))
    assert broker.sent[0]["size"] == 3


@pytest.mark.parametrize("bad", [
    b"not json", b"[]", b'{"action":"launch"}', b'{"action":"buy"}',
])
def test_malformed_alerts_are_refused(bad):
    with pytest.raises(Rejected):
        payload = parse_alert(bad)
        Bridge(cfg(), DryRunBroker(cfg())).handle(payload)


def test_alert_key_ignores_arrival_time():
    assert alert_key(ALERT) == alert_key(dict(ALERT))
    assert alert_key(ALERT) != alert_key(dict(ALERT, time="2026-08-31T15:02:00Z"))


# --------------------------------------------------------------------------
# the HTTP surface

@pytest.fixture
def server():
    c = cfg()
    broker = DryRunBroker(c)
    bridge = Bridge(c, broker)
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(bridge, c.webhook_secret))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_port}", broker
    httpd.shutdown()
    httpd.server_close()


def post(url: str, body: dict, secret: str | None = None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    if secret is not None:
        req.add_header("X-Webhook-Secret", secret)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_the_right_secret_gets_the_order_placed(server):
    url, broker = server
    status, body = post(url, dict(ALERT), secret="s3cret")
    assert status == 200 and body["status"] == "placed"
    assert len(broker.sent) == 1


def test_a_wrong_secret_is_rejected(server):
    url, broker = server
    status, _ = post(url, dict(ALERT), secret="wrong")
    assert status == 401
    assert broker.sent == []


def test_a_missing_secret_is_rejected(server):
    url, broker = server
    status, _ = post(url, dict(ALERT))
    assert status == 401
    assert broker.sent == []


def test_the_secret_may_travel_in_the_body(server):
    """Not every TradingView plan can set custom headers."""
    url, broker = server
    status, body = post(url, dict(ALERT, secret="s3cret"))
    assert status == 200 and body["status"] == "placed"
    assert "secret" not in broker.sent[0].get("customTag", "")


def test_a_blocked_alert_answers_200_so_tradingview_stops_retrying(server):
    url, broker = server
    status, body = post(url, dict(ALERT, qty=0), secret="s3cret")
    assert status == 200 and body["status"] == "skipped"
    assert broker.sent == []
