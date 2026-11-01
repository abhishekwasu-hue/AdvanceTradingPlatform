"""M72 PR-7 (CP-7, findings F13 / F14): the Copilot watch - opt-in information cards about the user's OWN data.

Each type is OFF until the user turns it on in Settings > Alerts (`user_prefs` namespace `copilot_watch`), and every type
with a number uses HIS number (Not set = the type cannot be turned on; ATP chooses no threshold, M68):

* `near_levels`      - an open position (his own, PAPER or LIVE alike) whose mark has his % or less of the room left
                       between the entry and his OWN invalidation level or his own profit level (the levels he set on it);
* `strike_distance`  - a short option whose underlying is within his % of the strike (or past it);
* `greeks_jump`      - at his option's strike, IV moved by his IV points, or delta by his amount, between the two latest
                       stored chain readings of his organisation (delta from the platform's Black-Scholes with HIS own
                       risk-free rate, Trade settings > Options; Not set = only the IV part);
* `oi_shift`         - open interest at his strike changed by his % between those two readings;
* `watchlist_signal` - a new setup from his own strategies or scans on a symbol in his own watchlists, in structure words
                       without a number (the daily rhythm's setup line).

Rules (F14, ADR-0004, ADR-0006): information only. No proposal row, no order, nothing closed or blocked; the card's only
action is "Ask why" (the Copilot opens beside the page with that position or signal as context). Every number on a card
is code arithmetic on his own rows and the stored readings, never a model's. Wording: structure words (no buy / sell /
target / stop wording, `rhythm.words.clean`).

Delivery: a notification addressed to him alone (`COPILOT_WATCH`; the feed never shows it to another member) that stays
off the organisation's shared channels, and - when he turns `telegram` on - his own Telegram chat (`rhythm.deliver
.destination`). Phones get nothing (no phone kind, MA-7). Dedupe without a table: an internal `user_prefs` row
(`copilot_watch_marks`, not a prefs namespace) keeps the conditions that hold (a card once, again only after it clears)
and the one-off events already sent. The personal brief and wrap stay the daily rhythm's (app/rhythm, his positions and
lists), not app/ai/briefing.py (F13).
"""
from __future__ import annotations

import html
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TradeRecord, User, UserPrefsRecord
from app.rhythm import words

logger = logging.getLogger(__name__)

FLAG = "copilot_watch"
PREFS = "copilot_watch"
MARKS = "copilot_watch_marks"        # internal: not in app/me/prefs.py NAMESPACES, so no route reads or writes it
KINDS = ("near_levels", "strike_distance", "greeks_jump", "oi_shift", "watchlist_signal")
MAX_SEEN = 300
MAX_CARDS_PER_RUN = 10               # per user and run: a burst never floods his feed (the rest follow on the next run)
TITLE = "Copilot watch"


@dataclass(frozen=True)
class Card:
    key: str                         # the dedupe key
    kind: str
    title: str
    message: str
    symbol: str
    holds: bool                      # True: a condition (sent once while it holds); False: a one-off event
    trade_id: int | None = None
    ask_kind: str | None = None   # "position" / "signal": the object "Ask why" opens with
    ask_id: int | None = None


def _n(value: float, nd: int = 2) -> str:
    text = f"{value:,.{nd}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _side(direction: str) -> int:
    return -1 if str(direction).upper() in ("SHORT", "SELL") else 1


def _ok(card: Card) -> bool:
    return words.clean(card.title) is not None and words.clean(card.message) is not None


# --- the five checks (pure: rows in, cards out) --------------------------------------------------------------------------

def near_levels_cards(trades: Sequence[TradeRecord], room_left_pct: float) -> list[Card]:
    """Open positions whose mark has `room_left_pct` % or less of the room to his own levels left."""
    out: list[Card] = []
    for t in trades:
        if t.mark_price is None:
            continue
        side, entry, mark = _side(t.direction), float(t.entry_price), float(t.mark_price)
        word = "short" if side < 0 else "long"
        for which, level, label in (("invalidation", t.stop_loss, "your own invalidation level"),
                                    ("profit", t.target1, "your own profit level")):
            if not level:
                continue
            level = float(level)
            room = (entry - level) * side if which == "invalidation" else (level - entry) * side
            if room <= 0:
                continue
            left = (mark - level) * side if which == "invalidation" else (level - mark) * side
            left_pct = max(0.0, left / room * 100.0)
            if left_pct > room_left_pct:
                continue
            where = f"{_n(left_pct, 1)} % of the room to {label} is left" if left > 0 else f"at or past {label}"
            out.append(Card(f"near_levels:{t.id}:{which}", "near_levels", f"{t.symbol} ({word}) near {label}",
                            f"{t.symbol} ({word}): mark {_n(mark)}, entry {_n(entry)}, {label} {_n(level)} - {where}.",
                            t.symbol, True, t.id, "position", t.id))
    return out


