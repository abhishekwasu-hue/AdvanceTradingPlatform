"""Backtest realism C4: every run says exactly what produced it, and the same inputs give the same bytes.

A run's fingerprint (on the result as `reproducibility`, stored with the run record):
- `engine_version`  - the engine's own version (bumped whenever results can change for the same inputs);
- `code_version`    - the deployed build (APP_VERSION);
- `data_version`    - SHA-256 over the bars themselves (UTC timestamps + OHLCV as float64), not a file name or label;
- `config_hash`     - SHA-256 over the canonical JSON of everything else that shapes the run: strategy id and its
                      effective parameters, symbol, base timeframe, risk config, exit rules, option settings;
- `result_hash`     - SHA-256 over the canonical JSON of the result (trades, equity curve, metrics, analytics);
- `seed`            - the RNG seed where a run draws random numbers (Monte Carlo); None for the deterministic engine.
Two runs with equal engine/code/data/config must have equal result hashes - tests/test_realism_repro.py checks it,
across processes with different hash seeds too.
"""
import dataclasses
import enum
import hashlib
import json
import math
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from app.core.config import APP_VERSION

OHLCV = ["open", "high", "low", "close", "volume"]


def _norm(value: Any) -> Any:
    """A JSON-ready form whose serialisation does not depend on dict order, set order or float formatting."""
    if hasattr(value, "model_dump"):
        return _norm(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _norm(dataclasses.asdict(value))
    if isinstance(value, enum.Enum):
        return _norm(value.value)
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_norm(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return repr(f) if math.isnan(f) or math.isinf(f) else float(repr(f))
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo else value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_norm(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def data_version(df: pd.DataFrame) -> str:
    index = pd.DatetimeIndex(df.index)
    index = index.tz_convert("UTC") if index.tz is not None else index
    stamps = np.ascontiguousarray(index.asi8, dtype="<i8").tobytes()
    values = np.ascontiguousarray(df[OHLCV].to_numpy(dtype="<f8")).tobytes()
    return _sha256(len(df).to_bytes(8, "little") + stamps + values)


def config_hash(**parts: Any) -> str:
    return _sha256(canonical_json(parts).encode())


def result_hash(result: Any) -> str:
    payload = result.model_dump(mode="json", exclude={"run_id", "reproducibility"})
    return _sha256(canonical_json(payload).encode())


def fingerprint(result: Any, df: pd.DataFrame, *, engine_version: str, seed: Optional[int] = None, **config: Any) -> Dict[str, Any]:
    return {
        "engine_version": engine_version, "code_version": APP_VERSION, "data_version": data_version(df),
        "config_hash": config_hash(**config), "result_hash": result_hash(result), "seed": seed, "bars": len(df),
    }
