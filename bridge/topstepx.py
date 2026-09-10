"""Minimal ProjectX Gateway client (TopstepX).

Endpoints and payload shapes are from the ProjectX Gateway docs:

    POST /api/Auth/loginKey       {userName, apiKey} -> {token, success, errorCode}
    POST /api/Order/place         {accountId, contractId, type, side, size, ...}
                                  -> {orderId, success, errorCode, errorMessage}
    POST /api/Order/cancel        {accountId, orderId} -> {success, errorCode}
    POST /api/Order/searchOpen    {accountId} -> {orders: [{id, status, ...}], ...}
    POST /api/History/retrieveBars {contractId, live, startTime, endTime, unit,
                                    unitNumber, limit, includePartialBar}
                                  -> {bars: [{t, o, h, l, c, v}], success, ...}

Two things about that order payload are easy to get wrong and expensive:

* **Brackets are in TICKS, not prices.** ``stopLossBracket.ticks`` is a
  distance from the entry, so an absolute stop price has to be converted with
  the instrument's tick size. Passing a price straight through would place a
  stop thousands of ticks away and it would look like it worked.
* ``side`` is 0 for buy and 1 for sell, and ``type`` is 1 for limit -- both are
  bare integers, so a wrong one is a valid request for the wrong trade.

``DryRunBroker`` implements the same surface and records what it would have
sent. It is the default; sending real orders takes an explicit --live.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .config import Config

ORDER_TYPE_LIMIT = 1
SIDE_BUY = 0
SIDE_SELL = 1

#: retrieveBars aggregation units. 2 is minutes; the rest are here so a wrong
#: one reads as wrong at the call site rather than as a bare integer.
UNIT_SECOND, UNIT_MINUTE, UNIT_HOUR, UNIT_DAY, UNIT_WEEK, UNIT_MONTH = 1, 2, 3, 4, 5, 6

BAR_COLUMNS = ("ts", "open", "high", "low", "close", "volume")


class BrokerError(RuntimeError):
    pass


@dataclass
class Order:
    """A bracket order, in the terms this bridge speaks."""

    side: str          # "buy" or "sell"
    size: int
    entry: float
    stop: float
    target: float
    tag: str = ""


def bracket_ticks(entry: float, level: float, tick_size: float) -> int:
    """Distance from entry to a bracket level, in ticks, always positive."""
    ticks = round(abs(entry - level) / tick_size)
    if ticks < 1:
        raise BrokerError(
            f"bracket level {level} is less than one tick ({tick_size}) from the "
            f"entry {entry}; refusing to send a zero-distance stop or target"
        )
    return int(ticks)


def order_payload(order: Order, cfg: Config) -> dict[str, Any]:
    """Build the /api/Order/place body. Pure, so the tests can read it."""
    if order.size < 1:
        raise BrokerError(f"refusing to send an order for {order.size} contracts")
    if cfg.account_id is None:
        raise BrokerError("no account id; set TOPSTEPX_ACCOUNT_ID")
    return {
        "accountId": cfg.account_id,
        "contractId": cfg.contract_id,
        "type": ORDER_TYPE_LIMIT,
        "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
        "size": int(order.size),
        "limitPrice": round(order.entry, 4),
        "stopLossBracket": {"ticks": bracket_ticks(order.entry, order.stop, cfg.tick_size),
                            "type": ORDER_TYPE_LIMIT},
        "takeProfitBracket": {"ticks": bracket_ticks(order.entry, order.target, cfg.tick_size),
                              "type": ORDER_TYPE_LIMIT},
        "customTag": order.tag[:50],
    }


def bars_payload(cfg: Config, start, end, *, limit: int = 2000) -> dict[str, Any]:
    """Build the /api/History/retrieveBars body. Pure, so the tests can read it.

    ``includePartialBar`` is false and must stay false. It is the ``confirmed_at``
    contract at the API boundary: every detector in ``ict/`` reports the close of
    the last bar it depended on, and a bar that has not closed has no such close.
    Letting one through would let the sweep, the CHoCH and the FVG all be judged
    on a price still moving, which is repainting by another name.

    ``live`` selects the data subscription, so a practice account needs it false
    and a funded one true. Getting it wrong reads as an empty ``bars`` array, not
    as an error.
    """
    return {
        "contractId": cfg.contract_id,
        "live": cfg.live_data,
        "startTime": _iso(start),
        "endTime": _iso(end),
        "unit": UNIT_MINUTE,
        "unitNumber": 1,
        "limit": limit,
        "includePartialBar": False,
    }


def _iso(ts) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_bars(payload: dict[str, Any]) -> pd.DataFrame:
    """Turn a retrieveBars response into the frame the research engine expects.

    Returned oldest-first: the docs do not promise an order, and every detector
    reads the frame positionally.
    """
    rows = payload.get("bars") or []
    frame = pd.DataFrame(
        [(r["t"], r["o"], r["h"], r["l"], r["c"], r.get("v", 0)) for r in rows],
        columns=list(BAR_COLUMNS),
    )
    if frame.empty:
        return frame.astype({"ts": "datetime64[ns, UTC]", "open": "float64",
                             "high": "float64", "low": "float64",
                             "close": "float64", "volume": "float64"})
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, format="ISO8601")
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = frame[col].astype("float64")
    return frame.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


@dataclass
class DryRunBroker:
    """Records orders instead of sending them. The default."""

    cfg: Config
    sent: list[dict[str, Any]] = field(default_factory=list)
    cancelled: list[int] = field(default_factory=list)
    #: Order ids a test wants to look filled, so the cutoff path can be driven
    #: down both branches.
    filled: set[int] = field(default_factory=set)
    #: Bars to hand back, so a replay can drive the runner without a network.
    bars: pd.DataFrame | None = None

    def authenticate(self) -> None:
        return None

    def place(self, order: Order) -> dict[str, Any]:
        payload = order_payload(order, self.cfg)
        self.sent.append(payload)
        return {"orderId": -len(self.sent), "success": True, "errorCode": 0,
                "errorMessage": None, "dryRun": True}

    def cancel(self, order_id: int) -> dict[str, Any]:
        self.cancelled.append(int(order_id))
        return {"success": True, "errorCode": 0, "errorMessage": None, "dryRun": True}

    def open_orders(self) -> list[dict[str, Any]]:
        """Everything placed and not yet cancelled.

        Nothing fills in a dry run, so every order it sent is still resting.
        Tests that need a fill put the id in ``filled``.
        """
        gone = set(self.cancelled) | set(self.filled)
        return [{"id": -i - 1, "accountId": self.cfg.account_id}
                for i in range(len(self.sent)) if -i - 1 not in gone]

    def retrieve_bars(self, start, end, *, limit: int = 2000) -> pd.DataFrame:
        """Replay, modelling ``includePartialBar=False`` faithfully.

        A bar stamped at its open at ``t`` has not closed until ``t + 1min``, so
        asking at 09:58:05 must hand back 09:57 and nothing newer. Filtering on
        the open alone would leak a bar that is still forming, which makes a
        replay look one bar quicker than any real run can be -- exactly the
        optimism this whole module exists to keep out.
        """
        if self.bars is None:
            return parse_bars({})
        closes = self.bars["ts"] + pd.Timedelta(minutes=1)
        window = self.bars[(self.bars["ts"] >= pd.Timestamp(start))
                           & (closes <= pd.Timestamp(end))]
        return window.tail(limit).reset_index(drop=True)


@dataclass
class TopstepXBroker:
    """Live client. Only constructed when --live is passed."""

    cfg: Config
    token: str | None = None

    def _post(self, path: str, body: dict[str, Any], auth: bool = True) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.cfg.base_url}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "accept": "application/json"},
            method="POST",
        )
        if auth:
            if not self.token:
                raise BrokerError("not authenticated")
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                out = json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            # 429 is rate limiting; the caller decides whether to back off. The
            # body is echoed because ProjectX puts the real reason in it.
            raise BrokerError(f"{path} -> HTTP {exc.code}: {exc.read()[:400]!r}") from exc
        except urllib.error.URLError as exc:
            raise BrokerError(f"{path} -> {exc.reason}") from exc
        if not out.get("success", False):
            raise BrokerError(
                f"{path} -> errorCode {out.get('errorCode')}: {out.get('errorMessage')}"
            )
        return out

    def authenticate(self) -> None:
        out = self._post("/api/Auth/loginKey",
                         {"userName": self.cfg.username, "apiKey": self.cfg.api_key},
                         auth=False)
        self.token = out["token"]

    def place(self, order: Order) -> dict[str, Any]:
        return self._post("/api/Order/place", order_payload(order, self.cfg))

    def cancel(self, order_id: int) -> dict[str, Any]:
        return self._post("/api/Order/cancel",
                          {"accountId": self.cfg.account_id, "orderId": int(order_id)})

    def open_orders(self) -> list[dict[str, Any]]:
        """Orders still resting, so a cancel sweep can skip the ones that filled."""
        out = self._post("/api/Order/searchOpen", {"accountId": self.cfg.account_id})
        return out.get("orders", [])

    def retrieve_bars(self, start, end, *, limit: int = 2000) -> pd.DataFrame:
        return parse_bars(self._post("/api/History/retrieveBars",
                                     bars_payload(self.cfg, start, end, limit=limit)))

    def accounts(self) -> list[dict[str, Any]]:
        """List tradable accounts, to find the account id.

        NOTE: this endpoint's exact path and body could not be verified against
        the published docs, unlike loginKey and Order/place above. Prefer
        setting TOPSTEPX_ACCOUNT_ID explicitly and treat this as a convenience
        that may need correcting.
        """
        out = self._post("/api/Account/search", {"onlyActiveAccounts": True})
        return out.get("accounts", [])
