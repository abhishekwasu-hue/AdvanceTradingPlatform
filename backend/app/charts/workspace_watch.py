"""M76 MP-11 (owner order M76 D-1; docs/design/M76_REVIEW.md §4; flag `copilot_workspace`): watch cards from the Copilot
workspace - opt-in information cards about his OWN watched areas, short strikes, IV, margin and his own square-off time,
in the app and (his choice) his own Telegram chat, each with "Open here" (the workspace opens in place, same tab).

His choices live in his prefs (`workspace_watch`, app/me/prefs.py `WorkspaceWatchPrefs`): every type OFF and every number
Not set until he saves one; a type with a number cannot be turned on without HIS number (ATP chooses none, M68).

* `area_entry`       - the last price is inside one of his watched areas (the areas he picked from the workspace's
                       reading: its side, its edges, its chart's timeframe);
* `area_rejection`   - a commitment candle at one of his watched areas on that area's own chart and timeframe: ATP's
                       ported level gate, called unchanged through the reading's `commitment_part` (M75Q-4 default),
                       on closed bars after he started watching it (`since`; never a bar before, no look-ahead);
* `strike_points`    - a short option of his whose underlying is within his N points of the strike (or past it);
* `iv_spike`         - IV at his option's strike moved by his IV points between the two latest stored chain readings
                       (the Copilot watch's own `chain_cards`, unchanged, with only its IV part);
* `margin_use`       - his broker's margin in use is at or above his % of his margin (his OWN broker session's margin
                       read; PAPER has no broker margin, so no card - never a guessed figure);
* `cut_off_reminder` - his N minutes before his own square-off time (Trade settings > Exits), on a trading day; his
                       square-off time not saved = no reminder.

Rules (ADR-0004, ADR-0006): information only. Nothing here places, changes, closes or blocks an order or an exit; no
proposal row. Every number is code arithmetic on his own rows, his own broker's answer and the stored readings. Words pass
`rhythm.words.clean` (no buy / sell / target / stop wording, no advice). Delivery is the Copilot watch's own: a
notification addressed to him alone (`COPILOT_WATCH`, off the shared channels) carrying where "Open here" opens, and his
own Telegram chat when he turns `telegram` on. Dedupe like the Copilot watch: a condition is sent once while it holds and
again only after it cleared; a one-off event once (an internal marks row, `workspace_watch_marks`). Behind `ui_v2` +
`copilot_workspace` (the worker skips an organisation while either is off; the settings route answers 404). No
migration: his choices and the marks are prefs rows.
"""
from __future__ import annotations

import html
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import watch
from app.ai.watch import Card, _n
from app.auth.dependencies import get_current_user
from app.db.models import User, UserPrefsRecord
from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/charts/workspace", tags=["charts"])
FLAG = "copilot_workspace"
UI_V2 = "ui_v2"
PREFS = "workspace_watch"
MARKS = "workspace_watch_marks"      # internal: not in app/me/prefs.py NAMESPACES, so no route reads or writes it
KINDS = ("area_entry", "area_rejection", "strike_points", "iv_spike", "margin_use", "cut_off_reminder")
TITLE = "Watch card"
SIDE_ROLE = {"buyer": "demand", "seller": "supply"}


def _area_words(a: dict[str, Any]) -> str:
    return f"{a['side']} area {_n(float(a['low']))} - {_n(float(a['high']))} ({a['tf']})"


# --- the checks (pure: rows in, cards out) ------------------------------------------------------------------------------

def area_entry_cards(areas: Sequence[dict[str, Any]], prices: dict[str, float]) -> list[Card]:
    """His watched areas whose symbol's last price is inside the area (edges included)."""
    out: list[Card] = []
    for a in areas:
        price = prices.get(a["symbol"])
        if price is None or not float(a["low"]) <= price <= float(a["high"]):
            continue
        out.append(Card(f"area_entry:{a['id']}", "area_entry", f"{a['symbol']}: the price is inside your watched {a['side']} area",
                        f"{a['symbol']} at {_n(price)} is inside your watched {_area_words(a)}.", a["symbol"], True))
    return out


