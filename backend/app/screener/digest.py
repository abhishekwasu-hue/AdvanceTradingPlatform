"""U9 TX3 (docs/specs/ATP_SCREENER_TAXONOMY_ADDENDUM.md §3 "setup-of-the-day digest"): at a time the user chooses, on
trading days, one message per method folder with the first matches of the user's pinned screens - behind the
`screener_tx3` flag (off by default).

* **Personal (BT1b).** A digest belongs to one user: their pinned screens (at most `MAX_PINNED`: library ids, their
  `.short` mirrors or their organisation's saved screens), their symbol list, run through their own broker session when
  the bring-your-own-broker data tier is on (`fetch_frames(user_id=...)`, the same path as `/run`), with the data tier
  that was actually read stated in the digest. It is kept on the user's own row (shown in-app to them only) and sent
  only to their own linked private Telegram chat - never to an organisation channel.
* **Closed bars only.** The screens run on the last closed bar, like `/run`; nothing is read ahead.
* **Evidence.** With `evidence_charts` on, the first match of each screen gets its evidence pack (owner-only), and the
  Telegram message of its method folder carries the image.
* **Wording.** "First matches in your list order" - never "top picks"; every message ends with the no-advice note, and
  `lint()` checks the fixed wording.

Research only (ADR-0006): nothing here places, changes or blocks an order.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, time, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScreenDigestRecord, User
from app.screener import taxonomy as tx

logger = logging.getLogger(__name__)

FLAG = "screener_tx3"
MAX_PINNED = 5
MAX_SYMBOLS = 50
FIRST_PER_SCREEN = 5
MAX_PER_CYCLE = 20                       # digests built per worker pass (the rest go next cycle)
NOTE = ("Matches of your pinned screens on the last closed bar, the first ones in your list order. Screens filter; they do "
        "not predict, and this is not advice.")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SYMBOL = re.compile(r"^[A-Z0-9&._:-]{1,40}$")          # the alert rules' and the breakout runs' symbol shape
NEEDS_FLAGS = ("screener_v2", "screener_gallery", FLAG)  # the routes' flags: the worker honours the same kill switches


class DigestSettings(BaseModel):
    enabled: bool = False
    send_time: str = Field(default="16:00", description="HH:MM, India time; sent once per trading day at or after it")
    pinned: List[str] = Field(default_factory=list, max_length=MAX_PINNED)
    symbols: List[str] = Field(default_factory=list, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")

    @field_validator("send_time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _TIME.match(v):
            raise ValueError("send_time must be HH:MM (00:00-23:59, India time)")
        return v

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: List[str]) -> List[str]:
        out = list(dict.fromkeys(s.strip().upper() for s in v if s.strip()))
        bad = [s for s in out if not _SYMBOL.fullmatch(s)]
        if bad:                                         # review of TX3: a symbol is short and plain (it is stored)
            raise ValueError(f"invalid symbol(s): {', '.join(b[:40] for b in bad[:5])}")
        return out

    @field_validator("pinned")
    @classmethod
    def _pinned(cls, v: List[str]) -> List[str]:
        out = list(dict.fromkeys(r.strip() for r in v if r.strip()))
        if any(len(r) > 120 for r in out):
            raise ValueError("a pinned screen id is at most 120 characters")
        return out


def settings_of(row: Optional[ScreenDigestRecord]) -> Dict[str, Any]:
    if row is None:
        return {**DigestSettings().model_dump(), "last_sent_on": None, "last_status": None, "last_digest": None}
    return {"enabled": row.enabled, "send_time": row.send_time, "pinned": json.loads(row.pinned_json or "[]"),
            "symbols": json.loads(row.symbols_json or "[]"), "exchange": row.exchange,
            "last_sent_on": row.last_sent_on.isoformat() if row.last_sent_on else None, "last_status": row.last_status,
            "last_digest": json.loads(row.last_digest_json) if row.last_digest_json else None}


async def own_row(session: AsyncSession, user: User) -> Optional[ScreenDigestRecord]:
    return await session.scalar(select(ScreenDigestRecord).where(ScreenDigestRecord.user_id == user.id,
                                                                 ScreenDigestRecord.tenant_id == user.tenant_id))


async def save(session: AsyncSession, user: User, body: DigestSettings) -> ScreenDigestRecord:
    row = await own_row(session, user)
    if row is None:
        row = ScreenDigestRecord(tenant_id=user.tenant_id, user_id=user.id)
        session.add(row)
    row.enabled, row.send_time, row.exchange = body.enabled, body.send_time, body.exchange
    row.pinned_json, row.symbols_json = json.dumps(body.pinned), json.dumps(body.symbols)
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return row


# --- building --------------------------------------------------------------------------------------------------------

def _detail(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)[:200]
    return str(detail)[:200]


def run_units(pinned: int, fetches: int) -> int:
    """`/run` calls a digest is worth (the preview is charged this on the shared bucket, like a confluence)."""
    return max(1, int(pinned)) + max(0, int(fetches) - 1)


async def compiled_pins(session: AsyncSession, user: User, pinned: Sequence[str]) -> Tuple[List[Tuple[Any, Any, Any]], List[str]]:
    """The pinned screens resolved and validated like `/run` validates (problems listed, never raised)."""
    from app.platform.controls import flag_enabled
    from app.screener import library as lib
    from app.screener import library_routes as lr
    from app.screener.parser import parse
    taxonomy = tx.load()
    hidden = await lr._hidden(session, user)
    mirrors_on = await flag_enabled(session, lib.FLAG, user.tenant_id)
    problems: List[str] = []
    compiled: List[Tuple[Any, Any, Any]] = []
    for ref in pinned:
        if ref.endswith(lib.MIRROR_SUFFIX) and not mirrors_on:
            problems.append(f"{ref}: mirror screens are off for your organisation")
            continue
        try:
            screen = await lr._resolve(session, user, ref, taxonomy, hidden)
            ast = lib.inline_params(parse(screen.text), screen.params)
            compiled.append((screen, ast, await lr._compile(session, user, screen.name, ast, screen.base_tf)))
        except HTTPException as exc:
            problems.append(f"{ref}: {_detail(exc)}")
        except ValueError as exc:
            problems.append(f"{ref}: {str(exc)[:200]}")
    return compiled, problems


async def build(session: AsyncSession, user: User, pinned: Sequence[str], symbols: Sequence[str], exchange: str,
                now: Optional[datetime] = None, *, store_evidence: bool = True,
                compiled: Optional[Tuple[List[Tuple[Any, Any, Any]], List[str]]] = None) -> Dict[str, Any]:
    """The digest of the pinned screens over the symbols, as of `now` (closed bars). Problems are listed, never raised.
    `store_evidence=False` (the preview, review of TX3): nothing is stored - no evidence pack is kept."""
    from app.screener import library_routes as lr
    now = now or datetime.now(timezone.utc)
    taxonomy = tx.load()
    pins, problems = compiled if compiled is not None else await compiled_pins(session, user, pinned)
    problems = list(problems)
    by_tf: Dict[str, Dict[str, int]] = {}
    for s, _, validated in pins:
        lr._merge(by_tf.setdefault(s.base_tf, {}), validated.fetch_lookback)
    results: Dict[str, List[str]] = {}
    evidence: Dict[str, Optional[int]] = {}
    tiers: Dict[str, Any] = {}
    from app.screener import evidence_store
    store_evidence = store_evidence and await evidence_store.enabled(session, user.tenant_id)
    for base_tf, lookback in by_tf.items():
        try:
            universe, fetch_problems, source = await lr._universe(session, user, list(symbols), exchange, base_tf, lookback)
        except HTTPException as exc:
            problems.append(f"{base_tf} bars: {_detail(exc)}")
            continue
        tiers[base_tf] = source
        problems += [f"{sym}: {why}" for sym, why in list(fetch_problems.items())[:10]]
        for s, ast, validated in pins:
            if s.base_tf != base_tf:
                continue
            got = await lr._run(session, user, ast, validated, universe, base_tf, exchange)
            results[s.ref] = [sym for sym in symbols if got.get(sym, (False, None))[0]]
            evidence[s.ref] = None
            if store_evidence and results[s.ref]:
                evidence[s.ref] = await _evidence(session, user, s, ast, validated, universe, results[s.ref][0], base_tf, exchange,
                                                  source, per_user="data_tier" in source)
    methods: Dict[str, List[Dict[str, Any]]] = {}
    for s, _, _ in pins:
        if s.ref not in results:
            continue
        found = results[s.ref]
        methods.setdefault(s.method, []).append({"ref": s.ref, "name": s.name, "base_tf": s.base_tf, "first": found[:FIRST_PER_SCREEN],
                                                 "more": max(0, len(found) - FIRST_PER_SCREEN), "evidence_id": evidence.get(s.ref)})
    labels = {m.id: m.label for m in taxonomy.methods}
    folders = [{"method": m, "label": labels.get(m, m), "screens": methods[m]} for m in labels if m in methods]
    from app.market_data.calendar import IST
    return {"day": now.astimezone(IST).date().isoformat(), "built_at": now.isoformat(), "methods": folders, "scanned": len(symbols),
            "sources": tiers, "problems": problems, "note": NOTE}


async def _evidence(session: AsyncSession, user: User, screen: Any, ast: Any, validated: Any, universe: List[Any], symbol: str,
                    base_tf: str, exchange: str, source: Dict[str, Any], per_user: bool) -> Optional[int]:
    """The evidence pack of the screen's first match (owner-only when it came through the user's own session)."""
    from app.screener import evidence_store
    try:
        if await evidence_store.daily_room(session, user.tenant_id) <= 0:
            return None
        chosen = [d for d in universe if d.symbol == symbol]
        packs = await asyncio.to_thread(evidence_store._build_for, ast, validated, universe, chosen, base_tf, {}, exchange,
                                        source.get("data_source"), None, screen.name, 1)
        pack = packs.get(symbol)
        if pack is None:
            return None
        row = evidence_store._record(pack, tenant_id=user.tenant_id, user_id=user.id, per_user=per_user)
        session.add(row)
        await session.flush()
        return int(row.id)
    except Exception as exc:  # noqa: BLE001 - evidence never fails a digest
        logger.warning("Digest evidence not stored: %s", exc.__class__.__name__)
        return None


def messages(digest: Dict[str, Any]) -> List[str]:
    """One plain-text message per method folder (the Telegram text and the in-app preview)."""
    out: List[str] = []
    for folder in digest.get("methods", []):
        lines = [f"Setups of the day - {folder['label']} ({digest['day']})"]
        for s in folder["screens"]:
            found = ", ".join(s["first"]) if s["first"] else "no match"
            more = f" (+{s['more']} more)" if s["more"] else ""
            lines.append(f"{s['name']}: {found}{more}")
        lines.append(NOTE)
        out.append("\n".join(lines))
    if not out:
        out.append(f"Setups of the day ({digest.get('day')}): none of your pinned screens could run."
                   + (" " + "; ".join(digest.get("problems", [])[:3]) if digest.get("problems") else "") + "\n" + NOTE)
    return out


async def deliver(session: AsyncSession, user: User, digest: Dict[str, Any], client: Any = None) -> str:
    """To the user's own linked private chat only (text per folder, then the evidence images). Returns the status."""
    import html

    from app.alerts.channels import TelegramConfig
    from app.alerts.dispatcher import send_telegram
    from app.screener import evidence_store
    chat = await evidence_store.own_chat(session, user.tenant_id, user.id)
    if chat is None:
        return "Kept in the app: link your Telegram under Settings > Notifications to get it there too."
    token = await evidence_store._bot(session, user.tenant_id)
    if token is None:
        return "Kept in the app: your organisation's Telegram bot is not set up or is off."
    sent = images = 0
    try:
        config = TelegramConfig(bot_token=token, chat_id=chat)
        for text, folder in zip(messages(digest), digest.get("methods", []) or [None]):
            await send_telegram(config, html.escape(text), client=client, chat_id=chat)
            sent += 1
            for s in (folder or {}).get("screens", []):
                row = await evidence_store.readable(session, user, s["evidence_id"]) if s.get("evidence_id") else None
                if row is not None:
                    png, caption = await asyncio.to_thread(evidence_store.image_and_caption, row)
                    from app.ai.vision_audit import send_photo
                    await send_photo(token, chat, png, caption, client)
                    images += 1
    except Exception as exc:  # noqa: BLE001 - the digest stays in the app
        return f"Kept in the app; Telegram failed after {sent} message(s): {str(exc).replace(token, '[redacted]')[:150]}"
    finally:
        token = ""
    return f"Sent to your Telegram: {sent} message(s), {images} image(s)."


