"""M76 MP-2: the pre-trade check service behind the ONE check card (docs/design/M76_REVIEW.md sections 8-9).

Rules (owner order M76 + amendment 1-5): ATP WARNS, never blocks or decides. Every finding is ONE line of facts with
numbers, compared only with a plain fact or a value the trader saved himself (none saved = no line). A finding never
disables Place, never touches an exit, never places or changes an order. Each type has its own switch (Settings › Trade
settings › Warnings, app/paper/pretrade_warnings.py); a type he accepted today for this contract is not shown again today.

Time budget: every check runs at once, each in its own task (and its own database session); the answer comes after at
most `BUDGET_S` (300 ms) with the findings that are ready and the ids still `pending`. A pending check keeps running; its
result is kept for a minute under the draft's hash, and one follow-up call (`only` = those ids) collects it. The ticket
never waits for any of this: Place goes ahead, the card fills in after.

Checks here (server facts): duplicate_order, limit_far_from_ltp, market_state, mis_cutoff. The ticket's own preview
already carries expiry_day, fo_ban, price_source and own_limits (`warning_ids` beside its `warnings`), and the browser
holds margin_short, event and liquidity_limits (the figures on its screen); the card folds all of them in, by the same
switches.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time as clock
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import func, select

from app.db.models import PaperRestingOrderRecord, TradeRecord, User
from app.paper.pretrade_warnings import IST, WarningSettings

BUDGET_S = 0.3
LATE_TTL_S = 60.0
MISTAKES = ("duplicate_order", "limit_far_from_ltp", "margin_short")


@dataclass(frozen=True)
class Draft:
    underlying: str
    expiry: date
    strike: float | None
    right: str
    side: str
    lots: int
    order_type: str | None
    product: str | None
    price: float | None
    ltp: float | None

    def digest(self, user_id: int) -> str:
        raw = json.dumps([user_id, self.underlying, self.expiry.isoformat(), self.strike, self.right, self.side, self.lots,
                          self.order_type, self.product, self.price, self.ltp])
        return hashlib.sha256(raw.encode()).hexdigest()[:24]


def finding(wid: str, text: str, **facts: Any) -> dict[str, Any]:
    return {"id": wid, "severity": "mistake" if wid in MISTAKES else "fact", "text": text, "facts": facts}


SessionFactory = Callable[[], Any]          # an async context manager yielding an AsyncSession
Check = Callable[[SessionFactory, User, Draft, WarningSettings, datetime], Coroutine[Any, Any, dict[str, Any] | None]]


def _num(v: float) -> str:
    return f"{v:,.2f}"


def _side_word(side: str) -> str:
    return "buy" if side == "BUY" else "sell"


def _lots(n: float) -> str:
    n = round(n, 2)
    return f"{n:g} lot{'' if n == 1 else 's'}"


async def check_duplicate(factory: SessionFactory, user: User, d: Draft, s: WarningSettings, now: datetime) -> dict[str, Any] | None:
    """His own open position or waiting entry on the same contract and side (fact)."""
    from app.paper import fno_ticket
    async with factory() as session:
        try:
            contract = await fno_ticket.find_contract(session, d.underlying, d.expiry, d.strike, d.right)
        except fno_ticket.ContractNotFound:
            return None
        symbol = contract.tradingsymbol.upper()
        direction = "LONG" if d.side == "BUY" else "SHORT"
        held = (await session.execute(select(TradeRecord.quantity).where(
            TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == "PAPER",
            TradeRecord.exit_time.is_(None), func.upper(TradeRecord.symbol) == symbol, TradeRecord.direction == direction))).scalars().all()
        waiting = (await session.execute(select(PaperRestingOrderRecord.quantity).where(
            PaperRestingOrderRecord.tenant_id == user.tenant_id, PaperRestingOrderRecord.user_id == user.id,
            PaperRestingOrderRecord.purpose == "ENTRY", PaperRestingOrderRecord.status == "RESTING",
            func.upper(PaperRestingOrderRecord.symbol) == symbol, PaperRestingOrderRecord.side == d.side))).scalars().all()
    lot = contract.lot_size or 1
    parts = []
    if held:
        parts.append(f"Open: {contract.label} {_side_word(d.side)} {_lots(sum(held) / lot)}")
    if waiting:
        parts.append(f"Waiting: {contract.label} {_side_word(d.side)} {_lots(sum(waiting) / lot)}")
    if not parts:
        return None
    return finding("duplicate_order", f"{' · '.join(parts)} · this order: {_side_word(d.side)} {_lots(d.lots)}",
                   open_lots=sum(held) / lot, waiting_lots=sum(waiting) / lot, order_lots=d.lots)


async def check_limit_far(factory: SessionFactory, user: User, d: Draft, s: WarningSettings, now: datetime) -> dict[str, Any] | None:
    """A LIMIT price further from the last price than HIS own band. No band saved: no line."""
    if s.collar_pct is None or d.order_type != "LIMIT" or not d.price or not d.ltp or d.ltp <= 0:
        return None
    away = abs(d.price - d.ltp) / d.ltp * 100
    if away <= s.collar_pct:
        return None
    return finding("limit_far_from_ltp", f"Limit {_num(d.price)} · LTP {_num(d.ltp)} · {away:.1f}% away · your band {s.collar_pct:g}%",
                   limit=d.price, ltp=d.ltp, away_pct=round(away, 2), band_pct=s.collar_pct)


async def check_market_state(factory: SessionFactory, user: User, d: Draft, s: WarningSettings, now: datetime) -> dict[str, Any] | None:
    """The exchange's session now (fact, from the NSE calendar)."""
    from app.market_data.strip_routes import _holidays, market_state
    async with factory() as session:
        state = market_state(now, await _holidays(session, now))
    if state["phase"] == "open":
        return None
    if state["phase"] == "pre-open":
        return finding("market_state", f"Market: pre-open (09:00-{state['opens_at']})", phase="pre-open")
    reason = {"holiday": f"holiday{': ' + state['holiday'] if state.get('holiday') else ''}", "weekend": "weekend",
              "before-open": "before the open", "after-close": "after the close"}.get(state.get("reason") or "", "closed")
    nxt = state.get("next_open")
    when = ""
    if nxt:
        opens = datetime.fromisoformat(nxt).astimezone(IST)
        when = f" · opens {opens:%a %d %b %H:%M}"
    return finding("market_state", f"Market: closed ({reason}){when}", phase="closed", reason=state.get("reason"))


