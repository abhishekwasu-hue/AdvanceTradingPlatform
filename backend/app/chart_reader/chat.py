"""CA-CHAT (owner 18 / 19 Nov, OPEN_QUESTIONS CA-CHAT-Q1): a chat on the SAME chart of an Analyze-a-chart item.

The trader writes his OWN conditions in plain words; the AI answers each one as met / not met / unclear on that chart,
with its evidence and, when the chart has a price scale, the price range it points at (drawn on the chart). Rules:
- his own Claude key only (the chart reader's key, cap and prices); the estimate is checked against his own daily cap
  before the call, the real cost is logged per message (placeholders only: the prompt and the answer are never stored
  in llm_calls) and shown with the answer;
- every sentence passes the signal-language guard (no buy / sell / target / stop words): a sentence that does not is
  withheld, never rewritten;
- the prompt is generic (it carries only his words and the chart); it is not the owner's method and no book's text;
- one thread per analysis, kept with the analysis (its stored result, key `_chat`, like the share link's state) - it
  goes when the analysis is deleted or expires; never part of a shared copy.
Reference only - nothing here places, changes or proposes an order.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.chart_analyze import share
from app.chart_reader import service
from app.db.models import ChartAnalysisRecord, User

logger = logging.getLogger(__name__)

PROMPT_VERSION = "ca_chat_v1"
TURNS_MAX = 30
TEXT_MAX = 1000
CONDITIONS_MAX = 8
EVIDENCE_MAX = 400
WITHHELD = "Withheld: this sentence used order words."
STATUSES = ("met", "not_met", "unclear")

SYSTEM = (
    "You check a trader's own conditions against one chart picture. Read only what the chart shows. "
    "Split his message into his separate conditions (at most 8) and judge each one on this chart: "
    "\"met\", \"not_met\" or \"unclear\" (unclear when the chart does not show enough). "
    "For each give short evidence in plain structure words (trend, swings, areas, candles, volume, indicators drawn on the chart). "
    "Never use the words buy, sell, target, stop, stop-loss or entry, never advise, never predict. "
    "When the chart has a price scale and a condition points at a price area, give that area as price_low and price_high; "
    "otherwise null. Never invent prices. "
    "Reply with JSON only: {\"conditions\": [{\"condition\": str, \"status\": \"met\"|\"not_met\"|\"unclear\", "
    "\"evidence\": str, \"price_low\": number|null, \"price_high\": number|null}]}"
)


def _media_type(data: bytes) -> str:
    return "image/jpeg" if data[:3] == b"\xff\xd8\xff" else ("image/webp" if data[8:12] == b"WEBP" else "image/png")


def _clean(text: Any, limit: int = EVIDENCE_MAX) -> str:
    out = " ".join(str(text or "").split())[:limit]
    from app.signals import language
    return out if language.is_clean(out) else WITHHELD


def _price(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def parse_answer(text: str) -> list[dict[str, Any]]:
    """The model's JSON reply -> at most CONDITIONS_MAX checked rows (status one of STATUSES, sentences guarded, a price
    range only when both ends are positive numbers, low <= high). Unreadable -> []."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    rows = data.get("conditions") if isinstance(data, dict) else None
    out: list[dict[str, Any]] = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not str(r.get("condition") or "").strip():
            continue
        lo, hi = _price(r.get("price_low")), _price(r.get("price_high"))
        if lo is None or hi is None:
            lo = hi = None
        elif lo > hi:
            lo, hi = hi, lo
        status = r.get("status") if r.get("status") in STATUSES else "unclear"
        out.append({"condition": _clean(r.get("condition"), 200), "status": status, "evidence": _clean(r.get("evidence")),
                    "price_low": lo, "price_high": hi})
        if len(out) >= CONDITIONS_MAX:
            break
    return out


def _facts(result_json: str | None) -> str:
    """A hybrid item's reading of the stored candles as plain facts (the same text the chart reader sends); else ""."""
    parsed, _ = share.split(result_json)
    reading = (parsed or {}).get("reading") if isinstance(parsed, dict) else None
    chart = (parsed or {}).get("chart") if isinstance(parsed, dict) else None
    if not isinstance(reading, dict) or not isinstance(chart, dict):
        return ""
    try:
        from app.chart_analyze import hybrid
        facts = hybrid.facts(reading, width=int(chart.get("width") or 0), height=int(chart.get("height") or 0))
        return service.facts_text(str(reading.get("symbol") or "Chart"), facts)
    except (KeyError, TypeError, ValueError):
        return ""


def thread(row: ChartAnalysisRecord) -> list[dict[str, Any]]:
    return share.chat_of(row.result_json)


