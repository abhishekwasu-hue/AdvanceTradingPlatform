"""Runner S2: the local audit log - every order intent and every result, one JSON line each, hash-chained.

Each row: seq, ts (UTC), kind, data, prev (the previous row's hash) and hash = sha256 of the row without its hash. A
changed, removed or reordered row breaks the chain (`verify`). ATP receives copies of these rows in step S4 (the design's
section 4: "local audit rows, hash-chained"). No secrets are ever written here (tokens, keys, PINs).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _digest(row: dict[str, Any]) -> str:
    body = {k: row[k] for k in ("seq", "ts", "kind", "data", "prev")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._seq = 0
        self._prev = GENESIS
        if path.exists():
            last: str | None = None
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        last = line
            if last is not None:
                row = json.loads(last)
                self._seq, self._prev = int(row["seq"]), str(row["hash"])

    def write(self, kind: str, **data: Any) -> dict[str, Any]:
        row: dict[str, Any] = {"seq": self._seq + 1, "ts": datetime.now(UTC).isoformat(), "kind": kind,
                               "data": json.loads(json.dumps(data, default=str)), "prev": self._prev}
        row["hash"] = _digest(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._seq, self._prev = row["seq"], row["hash"]
        return row


def read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def verify(path: Path) -> tuple[bool, int, str | None]:
    """(chain intact, rows checked, the first problem in plain words or None)."""
    prev, rows = GENESIS, read(path)
    for i, row in enumerate(rows, start=1):
        if row.get("seq") != i:
            return False, i, f"row {i}: sequence number {row.get('seq')} (a row is missing or out of order)"
        if row.get("prev") != prev:
            return False, i, f"row {i}: does not follow the row before it"
        if row.get("hash") != _digest(row):
            return False, i, f"row {i}: changed after it was written"
        prev = row["hash"]
    return True, len(rows), None


__all__ = ["AuditLog", "read", "verify", "GENESIS"]
