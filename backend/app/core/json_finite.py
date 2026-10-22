"""JSON every reader accepts (UI2-3 review): Python's json.dumps writes a NaN or infinite float as the bare word NaN /
Infinity, which is not JSON - Postgres refuses it (`invalid input syntax for type json`) and a strict parser does too.
A signal's floats are not checked for being finite (a TradingView alert or a public-API call can carry NaN), so the
writers of stored evidence turn them into null first and then refuse anything non-finite that is left."""
from __future__ import annotations

import json
import math
from typing import Any


def finite(value: Any) -> Any:
    """`value` with every NaN / infinite float (also inside dicts, lists and tuples) replaced by None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite(v) for v in value]
    return value


def dumps(value: Any) -> str:
    """json.dumps for stored evidence: non-finite floats become null, other objects their str(), never NaN."""
    return json.dumps(finite(value), default=str, allow_nan=False)
