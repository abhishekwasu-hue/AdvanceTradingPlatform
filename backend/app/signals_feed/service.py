"""UI2-3 (UI v2 proposal §7, answered question UI-4): the Signals inbox, read from the tables that already hold signals.

One list of "what should I act on?" across the places a trade idea comes from today, in one normalised row shape:

* **Proposals** - `ai_actions` with `action = ENTRY` (Z3 approval-only entries: Autopilot deployments, scan setups, the
  PAPER ticket, TradingView / API / manual setups): waiting, snoozed, taken, skipped, expired or not placed. Only the
  caller's own (`owner_user_id`) and the organisation-wide ones (no owner) - another member's proposal never appears.
  Approve, skip and snooze stay on the existing `/api/ai/actions/{id}/approve|reject|snooze` (with their step-up and
  owner rules); a row only carries `action_id` to link there.
* **Execution intents** (flag `execution_core`) - the caller's scan setups the core routed without a proposal
  (a notification, refused, expired). An intent that became a proposal is shown once, as that proposal.
* **Scan matches** (flag `screener_v2`) - `alert_events` of the alert rules the caller created.
* **Autopilot fills** - the caller's own positions opened by a deployment, unless the position came from an approved
  proposal (then the proposal row says "taken").
* **Signal generator** - the caller's own `signal_history` rows with a direction (the M6 "Try it on a symbol" tool).

Tabs: `waiting` (proposals waiting for a decision, not snoozed, not expired), `today` (since 00:00 IST) and `history`
(the last HISTORY_DAYS days), newest first, paged by an opaque cursor. Filters: the source kind and one symbol.

Read-only by construction: this module only runs SELECTs - it never commits, never changes a row (an overdue PROPOSED
proposal is *reported* as expired; the worker still expires it) and never reaches an order path (ADR-0006). A source
whose flag is off contributes nothing.
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.db.models import AiActionRecord, AlertEventRecord, AlertRuleRecord, ExecutionIntentRecord, SignalHistoryRecord, TradeRecord, User
from app.market_data.calendar import IST

TABS = ("waiting", "today", "history")
SOURCE_FILTERS = ("all", "autopilot", "scan", "copilot")
HISTORY_DAYS = 30
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
BATCH = 200
# The most rows one request reads from one source: a pathological day stays bounded (the cursor carries on from there).
SCAN_CAP = 2000

ENTRY = "ENTRY"
# The sources in their tie-break order (rows at the same instant) - also each row id's prefix ("action:12").
SOURCES = ("action", "intent", "alert", "trade", "history")
RANK = {name: i for i, name in enumerate(SOURCES)}
# Statuses a row can have. waiting / snoozed: a proposal still open; taken: approved (or an Autopilot fill); skipped:
# rejected; expired: nobody decided in time; not_placed: approved or routed but nothing was placed (with why);
# info: a match or a generated signal - nothing to decide.
STATUSES = ("waiting", "snoozed", "taken", "skipped", "expired", "not_placed", "info")
KIND_LABELS = {"autopilot": "Autopilot", "scan": "Scan", "copilot": "Copilot", "library": "Signal generator"}


class BadCursor(ValueError):
    """The cursor is not one this endpoint issued."""


@dataclass(frozen=True)
class Cursor:
    at: datetime
    rank: int
    num: int


def encode_cursor(c: Cursor) -> str:
    raw = json.dumps({"a": c.at.astimezone(timezone.utc).isoformat(), "r": c.rank, "n": c.num}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(text: str) -> Cursor:
    try:
        data = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode())
        at = datetime.fromisoformat(str(data["a"]))
        rank, num = int(data["r"]), int(data["n"])
    except (binascii.Error, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise BadCursor("Bad cursor") from exc
    if rank not in RANK.values() or num < 0:
        raise BadCursor("Bad cursor")
    return Cursor(_utc(at), rank, num)


def _utc(dt: datetime) -> datetime:
    """SQLite gives the stored UTC instant back without its zone; Postgres with it."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return _utc(dt).isoformat() if dt is not None else None


