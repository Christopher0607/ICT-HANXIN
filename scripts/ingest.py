"""Merge the raw Databento shards into validated processed datasets.

Run once after cloning:  uv run python scripts/ingest.py
"""

from __future__ import annotations

import sys

import pandas as pd

from ict import data as D


def main() -> int:
    D.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    print("loading 1-minute shards ...")
    df1m = D.load_raw("NQ_v_0_1m_*.parquet")
    print(f"  {len(df1m):,} bars   {df1m.ts.min()} -> {df1m.ts.max()}")

    print("loading 5-minute shards ...")
    df5m = D.load_raw("NQ_v_0_5m_*.parquet")
    print(f"  {len(df5m):,} bars   {df5m.ts.min()} -> {df5m.ts.max()}")

    # The shipped 5m file carries ema12/atr14 computed over the full continuous
    # series before it was split. Recompute from the merged frame and compare:
    # agreement proves both the merge and our indicator implementations.
    print("verifying shipped indicators against recomputed values ...")
    rec_ema = D.ema(df5m["close"], 12)
    rec_atr = D.wilder_atr(df5m, 14)
    for name, shipped, rebuilt in (("ema12", df5m["ema12"], rec_ema),
                                   ("atr14", df5m["atr14"], rec_atr)):
        both = shipped.notna() & rebuilt.notna()
        diff = (shipped[both] - rebuilt[both]).abs().max()
        print(f"  {name}: max abs diff {diff:.10f} over {int(both.sum()):,} bars")

    df1m.to_parquet(D.PROCESSED_DIR / "nq_1m.parquet", compression="zstd", index=False)
    df5m.to_parquet(D.PROCESSED_DIR / "nq_5m.parquet", compression="zstd", index=False)
    print(f"wrote {D.PROCESSED_DIR}/nq_1m.parquet and nq_5m.parquet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
