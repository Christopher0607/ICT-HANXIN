"""Append-only record of every decision the bridge makes.

Logs are for reading; this is for reconciling. One JSON object per line, one
line per decision -- including the ones where nothing was sent, because "the
bridge declined 14 signals last month" is exactly the kind of divergence that
is invisible in a broker statement and obvious here.

Never holds a secret: the webhook secret is stripped before the payload
reaches this module, and broker credentials never enter it at all.
"""

from __future__ import annotations

import json
import pathlib
import threading
from datetime import datetime, timezone
from typing import Any

DEFAULT_PATH = pathlib.Path("data/bridge_journal.jsonl")


class Journal:
    """Writes one line per event. Safe to share across request threads."""

    def __init__(self, path: pathlib.Path | str | None = DEFAULT_PATH) -> None:
        self.path = pathlib.Path(path) if path else None
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields: Any) -> dict:
        """Record one decision and return the row, so callers can log it too."""
        row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "event": event, **fields}
        if self.path:
            line = json.dumps(row, default=str, sort_keys=True)
            with self._lock, self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return row

    def read(self) -> list[dict]:
        """Every row, oldest first. Malformed lines are skipped, not fatal.

        A half-written final line is the normal consequence of killing the
        process mid-write; losing the reconciliation over it would be worse
        than losing that one row.
        """
        if not self.path or not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
