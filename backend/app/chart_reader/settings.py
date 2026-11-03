"""M86 W1 / W4: the chart reader's platform settings (SUPER_ADMIN, `GET/PUT /api/admin/chart-reader/settings`).

Every value the reader's service uses lives here, in `DEFAULTS`, and nowhere in the logic. The stored values are a
platform control row (`platform_controls`, key `chart_reader_settings`, the same store as Analyze a chart's settings); a
missing or malformed stored value reads as its default, never as a crash.

- `model` / `effort`: what the reader runs on (the owner's order: settings, not code).
- `api_key_env` / `api_key_fallback_env`: the NAMES of the server environment variables that hold the reader's API key
  (the host may hide ANTHROPIC_* variables, so CHART_READ_API_KEY comes first). The key itself is never stored, shown,
  logged or returned - only whether it is set.
- `timeframes`: the charts ATP renders from the stored candles for one read, in the M59 hybrid's own timeframe ids
  (app/chart_analyze/hybrid.py TIMEFRAMES); the first one is the main chart (the paired chart uses it).
- `pair`: W2 - the symbol whose chart is attached automatically for a strategy with two-chart conditions.
- `cache_hours`: how long a read is reused while the chart's structure is unchanged (the Redis cache caps every entry
  at CACHE_MAX_TTL_SECONDS, so the effective time is the lower of the two).
- prices per million tokens and the token estimate: the estimated cost shown before a read and checked against the
  user's own daily cap (the real cost of a run is computed from the usage the reader returns).
- `max_output_tokens`: passed to the reader only when its `read()` accepts it.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.chart_analyze import hybrid
from app.db.models import User

KEY = "chart_reader_settings"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODEL = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,79}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MAX_TIMEFRAMES = len(hybrid.TIMEFRAMES)
MAX_PAIRS = 20

DEFAULTS: dict[str, Any] = {
    "model": "claude-opus-5-5",
    "effort": "high",
    "api_key_env": "CHART_READ_API_KEY",
    "api_key_fallback_env": "ANTHROPIC_API_KEY",
    "timeframes": ["1h", "15m"],
    "pair": {"NIFTY 50": "NIFTY BANK", "NIFTY BANK": "NIFTY 50"},
    "cache_hours": 24,
    "price_input_per_mtok_usd": 4.0,
    "price_output_per_mtok_usd": 20.0,
    "price_cache_read_per_mtok_usd": 0.20,
    "estimate_input_tokens_base": 3000,
    "estimate_input_tokens_per_chart": 1600,
    "estimate_output_tokens": 2000,
    "max_output_tokens": 16000,
}

# Lower and upper bounds an admin may set for the numbers.
BOUNDS: dict[str, tuple[float, float]] = {
    "cache_hours": (0, 720),
    "price_input_per_mtok_usd": (0.0, 1000.0),
    "price_output_per_mtok_usd": (0.0, 1000.0),
    "price_cache_read_per_mtok_usd": (0.0, 1000.0),
    "estimate_input_tokens_base": (0, 1_000_000),
    "estimate_input_tokens_per_chart": (0, 1_000_000),
    "estimate_output_tokens": (0, 1_000_000),
    "max_output_tokens": (256, 128_000),
}
TEXTS = ("model", "effort", "api_key_env", "api_key_fallback_env")


class SettingsError(ValueError):
    """A refused admin change; the message says which value and what is allowed."""


def _number(key: str, value: Any) -> int | float:
    default = DEFAULTS[key]
    if isinstance(value, bool):
        raise SettingsError(f"{key} must be a number")
    try:
        number = type(default)(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{key} must be a number") from None
    low, high = BOUNDS[key]
    if not low <= number <= high:
        raise SettingsError(f"{key} must be between {low:g} and {high:g}")
    return number


def _text(key: str, value: Any) -> str:
    text = str(value or "").strip() if isinstance(value, str) else ""
    if key == "effort":
        if text not in EFFORTS:
            raise SettingsError(f"effort must be one of {', '.join(EFFORTS)}")
    elif key == "model":
        if not MODEL.fullmatch(text):
            raise SettingsError("model must be a model id (lower-case letters, digits, '.' and '-')")
    elif not ENV_NAME.fullmatch(text):
        raise SettingsError(f"{key} must be an environment variable name (A-Z, 0-9, _), never the key itself")
    return text


def _timeframes(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > MAX_TIMEFRAMES:
        raise SettingsError(f"timeframes must be a list of 1 to {MAX_TIMEFRAMES} of: {', '.join(hybrid.TIMEFRAMES)}")
    out: list[str] = []
    for tf in value:
        if tf not in hybrid.TIMEFRAMES or tf in out:
            raise SettingsError(f"timeframes must be distinct, each one of: {', '.join(hybrid.TIMEFRAMES)}")
        out.append(tf)
    return out


def _symbol(value: Any) -> str:
    sym = " ".join(str(value or "").strip().upper().split()) if isinstance(value, str) else ""
    if not hybrid.SYMBOL.fullmatch(sym):
        raise SettingsError("pair symbols must be symbol names (letters, digits and . & -, at most 40)")
    return sym


def _pair(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > MAX_PAIRS:
        raise SettingsError(f"pair must be an object of at most {MAX_PAIRS} symbol -> symbol entries")
    out: dict[str, str] = {}
    for k, v in value.items():
        a, b = _symbol(k), _symbol(v)
        if a == b:
            raise SettingsError("a symbol cannot be paired with itself")
        out[a] = b
    return out


def _check(key: str, value: Any) -> Any:
    if key in BOUNDS:
        return _number(key, value)
    if key in TEXTS:
        return _text(key, value)
    if key == "timeframes":
        return _timeframes(value)
    if key == "pair":
        return _pair(value)
    raise SettingsError(f"Unknown setting '{key}' - known: {', '.join(sorted(DEFAULTS))}")


def merge(stored: dict[str, Any] | None) -> dict[str, Any]:
    """The effective settings: the defaults, overlaid with every stored value that is well formed and in bounds."""
    out = copy.deepcopy(DEFAULTS)
    for key, value in (stored if isinstance(stored, dict) else {}).items():
        if key not in DEFAULTS:
            continue
        try:
            out[key] = _check(key, value)
        except SettingsError:
            continue
    return out


async def load(session: AsyncSession) -> dict[str, Any]:
    from app.platform.controls import _get
    return merge(await _get(session, KEY))


def validate_update(current: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """The new settings after `values` (a partial update), or SettingsError naming the first bad value."""
    out = copy.deepcopy(current)
    for key, value in (values or {}).items():
        out[key] = _check(key, value)
    return out


async def save(session: AsyncSession, user: User, values: dict[str, Any]) -> dict[str, Any]:
    from app.platform.controls import _set
    new = validate_update(await load(session), values)
    await _set(session, KEY, new, user)
    await write_audit_log(session, None, user.id, "chart_reader_settings_set", ", ".join(sorted(values or {})) or "-")
    await session.commit()
    return new
