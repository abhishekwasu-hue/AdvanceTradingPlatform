"""M88-R S2-S4: build the day's report - one compact table, ONE AI request on the admin's own key, the output check
(refuse and ask once more), then store it once for that trading day. Users read the stored row (`latest`); there is no
per-user AI call anywhere here.

A run that fails (no key, nothing stored for the day, the provider failed, the answer was refused twice) stores NO
report: the day's row keeps status `failed`, the plain reason and when the worker tries again (`next_try_at`).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.daily_report import check, info_line, inputs
from app.daily_report import settings as S
from app.daily_report.universe import expand
from app.db.models import SharedDailyReportRecord, User
from app.universe.sources import ReferenceSource

logger = logging.getLogger(__name__)

SCHEMA = "daily-report/1"
PROMPT_VERSION = "daily-report-v1"
TASK = "narration"                    # the fast tier: it describes a prepared table (app.ai.providers.TASK_TIERS)
RUNNING_STALE = timedelta(minutes=30)
_LOCK = asyncio.Lock()

SYSTEM = (
    "You write a short end-of-day market report for retail traders in India from a table of end-of-day figures. "
    "Describe only what the figures show: trend and structure, areas, breadth, sector strength, volume and open interest. "
    "Never recommend anything. Never use these words in any form: buy, sell, seller, buyer, target, stop, stop-loss, entry, should. "
    "Say 'demand area' and 'supply area', never buyer or seller. Do not tell anyone what to do, do not predict prices, "
    "do not name single price levels to act on. Plain English, short sentences. "
    "Answer with ONE JSON object only, with these keys: index_read, breadth_read, sectors_read, leaders_laggards, "
    "notable_structures, conclusion (each a string of at most 80 words) and stocks (an object: every stock symbol of the "
    "table -> one sentence of at most 25 words on its structure, notable change, volume and open interest).")


class NothingToRun(Exception):
    """The run cannot start (setting empty, flag off, another run going); the message is the admin's plain reason."""


def _f(x: Any, n: int = 2) -> str:
    if x is None:
        return "-"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, (int, float)):
        return f"{x:.{n}f}" if isinstance(x, float) else str(x)
    return str(x)


def _area_text(areas: Sequence[dict[str, Any]]) -> str:
    return "; ".join(f"{a['kind']} {a['low']}-{a['high']} {a['where']}" for a in areas) or "-"


def table(rows: Sequence[dict[str, Any]], breadth: dict[str, Any], sectors: Sequence[dict[str, Any]], day: str) -> str:
    """The ONE compact input table the AI reads (pipe-separated; '-' = not stored)."""
    head = ("symbol|kind|sector|close|chg%|vol_x_avg|deliv%|range_pos%|new_recent_high|new_recent_low|d_structure|d_last_event|"
            "w_structure|vs_swing_high%|vs_swing_low%|areas|fut_oi_chg%|pcr")
    lines = [f"Last close {day}. Figures from the stored NSE end-of-day files.", head]
    for r in rows:
        if r["missing"]:
            lines.append(f"{r['symbol']}|{r['kind']}|{_f(r.get('sector'))}|no close stored for this day")
            continue
        d, w = r["structure_d"], r["structure_w"]
        lines.append("|".join([r["symbol"], r["kind"], _f(r.get("sector")), _f(r["close"]), _f(r["change_pct"]), _f(r["volume_ratio"]),
                               _f(r["delivery_pct"]), _f(r["range_position_pct"], 1), _f(r["new_recent_high"]), _f(r["new_recent_low"]),
                               d["direction"], _f(d.get("last_event")), w["direction"], _f(r["swing_high_pct"]), _f(r["swing_low_pct"]),
                               _area_text(r["areas"]), _f(r["fut_oi_change_pct"]), _f(r["pcr"])]))
    lines.append("breadth|" + "|".join(f"{k}={_f(v)}" for k, v in breadth.items()))
    for s in sectors:
        lines.append(f"sector|{s['sector']}|stocks={s['stocks']}|avg_chg%={_f(s['avg_change_pct'])}|adv={s['advances']}|dec={s['declines']}")
    return "\n".join(lines)