def today_start(now: datetime) -> datetime:
    """00:00 IST of `now`'s IST date, as UTC."""
    local = _utc(now).astimezone(IST)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _json(text: Optional[str]) -> Dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def _direction(value: Any) -> Optional[str]:
    text = str(value or "").upper()
    return text if text in ("LONG", "SHORT") else None


def _reward_risk(entry: Optional[float], stop: Optional[float], target: Optional[float], given: Any = None) -> Optional[float]:
    stated = _num(given)
    if stated is not None and stated > 0:
        return round(stated, 2)
    if entry is None or stop is None or target is None or entry == stop:
        return None
    return round(abs(target - entry) / abs(entry - stop), 2)


def _reasons(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    return [str(v)[:200] for v in values if isinstance(v, (str, int, float)) and str(v).strip()][:3]


def strategy_name(strategy_id: str) -> str:
    """The inbuilt strategy's display name, else the id as stored (a custom strategy, a manual setup)."""
    try:
        from app.strategy_engine.registry import registry
        return str(registry.get(strategy_id).name)
    except (KeyError, AttributeError):
        return strategy_id


def _item(*, source: str, num: int, at: datetime, symbol: Any, direction: Any, setup: str, entry: Any, stop: Any, target: Any,
          rr: Any, kind: str, name: str, mode: Optional[str], status: str, expires_at: Optional[datetime] = None,
          reasons: Any = None, action_id: Optional[int] = None, snoozed_until: Optional[datetime] = None,
          outcome: Optional[str] = None, exchange: Optional[str] = None) -> Dict[str, Any]:
    e, s, t = _num(entry), _num(stop), _num(target)
    return {
        "id": f"{source}:{num}", "at": _iso(at), "symbol": str(symbol or "").upper(), "exchange": (exchange or "NSE").upper(),
        "direction": _direction(direction), "setup": setup, "entry": e, "stop": s, "target": t, "rr": _reward_risk(e, s, t, rr),
        "source": {"kind": kind, "name": name}, "mode": mode, "status": status, "expires_at": _iso(expires_at),
        "snoozed_until": _iso(snoozed_until), "reasons": _reasons(reasons), "action_id": action_id,
        "outcome": (outcome or None) and str(outcome)[:300],
        "_key": (_utc(at), RANK[source], num),
    }


# --- one converter per source --------------------------------------------------------------------------------------

def action_status(row: AiActionRecord, now: datetime) -> str:
    if row.status == "PROPOSED":
        if _utc(row.expires_at) <= now:
            return "expired"                 # the worker expires it on its next pass; the feed only says so
        if row.snoozed_until is not None and _utc(row.snoozed_until) > now:
            return "snoozed"
        return "waiting"
    return {"APPROVED": "taken", "EXECUTED": "taken", "REJECTED": "skipped", "EXPIRED": "expired", "FAILED": "not_placed"}.get(row.status, "expired")


def action_kind(row: AiActionRecord, evidence: Dict[str, Any]) -> str:
    origin = str(evidence.get("source") or "")
    if row.deployment_id is not None or origin == "deployment":
        return "autopilot"
    if origin == "scan":
        return "scan"
    return "copilot"


def _from_action(row: AiActionRecord, now: datetime) -> Dict[str, Any]:
    from app.execution.approval import SOURCE_LABELS
    ev = _json(row.evidence_json)
    raw = ev.get("signal")
    signal: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    kind = action_kind(row, ev)
    origin = str(ev.get("source") or "")
    name = f"Deployment #{row.deployment_id}" if kind == "autopilot" and row.deployment_id is not None else SOURCE_LABELS.get(origin, "Proposal")
    sid = str(ev.get("strategy_id") or signal.get("strategy_id") or "")
    setup = str(signal.get("strategy_name") or strategy_name(sid) or "Entry")
    return _item(source="action", num=row.id, at=row.created_at, symbol=ev.get("symbol"), direction=ev.get("direction"), setup=setup,
                 entry=ev.get("proposal_price"), stop=ev.get("stop_loss"), target=ev.get("target1"), rr=ev.get("risk_reward"),
                 kind=kind, name=name, mode="PAPER",      # Z3 / BT2: an approved entry proposal places a PAPER order (Phase 0)
                 status=action_status(row, now), expires_at=row.expires_at, reasons=signal.get("reasons"), action_id=row.id,
                 snoozed_until=row.snoozed_until, outcome=row.result or row.decision_note, exchange=ev.get("exchange"))


def _from_intent(row: ExecutionIntentRecord) -> Dict[str, Any]:
    from app.execution.core.intents import SOURCES as INTENT_SOURCES
    signal = _json(row.signal_json)
    status = {"DONE": "info", "QUEUED": "info", "CLAIMED": "info", "REFUSED": "not_placed", "FAILED": "not_placed", "EXPIRED": "expired"}.get(row.status, "info")
    return _item(source="intent", num=row.id, at=row.created_at, symbol=row.symbol, direction=row.direction,
                 setup=str(signal.get("strategy_name") or strategy_name(row.strategy_id)), entry=signal.get("entry"), stop=signal.get("stop_loss"),
                 target=signal.get("target1"), rr=signal.get("risk_reward"), kind="scan", name=INTENT_SOURCES.get(row.source, "Scan setup"),
                 mode="PAPER", status=status, expires_at=row.expires_at, reasons=signal.get("reasons"), outcome=row.reason, exchange=row.exchange)


def _match_values(values: Dict[str, Any]) -> List[str]:
    """Up to three "name: value" lines from a scan match's trigger values (a value may be {value, at})."""
    out: List[str] = []
    for key, value in values.items():
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            out.append(f"{key.replace('_', ' ')}: {round(float(value), 2):g}")
        elif isinstance(value, str) and len(value) <= 60:
            out.append(f"{key.replace('_', ' ')}: {value}")
        if len(out) == 3:
            break
    return out


def _from_alert(event: AlertEventRecord, rule_name: str) -> Dict[str, Any]:
    return _item(source="alert", num=event.id, at=event.created_at, symbol=event.symbol, direction=None, setup=rule_name, entry=None,
                 stop=None, target=None, rr=None, kind="scan", name=rule_name, mode=None, status="info",
                 reasons=_match_values(_json(event.values_json)))


def _from_trade(row: TradeRecord) -> Dict[str, Any]:
    return _item(source="trade", num=row.id, at=row.entry_time, symbol=row.symbol, direction=row.direction, setup=strategy_name(row.strategy_id),
                 entry=row.entry_price, stop=row.stop_loss, target=row.target1, rr=None, kind="autopilot",
                 name=f"Deployment #{row.deployment_id}", mode=row.mode, status="taken", exchange=row.exchange)


def _from_history(row: SignalHistoryRecord) -> Dict[str, Any]:
    reasons: Any
    try:
        reasons = json.loads(row.reasons_json or "[]")
    except ValueError:
        reasons = []
    return _item(source="history", num=row.id, at=row.created_at, symbol=row.symbol, direction=row.direction, setup=strategy_name(row.strategy_id),
                 entry=row.entry, stop=row.stop_loss, target=row.target1, rr=row.risk_reward, kind="library",
                 name=KIND_LABELS["library"], mode=None, status="info", reasons=reasons)


# --- reading -------------------------------------------------------------------------------------------------------

@dataclass
class FeedQuery:
    user: User
    tab: str
    source: str
    symbol: Optional[str]
    now: datetime
    flags: Dict[str, bool] = field(default_factory=dict)

    @property
    def since(self) -> Optional[datetime]:
        if self.tab == "today":
            return today_start(self.now)
        if self.tab == "history":
            return self.now - timedelta(days=HISTORY_DAYS)
        return None                           # waiting: open proposals, bounded by their own expiry

    def keeps(self, item: Dict[str, Any]) -> bool:
        if self.source != "all" and item["source"]["kind"] != self.source:
            return False
        if self.symbol and item["symbol"] != self.symbol:
            return False
        return self.tab != "waiting" or item["status"] == "waiting"


@dataclass
class Source:
    name: str
    query: Select
    at: Any                                   # the time column, newest first
    pk: Any
    convert: Callable[[Any], Dict[str, Any]]


def _after(at: Any, pk: Any, rank: int, c: Cursor) -> Any:
    """Rows that sort after the cursor in (time, source rank, id) descending order."""
    if rank < c.rank:
        return at <= c.at
    if rank == c.rank:
        return or_(at < c.at, and_(at == c.at, pk < c.num))
    return at < c.at


async def _read(session: AsyncSession, q: FeedQuery, src: Source, cursor: Optional[Cursor], need: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Up to `need` kept rows of one source, newest first, after the cursor. Also says whether rows may remain."""
    out: List[Dict[str, Any]] = []
    scanned = 0
    last: Optional[Tuple[datetime, int]] = None
    since = q.since
    while len(out) < need:
        stmt = src.query
        if since is not None:
            stmt = stmt.where(src.at >= since)
        if cursor is not None:
            stmt = stmt.where(_after(src.at, src.pk, RANK[src.name], cursor))
        if last is not None:
            stmt = stmt.where(or_(src.at < last[0], and_(src.at == last[0], src.pk < last[1])))
        rows = list((await session.execute(stmt.order_by(src.at.desc(), src.pk.desc()).limit(BATCH))).all())
        for row in rows:
            item = src.convert(row)
            if q.keeps(item):
                out.append(item)
        scanned += len(rows)
        if len(rows) < BATCH:
            return out, False
        if scanned >= SCAN_CAP:
            return out, True
        tail = src.convert(rows[-1])["_key"]
        last = (tail[0], tail[2])
    return out, True


def _sources(q: FeedQuery) -> List[Source]:
    user, now = q.user, q.now
    actions = select(AiActionRecord).where(
        AiActionRecord.tenant_id == user.tenant_id, AiActionRecord.action == ENTRY,
        or_(AiActionRecord.owner_user_id == user.id, AiActionRecord.owner_user_id.is_(None)))
    if q.tab == "waiting":
        actions = actions.where(AiActionRecord.status == "PROPOSED", AiActionRecord.expires_at > now,
                                or_(AiActionRecord.snoozed_until.is_(None), AiActionRecord.snoozed_until <= now))
    out = [Source("action", actions, AiActionRecord.created_at, AiActionRecord.id, lambda r: _from_action(r[0], now))]
    if q.tab == "waiting":
        return out
    if q.source in ("all", "scan") and q.flags.get("execution_core"):
        intents = select(ExecutionIntentRecord).where(
            ExecutionIntentRecord.tenant_id == user.tenant_id, ExecutionIntentRecord.user_id == user.id, ExecutionIntentRecord.ai_action_id.is_(None))
        if q.symbol:
            intents = intents.where(ExecutionIntentRecord.symbol == q.symbol)
        out.append(Source("intent", intents, ExecutionIntentRecord.created_at, ExecutionIntentRecord.id, lambda r: _from_intent(r[0])))
    if q.source in ("all", "scan") and q.flags.get("screener_v2"):
        alerts = select(AlertEventRecord, AlertRuleRecord.name).join(AlertRuleRecord, AlertRuleRecord.id == AlertEventRecord.rule_id).where(
            AlertEventRecord.tenant_id == user.tenant_id, AlertRuleRecord.tenant_id == user.tenant_id, AlertRuleRecord.created_by == user.id)
        if q.symbol:
            alerts = alerts.where(AlertEventRecord.symbol == q.symbol)
        out.append(Source("alert", alerts, AlertEventRecord.created_at, AlertEventRecord.id, lambda r: _from_alert(r[0], str(r[1]))))
    if q.source in ("all", "autopilot"):
        from_proposals = select(AiActionRecord.trade_id).where(
            AiActionRecord.tenant_id == user.tenant_id, AiActionRecord.action == ENTRY, AiActionRecord.trade_id.is_not(None))
        trades = select(TradeRecord).where(
            TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.deployment_id.is_not(None),
            TradeRecord.id.not_in(from_proposals))
        if q.symbol:
            trades = trades.where(TradeRecord.symbol == q.symbol)
        out.append(Source("trade", trades, TradeRecord.entry_time, TradeRecord.id, lambda r: _from_trade(r[0])))
    if q.source == "all":
        history = select(SignalHistoryRecord).where(
            SignalHistoryRecord.tenant_id == user.tenant_id, SignalHistoryRecord.user_id == user.id,
            SignalHistoryRecord.direction.in_(("LONG", "SHORT")))
        if q.symbol:
            history = history.where(SignalHistoryRecord.symbol == q.symbol)
        out.append(Source("history", history, SignalHistoryRecord.created_at, SignalHistoryRecord.id, lambda r: _from_history(r[0])))
    return out


async def _page(session: AsyncSession, q: FeedQuery, cursor: Optional[Cursor], limit: int) -> Tuple[List[Dict[str, Any]], Optional[Cursor]]:
    merged: List[Dict[str, Any]] = []
    capped = False
    for src in _sources(q):
        rows, more = await _read(session, q, src, cursor, limit + 1)
        merged.extend(rows)
        capped = capped or (more and len(rows) <= limit)
    merged.sort(key=lambda i: i["_key"], reverse=True)
    page = merged[:limit]
    more = len(merged) > limit or capped
    nxt = Cursor(*page[-1]["_key"]) if more and page else None
    return page, nxt


async def _count(session: AsyncSession, q: FeedQuery) -> int:
    total = 0
    for src in _sources(q):
        rows, _ = await _read(session, q, src, None, SCAN_CAP)
        total += len(rows)
    return total


async def feed(session: AsyncSession, user: User, *, tab: str = "today", source: str = "all", symbol: Optional[str] = None,
               cursor: Optional[str] = None, limit: int = DEFAULT_LIMIT, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The page of the inbox `tab` for `user` (see the module docstring). Raises BadCursor for a cursor it did not issue."""
    if tab not in TABS or source not in SOURCE_FILTERS:
        raise ValueError("Unknown tab or source")
    from app.platform.controls import features_for_tenant
    now = _utc(now or datetime.now(timezone.utc))
    flags = await features_for_tenant(session, user.tenant_id)
    sym = (symbol or "").strip().upper() or None
    q = FeedQuery(user=user, tab=tab, source=source, symbol=sym, now=now, flags=flags)
    at = decode_cursor(cursor) if cursor else None
    items, nxt = await _page(session, q, at, max(1, min(int(limit), MAX_LIMIT)))
    counts = None
    if at is None:                            # the tab counts come with the first page only
        counts = {"waiting": await _count(session, FeedQuery(user, "waiting", source, sym, now, flags)),
                  "today": await _count(session, FeedQuery(user, "today", source, sym, now, flags))}
    return {
        "tab": tab, "source": source, "symbol": sym,
        "items": [{k: v for k, v in i.items() if k != "_key"} for i in items],
        "next_cursor": encode_cursor(nxt) if nxt else None,
        "counts": counts,
        "as_of": now.isoformat(),
        "since": _iso(q.since),
        # Which sources this organisation has on (a source whose flag is off contributes nothing).
        "sources": {"proposals": True, "intents": bool(flags.get("execution_core")), "scan_matches": bool(flags.get("screener_v2")),
                    "autopilot_fills": True, "signal_generator": True},
    }
