"""SRV2 P3 (owner AGENT-MODES 20 Nov, ADR-0029): the owner's agent mode for the 15M SRv2 bot.

- `human_confirm` (the default): the setup goes to Vision, a Telegram card is sent, and only his ✅ places the entry.
  No ✅ before the card's deadline means no entry.
- `auto`: the bot places the entry itself when its rules (and Vision, when on) agree.

The mode is stored by the private package (its `modes` module, on Trade's own Vision settings) - one place, no ATP column and no
migration. ATP's part:
- Only the owner may switch, and `auto` is passed as the owner's own deployment (the second lock is in the private package).
- Every switch is written to ATP's hash-chained audit log.
- Exits never pass through here (ADR-0004); LIVE stays his own switch."""
from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.db.models import AuditLogRecord
from app.srv2bot import loader

EVENT = "srv2.agent_mode"
MODES = ("human_confirm", "auto")
DEFAULT = "human_confirm"


class ModeUnavailable(RuntimeError):
    """The private package (or its modes module) is not installed on this server."""


def _modes() -> Any:
    bot = loader.load()
    modes = getattr(bot, "modes", None) if bot is not None else None
    if modes is None:
        raise ModeUnavailable("The trading agent is not installed on this server.")
    return modes


async def last_switch(session: AsyncSession) -> dict[str, Any] | None:
    rec = await session.scalar(select(AuditLogRecord).where(AuditLogRecord.event == EVENT).order_by(AuditLogRecord.id.desc()).limit(1))
    if rec is None:
        return None
    try:
        detail = json.loads(rec.detail or "{}")
    except ValueError:
        detail = {}
    return {"by": rec.user_id, "at": rec.created_at.isoformat() if rec.created_at else None,
            "from": detail.get("from"), "to": detail.get("to"), "vision_on": detail.get("vision_on")}


async def read(session: AsyncSession) -> dict[str, Any]:
    """{installed, mode, vision_on, last, cards}. Not installed: the default mode is shown and nothing can be switched."""
    try:
        modes = _modes()
    except ModeUnavailable:
        return {"installed": False, "mode": DEFAULT, "vision_on": True, "last": await last_switch(session), "cards": []}
    mode, vision_on = await asyncio.to_thread(modes.current)
    return {"installed": True, "mode": mode, "vision_on": bool(vision_on), "last": await last_switch(session),
            "cards": await cards(modes)}


CARD_FIELDS = ("signal_id", "symbol", "direction", "level", "setup_tf", "signal_ts", "created_at", "status", "verdict", "deadline",
               "decided_at", "decided_by", "decision_reason", "executed_at", "exec_note")


async def cards(modes: Any, limit: int = 20) -> list[dict[str, Any]]:
    """The bot's recent Telegram cards, newest first, read-only (an older private package without `cards`: none)."""
    read_cards = getattr(modes, "cards", None)
    if read_cards is None:
        return []
    try:
        rows = await asyncio.to_thread(read_cards, limit)
    except Exception:  # noqa: BLE001 - a broken store never breaks the admin page
        return []
    return [_with_ist({k: (r or {}).get(k) for k in CARD_FIELDS}) for r in rows or []][:limit]


TIME_FIELDS = ("signal_ts", "created_at", "deadline", "decided_at", "executed_at", "opened_at", "closed_at")


def _with_ist(card: dict[str, Any]) -> dict[str, Any]:
    """Trade's store writes IST wall-clock times without an offset; send them with +05:30 so the page shows the same clock."""
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    for k in TIME_FIELDS:
        v = card.get(k)
        if not isinstance(v, str) or not v:
            continue
        try:
            t = datetime.fromisoformat(v)
        except ValueError:
            continue
        card[k] = (t if t.tzinfo else t.replace(tzinfo=ist)).isoformat()
    return card


async def _audit(session: AsyncSession, user_id: int | None, record: dict[str, Any]) -> None:
    keep = {k: record.get(k) for k in ("from", "to", "vision_on", "at")}
    await write_audit_log(session, None, user_id, EVENT, json.dumps(keep, ensure_ascii=False, default=str))


async def trading_mode_fn(session: AsyncSession) -> Callable[[str, str], str]:
    """Owner 25 Nov (VPS log: "human_confirm ... PAPER bot - srv2_momentum_reversal: {'NIFTY': 'UNKNOWN'}"): Trade's own guard in
    `vision/config.save` reads the bot's trading mode per symbol and refuses human_confirm / auto_veto unless it is PAPER. Its own read
    imports the bot's `cloud_db` stand-in, which exists only after the worker's first step (and never in the web process), so it read
    UNKNOWN. ATP hands it the same rule on ATP's own store (`io.effective_trading_mode`); a store that cannot be read = UNKNOWN
    (refused, as before)."""
    from app.srv2bot import io as bot_io
    from app.srv2bot import store
    bot = loader.load()
    key = getattr(getattr(bot, "settings", None), "STRATEGY_KEY", None) if bot is not None else None
    try:
        rows: dict[str, Any] | None = await store.rows(session)
    except Exception:  # noqa: BLE001 - unreadable = UNKNOWN below
        rows = None

    def fn(_bot: str, symbol: str) -> str:
        if rows is None or not key:
            return "UNKNOWN"
        return bot_io.effective_trading_mode(rows.get(store._key(key, symbol)))   # noqa: SLF001 - the store's own key rule
    return fn


def _with_modes_fn(call: Callable[..., Any], fn: Callable[[str, str], str]) -> dict[str, Any]:
    """`trading_mode_fn=` only for a private package that takes it (an older one is called as before)."""
    return {"trading_mode_fn": fn} if "trading_mode_fn" in inspect.signature(call).parameters else {}


async def switch(session: AsyncSession, user: Any, mode: str, *, vision_on: bool, owner: bool) -> dict[str, Any]:
    """The owner's switch. `owner` is the route's check (the configured owner account); every other caller is refused before this.
    Writes the private settings, then the audit row; the caller commits."""
    if mode not in MODES:
        raise ValueError(f"The mode must be one of: {', '.join(MODES)}.")
    modes = _modes()
    fn = await trading_mode_fn(session)
    record = await asyncio.to_thread(modes.apply, mode, f"user:{user.id}", owner is True, bool(vision_on),
                                     **_with_modes_fn(modes.apply, fn))
    await _audit(session, user.id, record)
    return record


async def ensure_default(session: AsyncSession, user_id: int | None) -> dict[str, Any] | None:
    """When the bot starts for the first time: human_confirm unless a mode is already stored (an existing auto is never changed)."""
    bot = loader.load()
    modes = getattr(bot, "modes", None) if bot is not None else None
    if modes is None:
        return None
    fn = await trading_mode_fn(session)
    record = await asyncio.to_thread(modes.ensure_default, f"user:{user_id}", **_with_modes_fn(modes.ensure_default, fn))
    if record is not None:
        await _audit(session, user_id, record)
    return record