async def check_mis_cutoff(factory: SessionFactory, user: User, d: Draft, s: WarningSettings, now: datetime) -> dict[str, Any] | None:
    """An MIS order near or after HIS own square-off time. Either his time or his minutes Not set: no line."""
    if d.product != "MIS" or s.square_off_time is None or s.mis_minutes is None:
        return None
    hh, mm = (int(x) for x in s.square_off_time.split(":"))
    ist = now.astimezone(IST)
    cut = datetime.combine(ist.date(), time(hh, mm), tzinfo=IST)
    left = (cut - ist).total_seconds() / 60
    if left > s.mis_minutes:
        return None
    if left < 0:
        return finding("mis_cutoff", f"MIS · your square-off {s.square_off_time} has passed", minutes_left=round(left))
    return finding("mis_cutoff", f"MIS · your square-off {s.square_off_time} · {int(left)} min left", minutes_left=int(left))


CHECKS: dict[str, Check] = {
    "duplicate_order": check_duplicate,
    "limit_far_from_ltp": check_limit_far,
    "market_state": check_market_state,
    "mis_cutoff": check_mis_cutoff,
}

# Results of checks that ran past the budget, by (user id, draft hash): {id: finding | None}, and when they were kept.
_LATE: dict[tuple[int, str], tuple[float, dict[str, dict[str, Any] | None], dict[str, asyncio.Task[Any]]]] = {}


def _sweep() -> None:
    cutoff = clock.monotonic() - LATE_TTL_S
    for key in [k for k, (at, _r, _t) in _LATE.items() if at < cutoff]:
        _LATE.pop(key, None)


async def run(factory: SessionFactory, user: User, d: Draft, s: WarningSettings, *, accepted: set[str],
              only: list[str] | None = None, now: datetime | None = None, budget_s: float = BUDGET_S) -> dict[str, Any]:
    """The ready findings within the budget, the ids still pending, and how long it took (ms)."""
    started = clock.perf_counter()
    now = now or datetime.now(UTC)
    _sweep()
    key = (user.id, d.digest(user.id))
    wanted = [w for w in (only or list(CHECKS)) if w in CHECKS and s.enabled(w) and w not in accepted]
    results: dict[str, dict[str, Any] | None] = {}
    tasks: dict[str, asyncio.Task[Any]] = {}
    kept = _LATE.get(key)
    for w in wanted:
        if kept and w in kept[1]:
            results[w] = kept[1][w]
        elif kept and w in kept[2]:
            tasks[w] = kept[2][w]
        else:
            tasks[w] = asyncio.create_task(CHECKS[w](factory, user, d, s, now))
    if tasks:
        await asyncio.wait(list(tasks.values()), timeout=budget_s)
    pending: list[str] = []
    for w, task in tasks.items():
        if not task.done():
            pending.append(w)
            continue
        try:
            results[w] = task.result()
        except Exception:  # noqa: BLE001 - a check that fails says nothing; it never blocks or invents a line
            results[w] = None
    if pending:
        store = _LATE.setdefault(key, (clock.monotonic(), {}, {}))
        for w in pending:
            store[2][w] = tasks[w]

            def keep(t: asyncio.Task[Any], w: str = w) -> None:
                try:
                    store[1][w] = None if t.cancelled() or t.exception() else t.result()
                except Exception:  # noqa: BLE001
                    store[1][w] = None
            tasks[w].add_done_callback(keep)
    findings = [f for f in (results.get(w) for w in wanted) if f]
    findings.sort(key=lambda f: (0 if f["severity"] == "mistake" else 1, list(CHECKS).index(f["id"])))
    return {"findings": findings, "pending": pending, "ms": round((clock.perf_counter() - started) * 1000, 1)}
