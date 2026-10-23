"""TM-9 / Part 7 smart execution: the exchange's quantity freeze limit per F&O underlying.

The instrument master carries no freeze quantity (FNO-1-2). The limit comes from, in this order:

1. the exchange's own published file, when the operator points `FREEZE_LIMITS_FILE` at a copy of it (NSE publishes the
   quantity freeze limits for every F&O underlying as a sheet with a symbol column and a freeze-quantity column; save it
   as CSV). Read at startup, no database, no migration, nothing secret;
2. the maintained table `data/freeze_limits.json` (index derivatives, with its source circular and the date it applies
   from).

A symbol in neither has NO known limit: it is never sliced, and the ticket's own cap applies (never a guessed limit).

How a limit is used (`max_slice_units`): the exchange freezes an order whose quantity reaches the limit, so a slice is the
largest whole number of lots strictly below it. Conservative on purpose (an exact multiple of the lot costs one lot per
slice, never a frozen order); open question SE-4 asks to confirm the wording against the circular.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)
TABLE_PATH = Path(__file__).parent / "data" / "freeze_limits.json"
ENV_FILE = "FREEZE_LIMITS_FILE"


def parse_exchange_file(text: str) -> Dict[str, int]:
    """The exchange's freeze-quantity sheet (as CSV) -> {underlying: limit}. Finds the header row by its columns (one
    naming the symbol, one the freeze quantity), so a title row or a serial-number column does not matter. Rows without
    a whole positive number are skipped."""
    rows = list(csv.reader(io.StringIO(text)))
    for at, header in enumerate(rows):
        names = [h.strip().upper() for h in header]
        sym = next((i for i, h in enumerate(names) if "SYMBOL" in h), None)
        frz = next((i for i, h in enumerate(names) if "FRZ" in h or "FREEZE" in h), None)
        if sym is None or frz is None:
            continue
        out: Dict[str, int] = {}
        for row in rows[at + 1:]:
            if len(row) <= max(sym, frz):
                continue
            symbol = row[sym].strip().upper()
            try:
                value = int(float(row[frz].strip().replace(",", "")))
            except ValueError:
                continue
            if symbol and value > 0:
                out[symbol] = value
        return out
    return {}


@lru_cache(maxsize=1)
def table() -> Dict[str, object]:
    """{limits, source, as_of}: the maintained table with the exchange's file (when configured) over it."""
    data = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    limits: Dict[str, int] = {k.upper(): int(v) for k, v in data["limits"].items()}
    source, as_of = str(data["source"]), str(data["as_of"])
    path = os.environ.get(ENV_FILE, "").strip()
    if path:
        try:
            loaded = parse_exchange_file(Path(path).read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            logger.warning("Freeze limits file %s not read (%s); the maintained table is used", path, type(exc).__name__)
            loaded = {}
        if loaded:
            limits.update(loaded)
            source = f"The exchange's quantity freeze file ({Path(path).name}), over: {source}"
    return {"limits": limits, "source": source, "as_of": as_of}


def freeze_limit(underlying: str) -> Optional[int]:
    """The exchange's freeze quantity for this F&O underlying, or None when it is not known."""
    from app.instruments.master import underlying_of
    limits = table()["limits"]
    value = limits.get(underlying_of(underlying)) if isinstance(limits, dict) else None
    return int(value) if value else None


def max_slice_units(freeze_qty: Optional[int], lot_size: int) -> Optional[int]:
    """The largest order (units) one slice may carry: whole lots strictly below the freeze quantity. None = no known
    limit (no slicing). At least one lot."""
    if not freeze_qty or lot_size <= 0:
        return None
    return max(lot_size, ((int(freeze_qty) - 1) // int(lot_size)) * int(lot_size))