def strike_distance_cards(holdings: Sequence[Any], spots: dict[str, float], distance_pct: float) -> list[Card]:
    """Short options whose underlying is within `distance_pct` % of the strike (or past it)."""
    out: list[Card] = []
    for h in holdings:
        if h.side != "SELL" or h.right not in ("CE", "PE") or not h.strike or h.underlying not in spots:
            continue
        spot, strike = spots[h.underlying], float(h.strike)
        gap = strike - spot if h.right == "CE" else spot - strike          # room before the price reaches the strike
        gap_pct = gap / strike * 100.0
        if gap_pct > distance_pct:
            continue
        where = (f"{_n(gap)} points ({_n(gap_pct, 2)} %) from the strike" if gap > 0
                 else f"past the strike by {_n(-gap)} points")
        out.append(Card(f"strike_distance:{h.trade_id}", "strike_distance", f"{h.label}: the price is near your short strike",
                        f"{h.label}: {h.underlying} at {_n(spot)} is {where} {_n(strike)}.", h.underlying, True,
                        h.trade_id, "position", h.trade_id))
    return out


def _iv_pct(value: float | None) -> float | None:
    from app.option_chain.analyses import PERCENT_CUTOFF
    if value is None or value <= 0:
        return None
    return float(value) * 100.0 if value < PERCENT_CUTOFF else float(value)


def _delta(right: str, spot: float | None, strike: float, iv_pct: float | None, expiry: Any, at: datetime,
           rate_pct: float | None) -> float | None:
    if rate_pct is None or iv_pct is None or not spot or spot <= 0:
        return None
    from app.option_chain.greeks import BSInputs, black_scholes, time_to_expiry_years
    from app.option_chain.models import OptionType
    years = time_to_expiry_years(expiry, at)
    if years <= 0:
        return None
    try:
        return black_scholes(BSInputs(float(spot), strike, years, rate_pct / 100.0, iv_pct / 100.0,
                                      OptionType.CALL if right == "CE" else OptionType.PUT)).delta
    except ValueError:
        return None


def _row(slot: Any, strike: float) -> Any:
    return next((r for r in slot.chain.rows if abs(float(r.strike) - strike) < 1e-6), None)


def chain_cards(holdings: Sequence[Any], slots: dict[str, list[Any]], choice: dict[str, Any], rate_pct: float | None) -> list[Card]:
    """IV / delta jumps and OI shifts at his options' strikes between the two latest stored readings of the same expiry."""
    out: list[Card] = []
    for h in holdings:
        if h.right not in ("CE", "PE") or not h.strike:
            continue
        same = [s for s in slots.get(h.underlying, []) if s.chain.expiry == h.expiry.isoformat()]
        if len(same) < 2:
            continue
        prev, last = same[-2], same[-1]
        a, b = _row(prev, float(h.strike)), _row(last, float(h.strike))
        if a is None or b is None:
            continue
        stamp = last.slot_start.isoformat()
        side = "call" if h.right == "CE" else "put"
        if choice.get("greeks_jump"):
            iv_a = _iv_pct(a.call_iv if h.right == "CE" else a.put_iv)
            iv_b = _iv_pct(b.call_iv if h.right == "CE" else b.put_iv)
            parts: list[str] = []
            points = choice.get("iv_jump_points")
            if points is not None and iv_a is not None and iv_b is not None and abs(iv_b - iv_a) >= points:
                parts.append(f"IV {_n(iv_a)} -> {_n(iv_b)} ({_n(iv_b - iv_a)} IV points)")
            jump = choice.get("delta_jump")
            d_a = _delta(h.right, prev.chain.underlying_ltp, float(h.strike), iv_a, h.expiry, prev.captured_at, rate_pct)
            d_b = _delta(h.right, last.chain.underlying_ltp, float(h.strike), iv_b, h.expiry, last.captured_at, rate_pct)
            if jump is not None and d_a is not None and d_b is not None and abs(d_b - d_a) >= jump:
                parts.append(f"delta {_n(d_a, 3)} -> {_n(d_b, 3)} ({_n(d_b - d_a, 3)})")
            if parts:
                out.append(Card(f"greeks_jump:{h.trade_id}:{stamp}", "greeks_jump", f"{h.label}: the {side}'s Greeks moved",
                                f"{h.label}: " + ", ".join(parts) + " between the last two chain readings.", h.underlying, False,
                                h.trade_id, "position", h.trade_id))
        pct = choice.get("oi_shift_pct")
        if choice.get("oi_shift") and pct is not None:
            oi_a = a.call_oi if h.right == "CE" else a.put_oi
            oi_b = b.call_oi if h.right == "CE" else b.put_oi
            if oi_a and oi_b is not None and oi_a > 0:
                change = (float(oi_b) - float(oi_a)) / float(oi_a) * 100.0
                if abs(change) >= pct:
                    out.append(Card(f"oi_shift:{h.trade_id}:{stamp}", "oi_shift", f"{h.label}: open interest moved at your strike",
                                    f"{h.label}: {side} open interest {_n(float(oi_a), 0)} -> {_n(float(oi_b), 0)} ({_n(change, 1)} %) "
                                    "between the last two chain readings.", h.underlying, False, h.trade_id, "position", h.trade_id))
    return out