async def _provider(session: AsyncSession, user: User, client: Any = None) -> Any:
    from app.ai import resolve
    provider = await resolve.user_key_provider(session, user, TASK, client=client)
    if provider is None:
        raise NothingToRun("Connect your own AI key in Settings › AI (and accept its consent line) to run the report.")
    provider.prompt_version = PROMPT_VERSION
    return provider


async def ask(provider: Any, prompt: str) -> tuple[dict[str, Any] | None, str | None, int]:
    """At most two requests: the first; a second only when the first was refused (recommendation words or not the
    asked shape). (sections, None, requests) or (None, the plain reason, requests)."""
    reason: str | None = None
    for attempt in (1, 2):
        user = prompt if attempt == 1 else (f"{prompt}\n\nYour previous answer was refused: {reason}. Write it again, "
                                           "describing only, without those words, as ONE JSON object.")
        result = await provider.complete_full(SYSTEM, user, max_tokens=config.DAILY_REPORT_MAX_OUTPUT_TOKENS)
        try:
            sections = check.parse(result.text)
        except ValueError as exc:
            reason = f"the answer was not in the asked form ({exc})"
            continue
        found = check.banned_words(sections)
        if found:
            reason = "it used the words " + ", ".join(found)
            continue
        return sections, None, attempt
    return None, f"The AI answer was refused twice: {reason}. Nothing was stored.", 2


def content(day: date, rows: list[dict[str, Any]], breadth: dict[str, Any], sectors: list[dict[str, Any]],
            sections: dict[str, Any], list_source: dict[str, str]) -> dict[str, Any]:
    from app.markets.service import TOP_N
    reads = sections.get("stocks") or {}
    stocks = [r for r in rows if r["kind"] == "EQ"]
    for r in rows:
        r["read"] = reads.get(r["symbol"].upper())
    moved = sorted((r for r in stocks if not r["missing"] and r.get("change_pct") is not None), key=lambda r: -r["change_pct"])
    return {"schema": SCHEMA, "trade_date": day.isoformat(), "info_line": info_line(day.isoformat()),
            "generated_at": datetime.now(UTC).isoformat(),
            "index": [r for r in rows if r["kind"] == "INDEX"], "stocks": stocks, "breadth": breadth, "sectors": sectors,
            "leaders": [r["symbol"] for r in moved[:TOP_N]], "laggards": [r["symbol"] for r in moved[::-1][:TOP_N]],
            "missing": [r["symbol"] for r in rows if r["missing"]],
            "sections": {k: sections[k] for k in check.SECTIONS}, "list_source": list_source}


async def _row_for(session: AsyncSession, day: date) -> SharedDailyReportRecord:
    row = await session.scalar(select(SharedDailyReportRecord).where(SharedDailyReportRecord.trade_date == day))
    if row is None:
        row = SharedDailyReportRecord(trade_date=day, status="running", attempts=0)
        session.add(row)
    return row


def _aware(t: datetime | None) -> datetime | None:
    return None if t is None else (t if t.tzinfo else t.replace(tzinfo=UTC))


