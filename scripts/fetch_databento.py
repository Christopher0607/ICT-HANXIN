"""Fetch NQ 1-minute bars from Databento and validate them against what we have.

The request parameters are the ones recorded in docs/DATABENTO_README.md, and
they are not interchangeable:

    dataset   GLBX.MDP3
    symbols   NQ.v.0      <- volume-ranked continuous, NOT NQ.c.0
    stype_in  continuous
    schema    ohlcv-1m

``.v.0`` follows the contract that is actually trading. ``.c.0`` stays on the
front month by calendar and sits on a dying contract through roll week; on
2024-09-20 it showed 380 lots in the 09:30 hour where ``.v.0`` showed 68,278.
Backtest fills on 380 lots are fiction.

**Overlap check.** By default the script re-fetches days it already has and
compares them bar for bar against ``data/processed/nq_1m.parquet``. If the
request parameters no longer reproduce the shipped series -- a changed
timestamp convention, a different continuous contract, a revised bar -- every
conclusion drawn from the new days is unsafe, and finding that out afterwards
is worse than paying for two extra days of data. A mismatch aborts the write.

**The API key is read from the environment only.** It is deliberately not a
command-line argument: arguments land in shell history and in the process list
where any other process on the box can read them.

    DATABENTO_API_KEY=... uv run python scripts/fetch_databento.py \
        --start 2026-08-26 --end 2026-09-09
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

from ict import data as D

DATASET = "GLBX.MDP3"
SYMBOL = "NQ.v.0"
STYPE_IN = "continuous"
SCHEMA = "ohlcv-1m"

COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def normalise(raw: pd.DataFrame) -> pd.DataFrame:
    """Reshape a Databento OHLCV frame into this repo's column layout."""
    df = raw.reset_index()
    stamp = "ts_event" if "ts_event" in df.columns else df.columns[0]
    df = df.rename(columns={stamp: "ts"})
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Databento response is missing {missing}; got {list(df.columns)}")
    df = df[COLUMNS].copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype("float64")
    df["volume"] = df["volume"].astype("uint64")
    return df.sort_values("ts", kind="mergesort").reset_index(drop=True)


def verify_overlap(fresh: pd.DataFrame) -> int:
    """Compare the re-fetched days against the stored ones, bar for bar.

    Returns the number of bars compared. Raises if any of them disagree, or if
    the two sources cover the same span with a different number of bars.
    """
    path = D.PROCESSED_DIR / "nq_1m.parquet"
    if not path.exists():
        print(f"  no {path} to compare against; skipping overlap check")
        return 0

    have = pd.read_parquet(path)
    lo, hi = fresh["ts"].min(), min(fresh["ts"].max(), have["ts"].max())
    if lo > hi:
        print("  fetched window starts after the stored data ends; nothing to compare")
        return 0

    a = have[(have["ts"] >= lo) & (have["ts"] <= hi)].reset_index(drop=True)
    b = fresh[(fresh["ts"] >= lo) & (fresh["ts"] <= hi)].reset_index(drop=True)
    if len(a) != len(b):
        raise ValueError(
            f"overlap {lo} -> {hi} has {len(a)} stored bars but {len(b)} fetched bars. "
            "The request no longer reproduces the shipped series."
        )
    if len(a) == 0:
        return 0

    bad = a["ts"].ne(b["ts"])
    if bad.any():
        i = int(bad.idxmax())
        raise ValueError(
            f"timestamps diverge at row {i}: stored {a['ts'].iloc[i]} vs fetched {b['ts'].iloc[i]}. "
            "Databento may have changed whether ohlcv bars are stamped at open or close."
        )
    for col in ("open", "high", "low", "close", "volume"):
        diff = a[col].astype("float64").sub(b[col].astype("float64")).abs()
        worst = float(diff.max())
        if worst > 0:
            i = int(diff.idxmax())
            raise ValueError(
                f"{col} differs by up to {worst} (row {i}, {a['ts'].iloc[i]}): "
                f"stored {a[col].iloc[i]} vs fetched {b[col].iloc[i]}"
            )
    return len(a)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="inclusive UTC date, YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="exclusive UTC date, YYYY-MM-DD")
    ap.add_argument("--out", default=None,
                    help="parquet path (default data/raw/NQ_v_0_1m_<start>_<end>.parquet)")
    ap.add_argument("--max-cost-usd", type=float, default=1.00,
                    help="abort before downloading if Databento quotes more than this")
    ap.add_argument("--no-verify-overlap", action="store_true",
                    help="skip the bar-for-bar check against the stored data")
    ap.add_argument("--dry-run", action="store_true", help="quote the cost and stop")
    args = ap.parse_args(argv)

    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print(
            "DATABENTO_API_KEY is not set.\n\n"
            "  DATABENTO_API_KEY=<key> uv run python scripts/fetch_databento.py ...\n\n"
            "Pass it through the environment, never as an argument: arguments are\n"
            "visible in shell history and in the process list.",
            file=sys.stderr,
        )
        return 2

    import databento as db  # imported here so the repo runs without it installed

    client = db.Historical(key)
    req = dict(dataset=DATASET, symbols=[SYMBOL], stype_in=STYPE_IN,
               schema=SCHEMA, start=args.start, end=args.end)

    print(f"{SYMBOL}  {SCHEMA}  {args.start} -> {args.end} (end exclusive)")
    cost = client.metadata.get_cost(**req)
    print(f"  quoted cost ${cost:.4f}  (cap ${args.max_cost_usd:.2f})")
    if cost > args.max_cost_usd:
        print(f"  ABORT: quote exceeds --max-cost-usd; nothing downloaded", file=sys.stderr)
        return 1
    if args.dry_run:
        print("  --dry-run: stopping before download")
        return 0

    print("  downloading ...")
    df = normalise(client.timeseries.get_range(**req).to_df())
    print(f"  {len(df):,} bars   {df.ts.min()} -> {df.ts.max()}")
    D.assert_clean(df)

    if not args.no_verify_overlap:
        print("verifying the overlap against data/processed/nq_1m.parquet ...")
        n = verify_overlap(df)
        print(f"  {n:,} overlapping bars match exactly"
              if n else "  no overlapping bars to check")

    out = args.out or (D.RAW_DIR / f"NQ_v_0_1m_{args.start}_{args.end}.parquet")
    D.RAW_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, compression="zstd", index=False)
    print(f"wrote {out}")
    print("\nRotate this API key now: it was pasted into a chat transcript.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
