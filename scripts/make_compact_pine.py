"""Generate paste-friendly copies of the Pine scripts.

The full scripts carry ~4 KB of comments, and a mobile clipboard truncated a
17 KB paste one line from the end. Smaller is safer, so this strips the header
block and the explanatory comments while keeping the code byte-for-byte.

Four comments survive: the ones that stop a future editor from silently
reintroducing lookahead bias or same-bar confirmation. Those are the two ways
this strategy can be broken without any visible symptom, so they are worth the
bytes.

    uv run python scripts/make_compact_pine.py
"""

from __future__ import annotations

import pathlib
import re
import sys

PINE = pathlib.Path(__file__).resolve().parent.parent / "tradingview"

#: A standalone comment is kept if it mentions any of these. They document the
#: two invariants that fail silently rather than loudly.
KEEP = ("lookahead", "afterSweep", "confirmed_at", "interdependent",
        "round", "ASCII", "BAR CLOSE")

HEADER = """//@version=6
// LTF Sweep - 15m pool / 1m sweep / 1m CHoCH / FVG entry / fixed 1:1
// Compact build of {src} - edit that file, not this one.
// Net negative over 10.7 years; profitable only since 2023. Evaluate on BAR
// CLOSE and set alerts to "Once Per Bar Close". Keep this file pure ASCII.
"""


def compact(src: pathlib.Path) -> str:
    lines = src.read_text(encoding="ascii").split("\n")
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("//"):
            if any(k in line for k in KEEP):
                out.append(line)
            continue
        if not stripped and out and not out[-1].strip():
            continue  # collapse runs of blank lines
        out.append(line)
    body = "\n".join(out).lstrip("\n")
    return HEADER.format(src=src.name) + body


def main() -> int:
    for name in ("ltf_sweep_strategy.pine", "ltf_sweep_indicator.pine"):
        src = PINE / name
        dst = PINE / name.replace(".pine", "_compact.pine")
        text = compact(src)
        dst.write_text(text, encoding="ascii")
        before, after = len(src.read_text()), len(text)
        print(f"{dst.name}: {before:,} -> {after:,} bytes "
              f"({100 * (1 - after / before):.0f}% smaller, "
              f"{len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
