"""M91 (owner order): the watchlist's "Screen" - his own saved screen read on a list, and drawn on a stock's chart.

GET  /api/screener/watchlist-screen/screens          his own saved screens (created by him, not archived) for the dropdown;
                                                     a screen that ranks symbols against each other is listed as not usable
GET  /api/screener/watchlist-screen/settings         each list's choice (W1): screen, "Matches only", alert, Telegram
PUT  /api/screener/watchlist-screen/settings/{key}   one list's choice; `screen_id` null = None (nothing chosen)
POST /api/screener/watchlist-screen/check            W2 / W3: the list's symbols checked store-first (M90: the own store
                                                     for the whole list, the reader's own broker only for missing symbols,
                                                     paced, the rest `pending`); the answer has the run's shape, so the page
                                                     streams it with the screener's own batch queue. W4: new matches are
                                                     alerted only when HIS choice for that list says so (default OFF)
POST /api/screener/watchlist-screen/overlay          C1: one stock's chart overlay - the screen's own drawables (the
                                                     evidence pack), a marker per bar where the whole screen held, and the
                                                     legend (every condition with its mark and value on the last closed bar)

The list and the overlay read the ONE shared evaluation `watch_screen.screen_marks` (C2). Behind `screener_v2` and
`watchlist_screen` (OFF by default; 503 while off, the page then shows no Screen dropdown). Per user and tenant: a screen another member saved, or another
organisation's, answers 404; the bars are the reader's own (M86: never another member's broker data). No default screen,
no default parameter, no new rule. Informational only: nothing here places, proposes or changes an order.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.core.rate_limit import user_rate_limit
from app.db.models import ScreenRecord, User
from app.db.session import get_session, release_connection
from app.platform.controls import require_flag
from app.screener import nodes, reasons
from app.screener import ranking as ranking_gate
from app.screener import routes as R
from app.screener import watch_screen as W

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/screener/watchlist-screen", tags=["screener"])
overlay_rate_limit = user_rate_limit("watchlist_screen_overlay", limit=60, window_seconds=60)

LIST_KEY = r"^(o:[0-9]{1,12}|s:[A-Za-z0-9_.-]{1,80})$"
MAX_LISTS = 200                         # list choices one user keeps (a watchlist's own maximum count is lower)
MAX_ALERT_NAMES = 10                    # symbols named in one alert; the rest are counted
NOT_FOUND = "Screen not found"
RANKS = "This screen ranks symbols against each other, so it cannot be read one stock at a time."
DISCLAIMER = "Stocks that meet your own screen on the data shown; they are not recommendations."


class ListChoice(BaseModel):
    """One list's choice. Everything OFF / None until he chooses it (no default screen)."""
    model_config = ConfigDict(extra="forbid", strict=True)
    screen_id: int | None = Field(default=None, ge=1)
    matches_only: bool = False
    alert: bool = False
    telegram: bool = False


class CheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    list_key: str = Field(pattern=LIST_KEY)
    list_name: str = Field(default="", max_length=80, description="the list's name, for the alert's words only")
    screen_id: int = Field(ge=1)
    symbols: list[str] = Field(min_length=1, max_length=R.MAX_REQUEST_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")


class OverlayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    screen_id: int = Field(ge=1)
    symbol: str = Field(min_length=1, max_length=60)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")


async def _enabled(session: AsyncSession, user: User) -> None:
    await R._flag(session, user)
    await require_flag(session, W.FLAG, user.tenant_id)


async def _own_screen(session: AsyncSession, user: User, screen_id: int) -> ScreenRecord:
    """His own saved screen (he created it, not archived); anything else is 404 as if it did not exist."""
    row = await session.scalar(select(ScreenRecord).where(ScreenRecord.id == screen_id, ScreenRecord.tenant_id == user.tenant_id,
                                                          ScreenRecord.created_by == user.id, ScreenRecord.archived.is_(False)))
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return row


async def _compiled_own(session: AsyncSession, user: User, row: ScreenRecord, exchange: str) -> tuple[Any, Any, dict[str, Any]]:
    """The saved screen as the run route compiles and gates it: its own timeframe and its own parameters, nothing added."""
    params = dict(json.loads(row.params_json or "{}"))
    ast, validated = R._compiled(json.loads(row.ast_json), row.base_tf, params)
    await R._gate(session, user, ast, validated, params, exchange)
    if not validated.ok:
        raise HTTPException(status_code=422, detail={"message": "the screen did not pass validation",
                                                     "problems": [p.as_dict() for p in validated.problems]})
    if validated.cross_sectional:
        raise HTTPException(status_code=422, detail=RANKS)
    missing = R.missing_data(ast, reference=True, chain=True)
    if missing:
        raise HTTPException(status_code=422, detail=f"{', '.join(m + '()' for m in missing)} need data the screener does not load yet")
    return ast, validated, params


def _usable(row: ScreenRecord) -> tuple[bool, str | None]:
    try:
        ast, validated = R._compiled(json.loads(row.ast_json), row.base_tf, dict(json.loads(row.params_json or "{}")))
    except Exception:  # noqa: BLE001 - a screen saved by an older release that no longer compiles is listed, not usable
        return False, "This screen needs to be opened and saved again in the Screener."
    if not validated.ok:
        return False, "This screen needs to be opened and saved again in the Screener."
    if validated.cross_sectional:
        return False, RANKS
    if R.missing_data(ast, reference=True, chain=True):
        return False, "This screen needs data the screener does not load yet."
    return True, None


# ---------------------------------------------------------------------------------------------- the screens and W1
@router.get("/screens")
async def screens(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _enabled(session, user)
    rows = await session.scalars(select(ScreenRecord).where(ScreenRecord.tenant_id == user.tenant_id, ScreenRecord.created_by == user.id,
                                                            ScreenRecord.archived.is_(False)).order_by(ScreenRecord.name, ScreenRecord.id))
    out = []
    for r in rows:
        ok, why = _usable(r)
        out.append({"id": r.id, "name": r.name, "base_tf": r.base_tf, "usable": ok, **({"why": why} if why else {})})
    return {"screens": out}


def _choices(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found = raw.get("lists")
    lists: dict[str, Any] = found if isinstance(found, dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in lists.items():
        try:
            out[str(key)] = ListChoice.model_validate(value).model_dump()
        except Exception:  # noqa: BLE001 - one unreadable choice never loses the others
            continue
    return out


async def _read_choices(session: AsyncSession, user: User) -> tuple[Any, dict[str, dict[str, Any]]]:
    from app.ai import watch
    row, raw = await watch.read_marks(session, user, W.SETTINGS)
    return row, _choices(raw)


@router.get("/settings")
async def get_settings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _enabled(session, user)
    _, lists = await _read_choices(session, user)
    return {"lists": lists}


@router.put("/settings/{list_key}")
async def put_setting(list_key: str, body: ListChoice, user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """W1: one list's choice, remembered on the server (phone and desktop agree). Choosing None removes it."""
    import re

    from app.ai import watch
    await _enabled(session, user)
    if not re.fullmatch(LIST_KEY, list_key):
        raise HTTPException(status_code=422, detail="unknown list")
    if body.screen_id is not None:
        await _own_screen(session, user, body.screen_id)
    row, lists = await _read_choices(session, user)
    before = lists.get(list_key, {})
    if body.screen_id is None:
        lists.pop(list_key, None)
    else:
        if list_key not in lists and len(lists) >= MAX_LISTS:
            raise HTTPException(status_code=409, detail=f"at most {MAX_LISTS} lists can each keep a screen")
        lists[list_key] = body.model_dump()
    now = datetime.now(UTC)
    await watch.write_marks(session, user, row, {"lists": lists}, now, W.SETTINGS)
    if before.get("screen_id") != body.screen_id or not body.alert:
        # W4: a new screen (or the alert turned off) starts from nothing; its first check is the baseline, never an alert
        seen_row, seen = await watch.read_marks(session, user, W.SEEN)
        if list_key in seen:
            seen.pop(list_key)
            await watch.write_marks(session, user, seen_row, seen, now, W.SEEN)
    return {"list_key": list_key, "choice": lists.get(list_key)}


# -------------------------------------------------------------------------------------------------- W2 / W3: check
def _evaluate(ast: Any, validated: Any, universe: list[Any], base_tf: str, params: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(d.symbol, W.screen_marks(ast, validated, d, base_tf=base_tf, params=params)) for d in universe]


async def _holidays(session: AsyncSession, exchange: str) -> set[Any]:
    from app.market_data.calendar import load_holidays
    try:
        return set(await load_holidays(session, "NSE" if exchange in ("NSE", "NFO") else exchange))
    except Exception:  # noqa: BLE001 - no calendar rows: weekends only (the bar-close engine's own fallback)
        return set()


@router.post("/check", dependencies=[Depends(R.run_rate_limit)])
async def check(body: CheckBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """W2 / W3: which of the list's symbols meet his screen on the last closed bar (the shared evaluation, C2)."""
    await _enabled(session, user)
    row = await _own_screen(session, user, body.screen_id)
    screen_name, base_tf = row.name, row.base_tf
    ast, validated, params = await _compiled_own(session, user, row, body.exchange)
    symbols = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
    holidays = await _holidays(session, body.exchange)
    budget: dict[str, Any] = {}
    pending: list[str] = []
    store = await R.store_first(session, user.tenant_id)
    universe, problems, data_source, systems = await R.load_universe(session, user, ast, validated, params, symbols, body.exchange, base_tf,
                                                                     {}, release=True, budget=budget, store=store, pending=pending)
    if pending:
        later = set(pending)
        symbols = [s for s in symbols if s not in later]
    await release_connection(session)
    with ranking_gate.active(systems):
        marked = await asyncio.to_thread(_evaluate, ast, validated, universe, base_tf, params)
    results: list[dict[str, Any]] = []
    newest: Any = None
    for symbol, m in marked:
        r = reasons.row(symbol, m["matched"], m["reason"])
        r["marks"] = W.marks_of(m["rows"])
        results.append(r)
        if m["bar"] is not None and (newest is None or m["bar"] > newest):
            newest = m["bar"]
    results += [reasons.row(s, False, reasons.as_reason(why)) for s, why in problems.items() if s not in {x for x, _ in marked}]
    matched = [str(r["symbol"]) for r in results if r["matched"]]
    alerted = await _alert_new(session, user, body, screen_name, symbols, matched)
    nxt = W.next_close(datetime.now(UTC), base_tf, holidays)
    out: dict[str, Any] = {
        "text": nodes.to_text(ast), "screen": {"id": row.id, "name": screen_name}, "base_tf": base_tf, "data_source": data_source,
        "scanned": len(symbols), "matched": matched, "results": results,
        "funnel": {"universe": len(symbols), "with_data": len(universe), "stages": []},
        "budget": budget, "bar": newest.isoformat() if newest is not None else None,
        "next_check_at": (nxt.timestamp() + W.CHECK_GRACE_SECONDS) * 1000 if nxt is not None else None,
        "alerted": alerted, "disclaimer": DISCLAIMER,
    }
    if pending:
        out["pending"] = pending
    return out


async def _alert_new(session: AsyncSession, user: User, body: CheckBody, screen_name: str, checked: list[str], matched: list[str]) -> list[str]:
    """W4: only when HIS choice for this list has `alert` on (default OFF) and the screen is the one chosen. A stock that
    newly enters the matches (not matched at the last check of this list) is alerted once - in the app, and in his own
    Telegram chat when `telegram` is on. The first check after choosing is the baseline: nothing is alerted for it."""
    from app.ai import watch
    _, lists = await _read_choices(session, user)
    choice = lists.get(body.list_key) or {}
    if not choice.get("alert") or choice.get("screen_id") != body.screen_id:
        return []
    seen_row, seen = await watch.read_marks(session, user, W.SEEN)
    mine = seen.get(body.list_key) if isinstance(seen.get(body.list_key), dict) else None
    now_set = set(matched)
    if mine is None or mine.get("screen_id") != body.screen_id:
        fresh: list[str] = []
        before: set[str] = set()
    else:
        before = {str(s) for s in mine.get("matched") or []}
        fresh = [s for s in matched if s not in before]
    after = sorted((before - set(checked)) | now_set)
    seen[body.list_key] = {"screen_id": body.screen_id, "matched": after}
    await watch.write_marks(session, user, seen_row, seen, datetime.now(UTC), W.SEEN)
    if fresh:
        await _deliver(session, user, body, screen_name, fresh, bool(choice.get("telegram")))
    return fresh


def alert_text(screen_name: str, list_name: str, fresh: list[str]) -> tuple[str, str]:
    """The alert's title and line: which of his stocks now meet his screen (structure words, no advice)."""
    names = ", ".join(fresh[:MAX_ALERT_NAMES]) + (f" and {len(fresh) - MAX_ALERT_NAMES} more" if len(fresh) > MAX_ALERT_NAMES else "")
    count = f"{len(fresh)} new {'match' if len(fresh) == 1 else 'matches'}"
    where = f" in {list_name}" if list_name.strip() else ""
    return f"{screen_name}: {count}", f"{names} now {'meets' if len(fresh) == 1 else 'meet'} your screen \"{screen_name}\"{where}."


async def _deliver(session: AsyncSession, user: User, body: CheckBody, screen_name: str, fresh: list[str], telegram: bool) -> None:
    from app.core.enums import NotificationSeverity, NotificationType
    from app.notifications.service import notify
    title, message = alert_text(screen_name, body.list_name, fresh)
    meta = json.dumps({"watchlist_screen": {"list_key": body.list_key, "screen_id": body.screen_id, "symbols": fresh[:50]}}, sort_keys=True)
    await notify(session, user.tenant_id, NotificationType.SCREEN_ALERT, title=title, message=message, severity=NotificationSeverity.INFO,
                 user_id=user.id, metadata_json=meta, channels=False)
    if not telegram:
        return
    try:
        from app.alerts.channels import decrypt_config
        from app.alerts.dispatcher import send_telegram
        from app.rhythm.deliver import destination
        target = await destination(session, user)
        if target is not None:
            text = f"<b>{html.escape(title)}</b>\n{html.escape(message)}\n<i>Information from your own screen; not a recommendation.</i>"
            await send_telegram(decrypt_config(target[0]), text, None, chat_id=target[1])  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - the alert stays in the app; Telegram is best effort
        logger.info("Watchlist screen Telegram for user %s not sent: %s", user.id, type(exc).__name__)


# ------------------------------------------------------------------------------------------------- C1: the overlay
@router.post("/overlay", dependencies=[Depends(overlay_rate_limit)])
async def overlay(body: OverlayBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """C1: his screen drawn on one stock's chart - from the same shared evaluation as the list (C2)."""
    from app.screener import evidence as E
    await _enabled(session, user)
    row = await _own_screen(session, user, body.screen_id)
    ast, validated, params = await _compiled_own(session, user, row, body.exchange)
    symbol = body.symbol.strip().upper()
    store = await R.store_first(session, user.tenant_id)
    universe, problems, data_source, systems = await R.load_universe(session, user, ast, validated, params, [symbol], body.exchange,
                                                                     row.base_tf, {}, release=True, budget={}, store=store, pending=[])
    await release_connection(session)
    head = {"symbol": symbol, "exchange": body.exchange, "base_tf": row.base_tf, "screen": {"id": row.id, "name": row.name},
            "text": nodes.to_text(ast), "data_source": data_source, "disclaimer": DISCLAIMER}
    data = next((d for d in universe if d.symbol == symbol), None)
    if data is None:
        why = problems.get(symbol)
        return {**head, "available": False, "reason": str(why) if why else "No closed bars for this stock yet."}

    def build() -> dict[str, Any]:
        marks = W.screen_marks(ast, validated, data, base_tf=row.base_tf, params=params, bars=W.OVERLAY_BARS)
        try:
            pack = E.build_pack(ast, validated, data, base_tf=row.base_tf, params=params, window=W.OVERLAY_BARS, exchange=body.exchange,
                                data_source=data_source, screen_name=row.name)
        except Exception as exc:  # noqa: BLE001 - nothing to draw; the legend still says what was checked
            logger.info("Watchlist screen overlay pack for %s not built: %s", symbol, type(exc).__name__)
            pack = None
        return {"marks": marks, "pack": pack}

    with ranking_gate.active(systems):
        got = await asyncio.to_thread(build)
    marks, pack = got["marks"], got["pack"]
    base = data.frames[row.base_tf]
    tail = base.index[-len(marks["hits"]):] if marks["hits"] else base.index[:0]
    hits = [E._iso(t) for t, h in zip(tail, marks["hits"], strict=False) if h]
    return {**head, "available": True, "matched": marks["matched"], "reason": str(marks["reason"]) if marks["reason"] else None,
            "legend": marks["rows"], "hits": hits,
            "as_of": pack["as_of"] if pack else {"bar_start": E._iso(base.index[-1])},
            "bars": pack["bars"] if pack else None, "drawables": pack["drawables"] if pack else [],
            "matched_index": pack["matched_index"] if pack else None}


__all__ = ["router", "ListChoice", "alert_text", "LIST_KEY"]