# --- the worker's pass ----------------------------------------------------------------------------------------------

async def _flags_on(session: AsyncSession, tenant_id: int) -> bool:
    """Every flag the digest routes need (`NEEDS_FLAGS`), so turning the screener or the gallery off stops the worker too."""
    from app.platform.controls import flag_enabled
    for name in NEEDS_FLAGS:
        if not await flag_enabled(session, name, tenant_id):
            return False
    return True


def due(row: ScreenDigestRecord, now: datetime, holidays: Iterable[Any] = ()) -> bool:
    from app.market_data.calendar import IST, is_trading_day
    local = now.astimezone(IST)
    hh, mm = (int(x) for x in row.send_time.split(":"))
    return (row.enabled and is_trading_day(local.date(), holidays) and local.time() >= time(hh, mm)
            and row.last_sent_on != local.date())


async def run_due(session_factory: Any, now: Optional[datetime] = None, client: Any = None) -> int:
    """Builds and delivers every due digest (at most MAX_PER_CYCLE), each in its own session. Returns how many."""
    from app.execution import approval
    from app.market_data.calendar import IST, load_holidays
    from app.secrets_store.envelope import ensure_tenant_key
    now = now or datetime.now(timezone.utc)
    async with session_factory() as session:
        holidays = await load_holidays(session, "NSE")
        today_ist = now.astimezone(IST).date()
        # review of TX3: only rows not yet built today come back (the time of day and the trading day are checked below)
        rows = [r for r in await session.scalars(select(ScreenDigestRecord).where(
                    ScreenDigestRecord.enabled.is_(True),
                    or_(ScreenDigestRecord.last_sent_on.is_(None), ScreenDigestRecord.last_sent_on != today_ist))
                .order_by(ScreenDigestRecord.id)) if due(r, now, holidays)]
        ids = [r.id for r in rows[:MAX_PER_CYCLE]]
    done = 0
    for ident in ids:
        async with session_factory() as session:
            row = await session.get(ScreenDigestRecord, ident)
            if row is None or not due(row, now, holidays):
                continue
            user = await session.get(User, row.user_id)
            day = now.astimezone(IST).date()
            try:
                if user is None or not user.is_active or user.tenant_id != row.tenant_id:
                    status = "Not built: the user is no longer an active member."
                elif not await _flags_on(session, row.tenant_id):
                    # review of TX3 (MED): the screener's kill switches (screener_v2, screener_gallery) stop the digest too
                    status = "Not built: the digest is turned off for this organisation."
                elif not approval.can_trade(user):
                    status = "Not built: running screens needs a trading role."
                else:
                    await ensure_tenant_key(session, row.tenant_id)
                    digest = await build(session, user, json.loads(row.pinned_json or "[]"), json.loads(row.symbols_json or "[]"),
                                         row.exchange, now)
                    row.last_digest_json = json.dumps(digest)
                    status = await deliver(session, user, digest, client)
                    done += 1
            except Exception as exc:  # noqa: BLE001 - one user's digest never stops the others
                logger.exception("Screen digest %s failed", ident)
                await session.rollback()
                row = await session.get(ScreenDigestRecord, ident)
                if row is None:
                    continue
                status = f"Not built: {exc.__class__.__name__}"
            row.last_sent_on, row.last_status = day, status[:300]
            await session.commit()
    return done


def lint() -> List[str]:
    """The fixed wording of the digest: no advice words."""
    sample = messages({"day": "2026-01-01", "methods": [{"label": "Indicator", "screens": [{"name": "Momentum", "first": ["AAA"], "more": 2}]}]})
    return [f"advice wording {tx.advice_words(t)}" for t in (NOTE, *sample) if tx.advice_words(t)]


__all__ = ["NEEDS_FLAGS", "run_units", "compiled_pins", "FLAG", "NOTE", "MAX_PINNED", "MAX_SYMBOLS", "FIRST_PER_SCREEN", "DigestSettings", "settings_of", "own_row", "save", "build",
           "messages", "deliver", "due", "run_due", "lint"]