def rejection_card(a: dict[str, Any], commitment: dict[str, Any] | None) -> Card | None:
    """The reading's commitment candle at his watched area (ATP's level gate), once per candle."""
    if not commitment or not commitment.get("bar_end"):
        return None
    form = str(commitment.get("form") or "commitment")
    return Card(f"area_rejection:{a['id']}:{commitment['bar_end']}", "area_rejection",
                f"{a['symbol']}: a commitment candle at your watched {a['side']} area",
                f"{a['symbol']} ({a['tf']}): a commitment candle ({form}) closed at your watched {_area_words(a)}.", a["symbol"], False)


def strike_points_cards(holdings: Sequence[Any], spots: dict[str, float], points: float) -> list[Card]:
    """His short options whose underlying is within `points` points of the strike (or past it)."""
    out: list[Card] = []
    for h in holdings:
        if h.side != "SELL" or h.right not in ("CE", "PE") or not h.strike or h.underlying not in spots:
            continue
        spot, strike = spots[h.underlying], float(h.strike)
        gap = strike - spot if h.right == "CE" else spot - strike          # room before the price reaches the strike
        if gap > points:
            continue
        where = f"{_n(gap)} points from the strike" if gap > 0 else f"past the strike by {_n(-gap)} points"
        out.append(Card(f"strike_points:{h.trade_id}", "strike_points", f"{h.label}: the price is within your points of the short strike",
                        f"{h.label}: {h.underlying} at {_n(spot)} is {where} {_n(strike)} (your level: {_n(points)} points).",
                        h.underlying, True, h.trade_id, "position", h.trade_id))
    return out


def iv_spike_cards(holdings: Sequence[Any], slots: dict[str, list[Any]], iv_points: float) -> list[Card]:
    """The Copilot watch's own IV check (`chain_cards`, unchanged; IV part only) as the workspace's IV spike card."""
    cards = watch.chain_cards(holdings, slots, {"greeks_jump": True, "iv_jump_points": iv_points}, None)
    return [Card(c.key.replace("greeks_jump:", "iv_spike:", 1), "iv_spike", c.title.split(": the ")[0] + ": IV moved at your strike",
                 c.message, c.symbol, c.holds, c.trade_id, c.ask_kind, c.ask_id) for c in cards if c.kind == "greeks_jump"]


def margin_card(used: float, total: float, pct: float, broker: str) -> Card | None:
    """His broker's margin in use at or above his %."""
    if total <= 0 or used < 0:
        return None
    share = used / total * 100.0
    if share < pct:
        return None
    return Card("margin_use", "margin_use", "Margin in use is at your level",
                f"Margin in use {_n(share, 1)} % ({_n(used, 0)} of {_n(total, 0)}, {broker}); your level {_n(pct, 1)} %.", "", True)