async def _save(session: AsyncSession, row: ChartAnalysisRecord, turns: list[dict[str, Any]]) -> None:
    """Writes the chat only while the row is still the analysis we read (a delete / purge meanwhile wins)."""
    old = row.result_json
    if not old:
        raise HTTPException(status_code=409, detail={"message": "Analyze the chart first, then ask about it.", "code": "not_analysed"})
    done = await session.execute(update(ChartAnalysisRecord).where(
        ChartAnalysisRecord.id == row.id, ChartAnalysisRecord.result_json == old)
        .values(result_json=share.with_chat(old, turns)).execution_options(synchronize_session=False))
    if getattr(done, "rowcount", 0) != 1:
        await session.rollback()
        raise HTTPException(status_code=409, detail={"message": "This analysis changed meanwhile. Open it again.", "code": "changed"})
    await session.commit()


async def _image(session: AsyncSession, user: User, row: ChartAnalysisRecord) -> bytes:
    """The chart the conditions are checked on: his own uploaded picture, else (a symbol-picked Advanced analysis has none)
    ATP's own chart of the stored candles that analysis read - the same picture its result shows."""
    try:
        chart, _ = await service.upload_chart(session, user, int(row.id))
        return chart.png
    except HTTPException as exc:
        if exc.status_code != 410:
            raise
    parsed, _ = share.split(row.result_json)
    reading = parsed.get("reading") if isinstance(parsed, dict) else None
    if isinstance(reading, dict) and reading.get("bars"):
        from app.chart_analyze import reading_png
        try:
            return reading_png.render(reading)
        except (ValueError, KeyError, TypeError):
            pass
    raise service.refused(410, "image_gone", "The chart image is no longer stored.")


def _call(client: Any, settings: dict[str, Any], image: bytes, text: str, facts: str) -> Any:
    content: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": _media_type(image), "data": base64.b64encode(image).decode("ascii")}},
        {"type": "text", "text": ("Facts read by code from the stored candles:\n" + facts + "\n\n" if facts else "") + "My conditions:\n" + text},
    ]
    return client.messages.create(model=settings["model"], max_tokens=min(int(settings["max_output_tokens"]), 4000),
                                  system=SYSTEM, messages=[{"role": "user", "content": content}])


async def ask(session: AsyncSession, user: User, settings: dict[str, Any], item_id: int, text: str,
              now: datetime | None = None) -> dict[str, Any]:
    """One chat message on the caller's own analysis: his conditions checked on its chart, the turn stored and returned."""
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    text = " ".join((text or "").split())[:TEXT_MAX]
    if not text:
        raise service.refused(422, "empty", "Write your conditions first.")
    from app.chart_analyze import service as ca_service
    row = await ca_service.own(session, user, item_id)                          # his own item only (404 otherwise)
    image = await _image(session, user, row)
    turns = thread(row)
    if len(turns) >= TURNS_MAX:
        raise service.refused(409, "thread_full", f"This chart already has {TURNS_MAX} questions. Start a new analysis to ask more.")
    key = await service.own_key(session, user)
    if key is None:
        raise service.refused(503, "not_connected", service.NOT_CONNECTED)
    estimate = service.estimate_usd(settings, 1)
    await service.check_cap(session, user, estimate, now)
    try:
        reply = await asyncio.to_thread(_call, service.make_client(key), settings, image, text, _facts(row.result_json))
    except Exception as exc:  # noqa: BLE001 - the API failed; its text is never logged
        logger.warning("ca chat failed (%s) for tenant %s user %s", exc.__class__.__name__, user.tenant_id, user.id)
        await service._log(session, user, settings, None, f"error: {exc.__class__.__name__}", 0.0, PROMPT_VERSION)
        raise service.refused(502, "read_failed", service.FAILED) from None
    usage = getattr(reply, "usage", None)
    result = {"usage": {"input_tokens": getattr(usage, "input_tokens", 0), "output_tokens": getattr(usage, "output_tokens", 0),
                        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
                        "model": getattr(reply, "model", None)}}
    cost = service.cost_usd(settings, result)
    answer_text = "".join(getattr(b, "text", "") for b in getattr(reply, "content", []) or [])
    rows = parse_answer(answer_text)
    await service._log(session, user, settings, result, "ok" if rows else "error: unreadable", cost, PROMPT_VERSION)
    turn = {"question": _clean(text, TEXT_MAX), "answers": rows, "cost_usd": cost, "at": now.isoformat(),
            "unreadable": not rows}
    await _save(session, await ca_service.own(session, user, item_id), [*turns, turn])
    return {"turn": turn, "turns": len(turns) + 1, "estimate_usd": estimate}


async def clear(session: AsyncSession, user: User, item_id: int) -> None:
    from app.chart_analyze import service as ca_service
    row = await ca_service.own(session, user, item_id)
    if thread(row):
        await _save(session, row, [])


__all__ = ["ask", "clear", "thread", "parse_answer", "SYSTEM", "PROMPT_VERSION", "TURNS_MAX", "WITHHELD"]
