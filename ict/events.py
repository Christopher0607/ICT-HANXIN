"""Event schema and the ``confirmed_at`` contract.

Every ICT detector in this package returns an *event frame*: one row per
detected structure, carrying both the timestamp the structure is anchored to
and the timestamp at which it first became **knowable**.

That second column is the whole point of this module.

An ICT backtest is only honest if it never acts on information that did not
exist yet.  The traps are subtle and specific:

* A fair value gap spanning bars ``i-2, i-1, i`` is anchored at ``i-1`` (the
  displacement bar) but cannot be seen until bar ``i`` closes.
* An ``n``-bar fractal swing high at bar ``i`` needs ``n`` bars *after* it to
  confirm; it is not knowable until ``ts[i + n]``.
* An order block is the last opposing candle before displacement, so it is
  anchored in the past but only identified once the displacement completes.

Detectors therefore report ``ts`` (where the structure lives on the chart) and
``confirmed_at`` (when a trader could first have known it).  The backtest
engine filters exclusively on ``confirmed_at``.  ``tests/test_no_lookahead.py``
enforces this for every detector by re-running each one on truncated data and
requiring the results to match.
"""

from __future__ import annotations

import pandas as pd

#: Columns present on every event frame.
BASE_COLUMNS = ["ts", "confirmed_at", "kind", "direction"]

#: Bullish / bearish direction constants.
BULLISH = 1
BEARISH = -1


def make_events(rows: list[dict], extra_columns: list[str] | None = None) -> pd.DataFrame:
    """Build a validated event frame from a list of row dicts.

    Returns an empty, correctly-typed frame when ``rows`` is empty so callers
    can concatenate and filter without special-casing the no-signal path.
    """
    columns = BASE_COLUMNS + list(extra_columns or [])
    if not rows:
        empty = pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
        empty["ts"] = pd.Series(dtype="datetime64[ns, UTC]")
        empty["confirmed_at"] = pd.Series(dtype="datetime64[ns, UTC]")
        empty["direction"] = pd.Series(dtype="int64")
        return empty

    df = pd.DataFrame(rows)
    for col in columns:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[columns + [c for c in df.columns if c not in columns]]
    df = df.sort_values("confirmed_at", kind="mergesort").reset_index(drop=True)
    validate_events(df)
    return df


def validate_events(df: pd.DataFrame) -> None:
    """Raise if an event frame violates the schema or the causality contract."""
    missing = [c for c in BASE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"event frame missing required columns: {missing}")
    if df.empty:
        return
    if df["confirmed_at"].isna().any():
        raise ValueError("event frame has null confirmed_at")
    late = df["confirmed_at"] < df["ts"]
    if late.any():
        raise ValueError(
            f"{int(late.sum())} events confirm before they occur — "
            "confirmed_at must be >= ts"
        )


def visible_at(df: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Subset of ``df`` a trader could legitimately act on at ``now``.

    This is the only sanctioned way for strategy code to read an event frame.
    """
    if df.empty:
        return df
    return df[df["confirmed_at"] <= now]


def latest_visible(df: pd.DataFrame, now: pd.Timestamp) -> pd.Series | None:
    """Most recently confirmed event at ``now``, or ``None`` if there is none."""
    vis = visible_at(df, now)
    if vis.empty:
        return None
    return vis.iloc[-1]