def cut_off_card(now_ist: datetime, square_off: str | None, minutes: int, trading_day: bool) -> Card | None:
    """His reminder: from his N minutes before his own square-off time until that time, once per trading day."""
    if not square_off or not trading_day:
        return None
    try:
        hh, mm = (int(x) for x in square_off.split(":"))
    except ValueError:
        return None
    at = now_ist.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if not at - timedelta(minutes=minutes) <= now_ist < at:
        return None
    left = int((at - now_ist).total_seconds() // 60)
    return Card(f"cut_off_reminder:{now_ist.date().isoformat()}", "cut_off_reminder", f"Your square-off time {square_off} is near",
                f"Your square-off time is {square_off}: {left} min left (your reminder: {minutes} min before).", "", False)


# --- reading his own rows -----------------------------------------------------------------------------------------------

async def _prices(session: AsyncSession, user: User, wanted: Sequence[tuple[str, str]]) -> dict[str, float]:
    """The last price of each (symbol, exchange) through his own price lookup; a symbol without one is left out."""
    from app.ai.worker_bridge import price_lookup_for_user
    lookup = await price_lookup_for_user(session, user)
    out: dict[str, float] = {}
    if lookup is None:
        return out
    for symbol, exchange in dict.fromkeys(wanted):
        try:
            value = float(await lookup(symbol, exchange))
        except Exception as exc:  # noqa: BLE001 - no price: no card (never a guessed price)
            logger.info("Workspace watch: no price for %s (%s)", symbol, type(exc).__name__)
            continue
        if value > 0:
            out[symbol] = value
    return out


async def _commitment(session: AsyncSession, user: User, area: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """The reading's commitment candle at this one area on its own chart, from closed bars after `since` (read_chart's
    own pieces, unchanged)."""
    from app.charts import price_action_layer as pal
    from app.charts import read_chart as rc
    from app.market_lake import own_bars
    since = datetime.fromisoformat(str(area["since"])) if area.get("since") else None
    if since is None or area["tf"] not in pal.DEFAULT_DAYS:
        return None
    key = f"{area.get('exchange') or 'NSE'}:{area['symbol']}"
    private = await own_bars.scope_for(session, user, key)
    try:
        frame, _source = await pal.load_frame(session, key, area["tf"], now - timedelta(days=pal.DEFAULT_DAYS[area["tf"]]), now, private=private)
    except pal.WindowTooLarge:
        return None
    frame = rc.closed_only(frame, now)
    if not len(frame):
        return None
    last_end = pd.Timestamp(frame["bar_end"].iloc[-1]).to_pydatetime()
    ctx = await rc._context(session, key, last_end if last_end.tzinfo else last_end.replace(tzinfo=UTC))  # noqa: SLF001 - the reading's own
    band = {"role": SIDE_ROLE[area["side"]], "low": float(area["low"]), "high": float(area["high"]), "key": area["id"],
            "low_text": _n(float(area["low"])), "high_text": _n(float(area["high"]))}
    return rc.commitment_part(frame, [band], ctx, since)


async def _margin(session: AsyncSession, user: User) -> tuple[float, float, str] | None:
    """(in use, his margin, broker name) from his OWN broker session's margin read; None without one or an answer."""
    from app.paper.fno_basket import own_broker
    try:
        broker = await own_broker(session, user)
        if broker is None:
            return None
        info = await broker.get_margins()
    except Exception as exc:  # noqa: BLE001 - no answer: no card
        logger.info("Workspace watch: margin not read (%s)", type(exc).__name__)
        return None
    used = float(info.used_margin or 0.0)
    total = float(info.total_margin or 0.0) or used + float(info.available_margin or 0.0)
    return used, total, str(getattr(broker, "name", "your broker"))


async def _square_off(session: AsyncSession, user: User) -> str | None:
    from app.me import trade_settings
    value = (await trade_settings.saved_values(user, session)).get("exits.square_off_time")
    return str(value) if isinstance(value, str) and value else None


async def evaluate(session: AsyncSession, user: User, choice: dict[str, Any], now: datetime) -> list[Card]:
    """Every card that holds now for this user (his own rows and his own choices only)."""
    cards: list[Card] = []
    areas = list(choice.get("areas") or [])
    if choice.get("area_entry") and areas:
        prices = await _prices(session, user, [(a["symbol"], a.get("exchange") or "NSE") for a in areas])
        cards += area_entry_cards(areas, prices)
    if choice.get("area_rejection"):
        for a in areas:
            card = rejection_card(a, await _commitment(session, user, a, now))
            if card is not None:
                cards.append(card)
    if (choice.get("strike_points") and choice.get("strike_points_n") is not None) or \
            (choice.get("iv_spike") and choice.get("iv_spike_points") is not None):
        from app.paper.settlement_watch import _spots, holdings_for
        holdings = [h for h in await holdings_for(session, user) if h.right in ("CE", "PE")]
        if holdings and choice.get("strike_points") and choice.get("strike_points_n") is not None:
            spots = await _spots(session, user, sorted({h.underlying for h in holdings if h.side == "SELL"}))
            cards += strike_points_cards(holdings, spots, float(choice["strike_points_n"]))
        if holdings and choice.get("iv_spike") and choice.get("iv_spike_points") is not None:
            from app.market_data.calendar import to_ist
            from app.option_chain import snapshots
            slots = await snapshots.day_chains_many(session, sorted({h.underlying for h in holdings}), to_ist(now).date(),
                                                    tenant_id=int(user.tenant_id))
            cards += iv_spike_cards(holdings, slots, float(choice["iv_spike_points"]))
    if choice.get("margin_use") and choice.get("margin_use_pct") is not None:
        read = await _margin(session, user)
        card = margin_card(read[0], read[1], float(choice["margin_use_pct"]), read[2]) if read else None
        if card is not None:
            cards.append(card)
    if choice.get("cut_off_reminder") and choice.get("cut_off_minutes") is not None:
        from app.market_data.calendar import is_trading_day, load_holidays, to_ist
        ist = to_ist(now)
        card = cut_off_card(ist, await _square_off(session, user), int(choice["cut_off_minutes"]),
                            is_trading_day(ist.date(), await load_holidays(session)))
        if card is not None:
            cards.append(card)
    return [c for c in cards if watch._ok(c)]  # noqa: SLF001 - the Copilot watch's own word guard


# --- "Open here", delivery and the worker's run ---------------------------------------------------------------------------

def open_path(symbol: str | None) -> str:
    """Where "Open here" opens: the Copilot's chart workspace (in place in the app; the address for Telegram)."""
    from urllib.parse import quote
    return f"/copilot/read?symbol={quote(symbol)}" if symbol else "/copilot/read"


def metadata(card: Card) -> str:
    out = json.loads(watch.metadata(card))
    out["open"] = {"symbol": card.symbol} if card.symbol else {}
    return json.dumps(out, sort_keys=True)


def open_of(metadata_json: str | None) -> dict[str, Any] | None:
    """The "Open here" target of a stored card: the symbol only (or the workspace with none); None for other cards."""
    try:
        raw = json.loads(metadata_json or "null")
    except ValueError:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("open"), dict) or raw.get("watch") not in KINDS:
        return None
    symbol = raw["open"].get("symbol")
    return {"symbol": symbol[:30]} if isinstance(symbol, str) and symbol else {}


def telegram_html(card: Card) -> str:
    from app.core import config
    base = config.PUBLIC_BASE_URL
    link = f'\n<a href="{html.escape(base + open_path(card.symbol or None))}">Open here</a>' if base else ""
    return f"<b>{html.escape(TITLE)}: {html.escape(card.title)}</b>\n{html.escape(card.message)}{link}\n<i>Information from your own records.</i>"


async def flags_on(session: AsyncSession, tenant_id: int, memo: dict[int, bool]) -> bool:
    if tenant_id not in memo:
        from app.platform.controls import flag_enabled
        memo[tenant_id] = await flag_enabled(session, UI_V2, tenant_id) and await flag_enabled(session, FLAG, tenant_id)
    return memo[tenant_id]


async def run_due(session: AsyncSession, now: datetime | None = None, client: Any = None) -> int:
    """Raises each opted-in user's new watch cards (the trading worker, with the Copilot watch). Returns how many."""
    from app.me.prefs import stored_value
    now = now or datetime.now(UTC)
    rows = [(r.tenant_id, r.user_id, r.value_json) for r in await session.scalars(
        select(UserPrefsRecord).where(UserPrefsRecord.namespace == PREFS).order_by(UserPrefsRecord.id))]
    memo: dict[int, bool] = {}
    raised = 0
    for tenant_id, user_id, value_json in rows:
        choice = stored_value(PREFS, value_json)
        if not any(choice.get(k) for k in KINDS) or not await flags_on(session, tenant_id, memo):
            continue
        user = await session.get(User, user_id)
        if user is None or not user.is_active or user.tenant_id != tenant_id:
            continue
        try:
            raised += await _run_user(session, user, choice, now, client)
        except Exception as exc:  # noqa: BLE001 - one user's cards never stop the others; tried again on the next run
            logger.warning("Workspace watch for user %s failed: %s", user_id, type(exc).__name__)
            await session.rollback()
    return raised


async def _run_user(session: AsyncSession, user: User, choice: dict[str, Any], now: datetime, client: Any) -> int:
    from app.core.enums import NotificationSeverity, NotificationType
    from app.notifications.service import notify
    tenant_id, user_id = int(user.tenant_id), int(user.id)
    _, marks = await watch.read_marks(session, user, MARKS)
    cards = await evaluate(session, user, choice, now)
    active = set(marks.get("active") or [])
    seen: list[str] = [str(k) for k in (marks.get("seen") or [])]
    holding = {c.key for c in cards if c.holds}
    fresh = [c for c in cards if (c.key not in active if c.holds else c.key not in seen)][:watch.MAX_CARDS_PER_RUN]
    target = None
    if fresh and choice.get("telegram"):
        from app.rhythm.deliver import destination
        target = await destination(session, user)
    for card in fresh:
        await notify(session, tenant_id, NotificationType.COPILOT_WATCH, title=card.title, message=card.message,
                     severity=NotificationSeverity.INFO, user_id=user_id, related_trade_id=card.trade_id,
                     metadata_json=metadata(card), channels=False)
        if not card.holds:
            seen.append(card.key)
        if target is not None:
            try:
                from app.alerts.channels import decrypt_config
                from app.alerts.dispatcher import send_telegram
                await send_telegram(decrypt_config(target[0]), telegram_html(card), client, chat_id=target[1])  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001 - the card stays in the app; Telegram is best effort
                logger.info("Workspace watch Telegram for user %s not sent: %s", user_id, type(exc).__name__)
    sent = {c.key for c in fresh}
    new_active = sorted((active & holding) | (holding & sent))   # a cleared condition is re-armed; unsent ones wait
    row, _ = await watch.read_marks(session, user, MARKS)
    await watch.write_marks(session, user, row, {"active": new_active, "seen": seen[-watch.MAX_SEEN:]}, now, MARKS)
    return len(fresh)


# --- his settings (the workspace's watch panel) ---------------------------------------------------------------------------

async def _on(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for flag in (UI_V2, FLAG):
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": flag})


async def _settings(session: AsyncSession, user: User) -> dict[str, Any]:
    from app.alerts import platform_bot
    from app.me import prefs as prefs_module
    from app.rhythm.deliver import destination
    value, version = await prefs_module.read_one(PREFS, user, session)
    channel = await platform_bot.telegram_channel(session, user.tenant_id)
    return {"prefs": value, "version": version, "square_off_time": await _square_off(session, user),
            "telegram": {"connected": bool(channel is not None and channel.enabled), "reaches_you": await destination(session, user) is not None},
            "blocks": False}


def stamp_areas(body: dict[str, Any], before: Sequence[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """`since` is the server's: an area he already watched keeps its own, a new one starts now (whatever the page sent)."""
    known = {a.get("id"): a.get("since") for a in before if isinstance(a, dict)}
    areas = body.get("areas")
    if isinstance(areas, list):
        for a in areas:
            if isinstance(a, dict):
                a["since"] = known.get(a.get("id")) or now.isoformat()
    return body


@router.get("/watch")
async def get_watch(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """His watch-card choices (every type OFF, every number Not set until he saves one), his own square-off time (the
    reminder needs it) and whether Telegram reaches his own chat."""
    await _on(session, user)
    return await _settings(session, user)


@router.put("/watch")
async def put_watch(request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Replaces his choices (the namespace's schema: a type on without his number is refused with 422). Read-only towards
    trading: nothing here places, changes or blocks an order."""
    await _on(session, user)
    from app.me import prefs as prefs_module
    raw = await prefs_module.read_body(request)
    try:
        body = json.loads(raw or b"null")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Body must be JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Body must be an object")
    before, _version = await prefs_module.read_one(PREFS, user, session)
    body = stamp_areas(body, before.get("areas") or [], datetime.now(UTC))
    await prefs_module.save(PREFS, json.dumps(body).encode(), None, user, session)
    return await _settings(session, user)


__all__ = ["router", "FLAG", "PREFS", "KINDS", "area_entry_cards", "rejection_card", "strike_points_cards", "iv_spike_cards",
           "margin_card", "cut_off_card", "evaluate", "run_due", "open_of", "open_path", "metadata", "stamp_areas"]
