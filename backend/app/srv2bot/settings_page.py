"""SRV2 P2 + P4 (owner order SRV2; "Pending job purn kr" 21 Nov): the owner's settings page for the 15M SRv2 bot, and the bot's
positions shown read-only.

- Settings: Trade's own fields for the bot, per symbol, plus the signal symbols.
  - The field list, the labels, the bounds and the defaults come from the private package (its `settings` module: FIELDS, DEFAULTS,
    validate). ATP only renders them and stores what he saves (store.py, Trade's `strategy_settings` rows).
  - Nothing is filled in for him: a key he did not send keeps its stored value, or Trade's default when none is stored.
  - Fields Trade shows in strikes are shown in strikes and stored in points (strikes x the symbol's strike step), as Trade's page does.
  - Every save is audited: who, which symbol, which keys.
- Positions: the bot's own PAPER store, read-only (`positions.read`). Exits stay Trade's own code (SRV2-Q1 A); nothing here changes a
  position or an order (ADR-0004 / ADR-0006)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.srv2bot import loader, store

EVENT = "srv2.settings"
SIGNAL_SYMBOLS_ROW = ("__signal_symbols__", "ALL")


class SettingsUnavailable(RuntimeError):
    """The private package (or its settings module) is not installed on this server."""


def _settings() -> Any:
    bot = loader.load()
    mod = getattr(bot, "settings", None) if bot is not None else None
    if mod is None:
        raise SettingsUnavailable("The trading agent's settings are not installed on this server.")
    return mod


def _step(mod: Any, symbol: str) -> int:
    steps = getattr(mod, "STRIKE_STEP", {}) or {}
    return int(steps.get(symbol) or steps.get("NIFTY") or 1)


def symbols(mod: Any) -> list[str]:
    return list(getattr(mod, "SIGNAL_SYMBOLS_CHOICES", ()) or ())


def _display(field: dict[str, Any], value: Any, step: int) -> Any:
    if field.get("strikes") and isinstance(value, (int, float)) and step:
        return round(value / step, 4)
    return value


def _stored_units(field: dict[str, Any], value: Any, step: int) -> Any:
    if field.get("strikes") and isinstance(value, (int, float)) and not isinstance(value, bool):
        return value * step
    return value


async def read(session: AsyncSession, symbol: str) -> dict[str, Any]:
    """The page for one symbol: the fields with his values (stored, else Trade's default) and the signal symbols."""
    try:
        mod = _settings()
    except SettingsUnavailable:
        return {"installed": False, "symbol": symbol, "symbols": [], "signal_symbols": [], "fields": []}
    choices = symbols(mod)
    if symbol not in choices:
        raise ValueError(f"The symbol must be one of: {', '.join(choices)}.")
    stored = await store.get_row(session, mod.STRATEGY_KEY, symbol) or {}
    values = mod.merged(symbol, stored)
    step = _step(mod, symbol)
    fields = []
    for f in getattr(mod, "FIELDS", None) or []:
        key = f.get("key")
        if key not in values:
            continue
        fields.append({**{k: f.get(k) for k in ("key", "label", "group", "type", "choices", "unit", "help", "strikes")},
                       "value": _display(f, values[key], step), "saved": key in stored})
    sig_row = await store.get_row(session, *SIGNAL_SYMBOLS_ROW) or {}
    try:
        signal = mod.clean_signal_symbols(sig_row["signal_symbols"]) if sig_row.get("signal_symbols") else \
            list(getattr(mod, "SIGNAL_SYMBOLS_DEFAULT", ()) or ())
    except ValueError:
        signal = list(getattr(mod, "SIGNAL_SYMBOLS_DEFAULT", ()) or ())
    return {"installed": True, "symbol": symbol, "symbols": choices, "signal_symbols": signal, "fields": fields,
            "has_fields": bool(getattr(mod, "FIELDS", None))}


async def save(session: AsyncSession, user_id: int | None, symbol: str, values: dict[str, Any]) -> dict[str, str]:
    """Validate his values with Trade's own rules (the private `validate`) and store only the keys he sent. Returns the errors
    (key -> sentence); nothing is stored when there is any error. The caller commits."""
    mod = _settings()
    if symbol not in symbols(mod):
        raise ValueError(f"The symbol must be one of: {', '.join(symbols(mod))}.")
    by_key = {f.get("key"): f for f in getattr(mod, "FIELDS", None) or []}
    unknown = {k: "This setting cannot be changed here." for k in values if k not in by_key}
    if unknown:
        return unknown
    step = _step(mod, symbol)
    raw = {k: _stored_units(by_key[k], v, step) for k, v in values.items()}
    validate = getattr(mod, "validate", None)
    if validate is None:
        raise SettingsUnavailable("The trading agent's settings cannot be checked on this server yet.")
    clean, errors = await asyncio.to_thread(validate, symbol, raw)
    if errors:
        return {str(k): str(v) for k, v in errors.items()}
    await store.save_row(session, mod.STRATEGY_KEY, symbol, dict(clean), by=user_id)
    await write_audit_log(session, None, user_id, EVENT, json.dumps({"symbol": symbol, "keys": sorted(clean)}))
    return {}


async def save_signal_symbols(session: AsyncSession, user_id: int | None, value: list[str]) -> list[str]:
    """The symbols the bot reads (Trade's `__signal_symbols__` row); Trade's own cleaner refuses unknown or empty lists."""
    mod = _settings()
    clean = mod.clean_signal_symbols(value)                 # ValueError with Trade's sentence
    await store.save_row(session, *SIGNAL_SYMBOLS_ROW, {"signal_symbols": clean}, by=user_id)
    await write_audit_log(session, None, user_id, EVENT, json.dumps({"signal_symbols": clean}))
    return list(clean)


POSITION_FIELDS = ("id", "symbol", "direction", "status", "opened_at", "closed_at", "legs", "pnl", "exit_reason", "mode")
LEG_FIELDS = ("strike", "option_type", "side", "qty", "entry_price", "exit_price")


async def positions(limit: int = 50) -> dict[str, Any]:
    """The bot's positions, newest first, read-only (an older package without `positions`, or an unreadable store: none)."""
    bot = loader.load()
    mod = getattr(bot, "positions", None) if bot is not None else None
    if mod is None:
        return {"installed": False, "positions": []}
    try:
        rows = await asyncio.to_thread(mod.read, limit)
    except Exception:  # noqa: BLE001 - a broken store never breaks the page
        rows = []
    from app.srv2bot.mode import _with_ist
    out = []
    for r in rows or []:
        p = {k: (r or {}).get(k) for k in POSITION_FIELDS}
        p["legs"] = [{k: (leg or {}).get(k) for k in LEG_FIELDS} for leg in (p.get("legs") or [])]
        out.append(_with_ist(p))
    return {"installed": True, "positions": out[:limit]}
