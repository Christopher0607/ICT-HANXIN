"""Merge the raw Databento shards into validated processed datasets.

Run after cloning, and again after every fetch:  uv run python scripts/ingest.py

The 5-minute file is *extended*, not rebuilt. Its shipped range is the series
the seven session strategies were tested on, so it is left byte-identical and
only the bars past its end are appended, resampled from the 1-minute file.
ema12/atr14 are recursive and have to be recomputed over the joined series;
the recomputed values are checked against the shipped ones over the shipped
range before anything is written, so an extension can never quietly rewrite
sixteen years of indicator history.
"""

from __future__ import annotations

import sys

import pandas as pd

from ict import data as D

OHLCV = ["ts", "open", "high", "low", "close", "volume"]
STEP_5M = pd.Timedelta("5min")


def extend_5m(df1m: pd.DataFrame, df5m: pd.DataFrame) -> pd.DataFrame:
    """Append 5-minute bars built from 1-minute data past the shipped end."""
    last = df5m["ts"].max()
    # Start one full bucket past the shipped end: the bars inside the shipped
    # last bucket already contributed to it, and re-cutting it from 1m data
    # would replace a shipped bar with a recomputed one.
    tail1m = df1m[df1m["ts"] >= last + STEP_5M]
    if tail1m.empty:
        return df5m[OHLCV].copy()

    tail5m = D.resample(tail1m[OHLCV], "5min")
    # Drop a final bucket the 1-minute data does not fully cover.
    complete = tail5m["ts"] + STEP_5M <= tail1m["ts"].max() + pd.Timedelta("1min")
    dropped = int((~complete).sum())
    if dropped:
        print(f"  dropping {dropped} incomplete trailing 5m bar(s)")
    tail5m = tail5m[complete]

    print(f"  extending 5m by {len(tail5m):,} bars "
          f"{tail5m.ts.min()} -> {tail5m.ts.max()}" if len(tail5m)
          else "  no complete new 5m bars to append")
    return pd.concat([df5m[OHLCV], tail5m], ignore_index=True)


def main() -> int:
    D.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    print("loading 1-minute shards ...")
    df1m = D.load_raw("NQ_v_0_1m_*.parquet")
    print(f"  {len(df1m):,} bars   {df1m.ts.min()} -> {df1m.ts.max()}")

    print("loading 5-minute shards ...")
    shipped5m = D.load_raw("NQ_v_0_5m_*.parquet")
    print(f"  {len(shipped5m):,} bars   {shipped5m.ts.min()} -> {shipped5m.ts.max()}")

    print("extending the 5-minute series from the 1-minute file ...")
    df5m = extend_5m(df1m, shipped5m)
    D.assert_clean(df5m)

    # Recompute over the joined series, then prove the shipped range is
    # untouched. Agreement here also proves the merge and our ema/atr code.
    print("verifying recomputed indicators against the shipped values ...")
    df5m["ema12"] = D.ema(df5m["close"], 12)
    df5m["atr14"] = D.wilder_atr(df5m, 14)
    within = df5m["ts"] <= shipped5m["ts"].max()
    for name in ("ema12", "atr14"):
        old = shipped5m[name].to_numpy()
        new = df5m.loc[within, name].to_numpy()
        assert len(old) == len(new), f"{name}: {len(old)} shipped vs {len(new)} rebuilt"
        both = pd.notna(old) & pd.notna(new)
        diff = float(abs(old[both] - new[both]).max())
        print(f"  {name}: max abs diff {diff:.10f} over {int(both.sum()):,} bars")
        if diff > 0:
            raise SystemExit(f"{name} no longer reproduces the shipped column; refusing to write")

    # A 1-minute file that runs past the 5-minute one makes every session
    # strategy silently stop early. Catch it here rather than in a backtest.
    lag = df1m["ts"].max() - df5m["ts"].max()
    print(f"  1m ends {df1m.ts.max()}, 5m ends {df5m.ts.max()} (lag {lag})")
    if lag >= 2 * STEP_5M:
        raise SystemExit(f"5-minute file lags the 1-minute file by {lag}; refusing to write")

    df1m.to_parquet(D.PROCESSED_DIR / "nq_1m.parquet", compression="zstd", index=False)
    df5m.to_parquet(D.PROCESSED_DIR / "nq_5m.parquet", compression="zstd", index=False)
    print(f"wrote {D.PROCESSED_DIR}/nq_1m.parquet and nq_5m.parquet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