def watchlist_cards(items: Sequence[dict[str, Any]], symbols: Sequence[str], since: datetime) -> list[Card]:
    """New setups since `since` on a symbol in his watchlists, in the rhythm's numberless setup words."""
    from app.rhythm.build import _setup_line
    wanted = {s.strip().upper() for s in symbols if s and s.strip()}
    out: list[Card] = []
    for item in items:
        sym = str(item.get("symbol") or "").upper()
        at = item.get("at")
        if sym not in wanted or not at or datetime.fromisoformat(str(at)).astimezone(UTC) <= since:
            continue
        line = _setup_line(item)
        if not line:
            continue
        kind, _, num = str(item.get("id") or "").partition(":")
        ask_kind, ask_id = ("signal", int(num)) if kind == "history" and num.isdigit() else (None, None)
        out.append(Card(f"watchlist_signal:{item.get('id')}", "watchlist_signal", f"{sym}: a setup on your watchlist", line, sym,
                        False, None, ask_kind, ask_id))
    return out


# --- reading his own rows ------------------------------------------------------------------------------------------------

async def _rate_pct(session: AsyncSession, user: User) -> float | None:
    from app.me import prefs, trade_settings
    value, _ = await prefs.read_one(trade_settings.NAMESPACE, user, session)
    rate = trade_settings.clean(value.get("values")).get("options.risk_free_rate_pct")
    return float(rate) if isinstance(rate, (int, float)) else None


async def evaluate(session: AsyncSession, user: User, choice: dict[str, Any], now: datetime, since: datetime) -> list[Card]:
    """Every card that holds now for this user (his own rows only; tenant AND user)."""
    cards: list[Card] = []
    if choice.get("near_levels") and choice.get("near_levels_room_left_pct") is not None:
        trades = list(await session.scalars(select(TradeRecord).where(
            TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.exit_time.is_(None))))
        cards += near_levels_cards(trades, float(choice["near_levels_room_left_pct"]))
    chain_on = choice.get("greeks_jump") or choice.get("oi_shift")
    if choice.get("strike_distance") or chain_on:
        from app.paper.settlement_watch import _spots, holdings_for
        holdings = [h for h in await holdings_for(session, user) if h.right in ("CE", "PE")]
        if holdings and choice.get("strike_distance") and choice.get("strike_distance_pct") is not None:
            spots = await _spots(session, user, sorted({h.underlying for h in holdings if h.side == "SELL"}))
            cards += strike_distance_cards(holdings, spots, float(choice["strike_distance_pct"]))
        if holdings and chain_on:
            from app.market_data.calendar import to_ist
            from app.option_chain import snapshots
            slots = await snapshots.day_chains_many(session, sorted({h.underlying for h in holdings}), to_ist(now).date(),
                                                    tenant_id=int(user.tenant_id))
            cards += chain_cards(holdings, slots, choice, await _rate_pct(session, user) if choice.get("delta_jump") else None)
    if choice.get("watchlist_signal"):
        from app.signals_feed import service as feed_service
        from app.watchlists import service as lists
        symbols = [s for w in await lists.list_own(session, int(user.tenant_id), int(user.id)) for s in lists.symbols_of(w)]
        if symbols:
            page = await feed_service.feed(session, user, tab="history", limit=50, now=now)
            cards += watchlist_cards(page.get("items", []), symbols, since)
    return [c for c in cards if _ok(c)]


# --- marks, delivery and the worker's run ----------------------------------------------------------------------------------

async def read_marks(session: AsyncSession, user: Any) -> tuple[UserPrefsRecord | None, dict[str, Any]]:
    row = await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.tenant_id == user.tenant_id, UserPrefsRecord.user_id == user.id,
                                                             UserPrefsRecord.namespace == MARKS))
    try:
        marks = json.loads(row.value_json) if row else {}
    except ValueError:
        marks = {}
    return row, marks if isinstance(marks, dict) else {}


async def write_marks(session: AsyncSession, user: Any, row: UserPrefsRecord | None, marks: dict[str, Any], now: datetime) -> None:
    value = json.dumps(marks, sort_keys=True)
    if row is None:
        session.add(UserPrefsRecord(tenant_id=user.tenant_id, user_id=user.id, namespace=MARKS, value_json=value, version=1, updated_at=now))
    else:
        row.value_json, row.version, row.updated_at = value, row.version + 1, now
    await session.commit()