async def run(session: AsyncSession, day: date, *, user: User, trigger: str = "schedule", provider: Any = None,
              source: ReferenceSource | None = None, now: datetime | None = None,
              on_stored: Callable[[AsyncSession, SharedDailyReportRecord], Awaitable[None]] | None = None) -> SharedDailyReportRecord:
    """One run for `day` (a bhavcopy day). Raises NothingToRun before anything is written when it cannot start."""
    now = now or datetime.now(UTC)
    if _LOCK.locked():
        raise NothingToRun("A report run is already going. It finishes in a few minutes.")
    async with _LOCK:
        cfg = await S.read(session)
        if not cfg["symbols"]:
            raise NothingToRun("The Shared report symbols setting is empty, so there is no report.")
        row = await _row_for(session, day)
        updated = _aware(row.updated_at)
        if row.status == "running" and row.id is not None and updated is not None and now - updated < RUNNING_STALE and row.attempts:
            raise NothingToRun("A report run for this day is already going.")
        row.status, row.attempts, row.trigger, row.run_by, row.error = "running", (row.attempts or 0) + 1, trigger, int(user.id), None
        row.updated_at = now
        await session.commit()
        row_id = int(row.id)
        try:
            error = await _build(session, row_id, day, cfg, user, provider, source)
        except NothingToRun as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - any failure is the run's reason; the worker tries again later
            from app.ai import byok
            logger.warning("Daily report for %s failed: %s", day, exc.__class__.__name__)
            error = "The run failed: " + (byok.sanitize_error(str(exc))[:300] or exc.__class__.__name__)
            await session.rollback()
        stored = await session.get(SharedDailyReportRecord, row_id)
        assert stored is not None
        if error is not None:
            stored.status, stored.error = "failed", error[:500]
            stored.next_try_at = (now + timedelta(minutes=config.DAILY_REPORT_RETRY_MINUTES)
                                  if stored.attempts < config.DAILY_REPORT_MAX_ATTEMPTS else None)
            if stored.content_json:                              # a failed re-run keeps the report stored before it
                stored.status = "ok"
        stored.updated_at = datetime.now(UTC)
        await session.commit()
        if error is None and on_stored is not None:
            await on_stored(session, stored)
        return stored


async def _build(session: AsyncSession, row_id: int, day: date, cfg: dict[str, Any], user: User, provider: Any,
                 source: ReferenceSource | None) -> str | None:
    universe = await expand(session, cfg["symbols"], source)
    if not universe.items:
        return " ".join(universe.problems) or "The Shared report symbols give no symbol."
    rows = [await inputs.symbol_row(session, item, day, int(cfg["recent_bars"])) for item in universe.items]
    if all(r["missing"] for r in rows):
        return f"No close is stored for {day.isoformat()} yet."
    breadth, sectors = inputs.breadth(rows), inputs.sectors(rows)
    prompt = table(rows, breadth, sectors, day.isoformat())
    provider = provider if provider is not None else await _provider(session, user)
    sections, refused, requests = await ask(provider, prompt)
    spent = getattr(provider, "spent", {}) or {}
    row = await session.get(SharedDailyReportRecord, row_id)
    assert row is not None
    row.ai_requests = int(spent.get("calls") or requests)
    row.input_tokens, row.output_tokens = int(spent.get("tokens_input") or 0), int(spent.get("tokens_output") or 0)
    row.cost_usd = round(float(spent.get("cost_usd") or 0.0), 6)
    row.symbols = len(rows)
    if sections is None:
        await session.commit()
        return refused
    for p in universe.problems:
        logger.info("Daily report %s: %s", day, p)
    row.content_json = json.dumps(content(day, rows, breadth, sectors, sections, universe.list_source), separators=(",", ":"))
    row.status, row.error, row.next_try_at = "ok", None, None
    await session.commit()
    return None


async def latest(session: AsyncSession) -> SharedDailyReportRecord | None:
    """The newest STORED report (status ok) - what every user reads. No AI call."""
    return await session.scalar(select(SharedDailyReportRecord).where(SharedDailyReportRecord.status == "ok",
                                                                      SharedDailyReportRecord.content_json.is_not(None))
                                .order_by(SharedDailyReportRecord.trade_date.desc()).limit(1))


def run_summary(row: SharedDailyReportRecord) -> dict[str, Any]:
    from app.ai import pricing
    return {"id": row.id, "trade_date": row.trade_date.isoformat(), "status": row.status, "error": row.error, "attempts": row.attempts,
            "ai_requests": row.ai_requests, "input_tokens": row.input_tokens, "output_tokens": row.output_tokens,
            "tokens": row.input_tokens + row.output_tokens, "cost_usd": row.cost_usd, "cost_inr": pricing.usd_to_inr(row.cost_usd),
            "symbols": row.symbols, "trigger": row.trigger, "stored": bool(row.content_json),
            "next_try_at": _iso(row.next_try_at), "updated_at": _iso(row.updated_at)}


def _iso(t: datetime | None) -> str | None:
    aware = _aware(t)
    return aware.isoformat() if aware is not None else None