def metadata(card: Card) -> str:
    ask: dict[str, Any] = {"symbol": card.symbol}
    if card.ask_kind and card.ask_id:
        ask["object"] = {"kind": card.ask_kind, "id": card.ask_id}
    return json.dumps({"watch": card.kind, "ask": ask}, sort_keys=True)


def ask_of(metadata_json: str) -> dict[str, Any] | None:
    """The "Ask why" context of a stored card (symbol, object kind and id; nothing else)."""
    try:
        ask = (json.loads(metadata_json) or {}).get("ask") or {}
    except (ValueError, AttributeError):
        return None
    if not isinstance(ask, dict):
        return None
    out: dict[str, Any] = {}
    if isinstance(ask.get("symbol"), str) and ask["symbol"]:
        out["symbol"] = ask["symbol"][:30]
    obj = ask.get("object")
    if isinstance(obj, dict) and obj.get("kind") in ("position", "signal") and isinstance(obj.get("id"), int) and obj["id"] > 0:
        out["object"] = {"kind": obj["kind"], "id": obj["id"]}
    return out or None


def telegram_html(card: Card) -> str:
    return (f"<b>{html.escape(TITLE)}: {html.escape(card.title)}</b>\n{html.escape(card.message)}\n"
            "<i>Information from your own records. Open the app and tap Ask why for the Copilot's reading.</i>")


async def flags_on(session: AsyncSession, tenant_id: int, memo: dict[int, bool]) -> bool:
    if tenant_id not in memo:
        from app.platform.controls import flag_enabled
        memo[tenant_id] = await flag_enabled(session, "ui_v2", tenant_id) and await flag_enabled(session, FLAG, tenant_id)
    return memo[tenant_id]


async def run_due(session: AsyncSession, now: datetime | None = None, client: Any = None) -> int:
    """Raises each opted-in user's new cards (the trading worker, every few minutes). Returns how many were raised."""
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
        except Exception as exc:  # noqa: BLE001 - one user's watch never stops the others; tried again on the next run
            logger.warning("Copilot watch for user %s failed: %s", user_id, type(exc).__name__)
            await session.rollback()
    return raised


async def _run_user(session: AsyncSession, user: User, choice: dict[str, Any], now: datetime, client: Any) -> int:
    from app.core.enums import NotificationSeverity, NotificationType
    from app.notifications.service import notify
    tenant_id, user_id = int(user.tenant_id), int(user.id)
    _, marks = await read_marks(session, user)
    since_raw = marks.get("since")
    if not since_raw:                                      # the first run only sets the start: no card for older setups
        row, _ = await read_marks(session, user)
        await write_marks(session, user, row, {"since": now.isoformat(), "active": [], "seen": []}, now)
        since = now
    else:
        since = datetime.fromisoformat(str(since_raw))
    cards = await evaluate(session, user, choice, now, since)
    active = set(marks.get("active") or [])
    seen: list[str] = [str(k) for k in (marks.get("seen") or [])]
    holding = {c.key for c in cards if c.holds}
    fresh = [c for c in cards if (c.key not in active if c.holds else c.key not in seen)][:MAX_CARDS_PER_RUN]
    target = None
    if fresh and choice.get("telegram"):
        from app.rhythm.deliver import destination
        target = await destination(session, user)
    raised = 0
    for card in fresh:
        await notify(session, tenant_id, NotificationType.COPILOT_WATCH, title=card.title, message=card.message,
                     severity=NotificationSeverity.INFO, user_id=user_id, related_trade_id=card.trade_id,
                     metadata_json=metadata(card), channels=False)
        raised += 1
        if not card.holds:
            seen.append(card.key)
        if target is not None:
            try:
                from app.alerts.channels import decrypt_config
                from app.alerts.dispatcher import send_telegram
                await send_telegram(decrypt_config(target[0]), telegram_html(card), client, chat_id=target[1])  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001 - the card stays in the app; Telegram is best effort
                logger.info("Copilot watch Telegram for user %s not sent: %s", user_id, type(exc).__name__)
    sent_keys = {c.key for c in fresh}
    # A condition stays marked while it holds (sent now or before); one that cleared is re-armed. Unsent ones wait.
    new_active = sorted((active & holding) | (holding & sent_keys))
    row, _ = await read_marks(session, user)
    await write_marks(session, user, row, {"since": since.isoformat() if since_raw else now.isoformat(), "active": new_active,
                                           "seen": seen[-MAX_SEEN:]}, now)
    return raised


__all__ = ["FLAG", "PREFS", "KINDS", "Card", "near_levels_cards", "strike_distance_cards", "chain_cards", "watchlist_cards",
           "evaluate", "run_due", "ask_of", "metadata"]
