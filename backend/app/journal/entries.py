"""M62 JN-2: the trader's own layer of a journal entry - notes, his own tags, his rule checklist, attachments, manual
(outside-ATP) trades and the chart snapshots at entry and exit.

Two kinds of entry:
* an ATP trade (`trades` row; PAPER or LIVE): the auto-captured journal (`capture.py`) plus this layer. Its notes stay
  on `trades.notes` - the one place the trader's words for a trade already live (`PATCH /api/trades/{id}/journal`
  writes the same column) - and nothing else on the trade row is ever written here.
* a manual trade (`journal_manual_trades`): a trade he did outside ATP, typed in by him. It is journal data only:
  no order, position, broker call, risk counter, P&L book or monitor reads it, and nothing here places, changes or
  blocks an order.

Every number shown is either the platform's record of the trade or the trader's own input: the plan's stop and target
are the ones the trade carried (his own settings / strategy), never computed or suggested here; a value nobody stated
stays empty. Personal: every read and write is scoped to the logged-in trader (`user_id` and `tenant_id`), so another
user, or another organisation, gets "not found".

Chart snapshots are drawn on request by the platform's existing server-side renderer (`app/ai/chart_png.py`) from the
bars stored in the market-data lake that end at the instant (entry or exit), with entry / exit / stop marked. No image
is stored for them (ADR-0026 §7: a snapshot is a reference, redrawn).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    JournalAttachmentRecord,
    JournalEntryRecord,
    JournalManualTradeRecord,
    JournalTagRecord,
    StrategyDeploymentRecord,
    TradeRecord,
    User,
)
from app.journal import capture, service
from app.journal.settings import DisciplineSettings, clean_label

MANUAL_SOURCE = "OUTSIDE_ATP"          # how a manual entry is marked everywhere it is shown
MANUAL_LABEL = "Manual - done outside ATP"
NOTES_MAX = 5000                       # the same limit as PATCH /api/trades/{id}/journal
KINDS = ("UNDERLYING", "OPTION", "FUTURE")
FUTURE_SLACK = timedelta(minutes=5)    # a manual trade cannot be entered in the future (clock skew allowed)
MB = 1024 * 1024


class EntryError(ValueError):
    """A request the trader can correct; `status` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    when = _utc(value)
    return when.isoformat() if when else None


def _loads(raw: str | None, default: Any) -> Any:
    return capture._loads(raw, default)


# --- which entry ------------------------------------------------------------------------------------------------------

@dataclass
class Ref:
    """One journal entry of the trader: an ATP trade (`trade`) or a manual trade (`manual`)."""
    trade: TradeRecord | None = None
    manual: JournalManualTradeRecord | None = None

    @property
    def kind(self) -> str:
        return "trade" if self.trade is not None else "manual"

    @property
    def id(self) -> int:
        row = self.trade if self.trade is not None else self.manual
        assert row is not None
        return int(row.id)


async def own_manual(session: AsyncSession, user: User, manual_id: int) -> JournalManualTradeRecord | None:
    row = await session.get(JournalManualTradeRecord, manual_id)
    if row is None or row.tenant_id != user.tenant_id or row.user_id != user.id:
        return None
    return row


async def own_ref(session: AsyncSession, user: User, kind: str, ref_id: int) -> Ref | None:
    if kind == "trade":
        trade = await service.own_trade(session, user, ref_id)
        return Ref(trade=trade) if trade is not None else None
    manual = await own_manual(session, user, ref_id)
    return Ref(manual=manual) if manual is not None else None


async def _journal_entry(session: AsyncSession, trade: TradeRecord) -> JournalEntryRecord:
    """The trade's journal entry, created from the trade row when no hook has written it yet."""
    entry = (await capture.ensure_entries(session, [trade])).get(trade.id)
    if entry is None:   # only a concurrent delete of the trade itself gets here
        raise EntryError("No such trade", 404)
    return entry


# --- notes ------------------------------------------------------------------------------------------------------------

async def set_notes(session: AsyncSession, user: User, ref: Ref, notes: str | None) -> str | None:
    """Replaces the entry's notes (empty text clears them). Editable any time, open or closed."""
    clean = (notes or "").strip()[:NOTES_MAX] or None
    if ref.trade is not None:
        # One column, written by a guarded UPDATE: nothing else on the trade row is touched.
        await session.execute(update(TradeRecord).where(TradeRecord.id == ref.trade.id, TradeRecord.tenant_id == user.tenant_id,
                                                        TradeRecord.user_id == user.id).values(notes=clean)
                              .execution_options(synchronize_session=False))
        ref.trade.notes = clean
    elif ref.manual is not None:
        ref.manual.notes = clean
    await session.commit()
    return clean


# --- the trader's own tags ----------------------------------------------------------------------------------------------

def _layer_holder(ref: Ref, entry: JournalEntryRecord | None) -> Any:
    return ref.manual if ref.manual is not None else entry


async def set_user_tags(session: AsyncSession, user: User, ref: Ref, tags: Sequence[str], s: DisciplineSettings) -> list[str]:
    """Replaces the entry's own mistake / emotion tags; each one must be on the trader's own list (settings)."""
    allowed = list(s.custom_tags)
    out: list[str] = []
    for raw in tags:
        tag = clean_label(raw)
        if not tag:
            continue
        if tag not in allowed:
            listed = ", ".join(allowed) if allowed else "none yet"
            raise EntryError(f"'{tag}' is not on your own tag list ({listed}). Add it in the journal settings first.")
        if tag not in out:
            out.append(tag)
    holder = _layer_holder(ref, await _journal_entry(session, ref.trade) if ref.trade is not None else None)
    holder.user_tags_json = json.dumps(out)
    await session.commit()
    return out


# --- the rule checklist -------------------------------------------------------------------------------------------------

def checklist_view(stored_raw: str | None, s: DisciplineSettings) -> list[dict[str, Any]]:
    """Every rule on the trader's own list with his answer (yes / no / not answered yet), then any answered rule he
    has since taken off his list (kept, marked `retired`)."""
    stored = {str(i.get("rule")): i.get("followed") for i in _loads(stored_raw, []) if isinstance(i, dict) and i.get("rule")}
    out = [{"rule": rule, "followed": stored.get(rule), "retired": False} for rule in s.checklist_rules]
    out += [{"rule": rule, "followed": followed, "retired": True} for rule, followed in stored.items() if rule not in s.checklist_rules]
    return out


async def set_checklist(session: AsyncSession, user: User, ref: Ref, items: Sequence[dict[str, Any]],
                        s: DisciplineSettings) -> list[dict[str, Any]]:
    """Answers some rules of the trader's own checklist: followed true / false, or null to clear an answer. Rules not
    sent keep their answer; a rule must be on his list (settings)."""
    entry = await _journal_entry(session, ref.trade) if ref.trade is not None else None
    holder = _layer_holder(ref, entry)
    answers: dict[str, bool] = {str(i.get("rule")): bool(i.get("followed")) for i in _loads(holder.checklist_json, [])
                                if isinstance(i, dict) and i.get("rule") and i.get("followed") is not None}
    for item in items:
        rule = " ".join(str(item.get("rule") or "").split())
        if rule not in s.checklist_rules:
            listed = "; ".join(s.checklist_rules) if s.checklist_rules else "none yet"
            raise EntryError(f"'{rule[:60]}' is not one of your own rules ({listed}). Add it in the journal settings first.")
        followed = item.get("followed")
        if followed is None:
            answers.pop(rule, None)
        elif isinstance(followed, bool):
            answers[rule] = followed
        else:
            raise EntryError("followed must be true, false or null")
    holder.checklist_json = json.dumps([{"rule": r, "followed": f} for r, f in answers.items()])
    await session.commit()
    return checklist_view(holder.checklist_json, s)


# --- attachments ------------------------------------------------------------------------------------------------------

def attachment_dict(row: JournalAttachmentRecord) -> dict[str, Any]:
    return {"id": row.id, "trade_id": row.trade_id, "manual_trade_id": row.manual_trade_id, "size_bytes": row.size_bytes,
            "width": row.width, "height": row.height, "created_at": _iso(row.created_at)}


def _attachment_filter(ref: Ref) -> Any:
    return (JournalAttachmentRecord.trade_id == ref.id) if ref.trade is not None else (JournalAttachmentRecord.manual_trade_id == ref.id)


async def attachments_of(session: AsyncSession, user: User, refs: Sequence[Ref]) -> dict[tuple[str, int], list[JournalAttachmentRecord]]:
    """{(kind, id): [attachment, ...]} for several entries in one query."""
    trade_ids = [r.id for r in refs if r.trade is not None]
    manual_ids = [r.id for r in refs if r.manual is not None]
    out: dict[tuple[str, int], list[JournalAttachmentRecord]] = {}
    if not trade_ids and not manual_ids:
        return out
    rows = await session.scalars(select(JournalAttachmentRecord).where(
        JournalAttachmentRecord.user_id == user.id, JournalAttachmentRecord.tenant_id == user.tenant_id,
        or_(JournalAttachmentRecord.trade_id.in_(trade_ids or [-1]), JournalAttachmentRecord.manual_trade_id.in_(manual_ids or [-1])),
    ).order_by(JournalAttachmentRecord.id))
    for row in rows:
        key = ("trade", int(row.trade_id)) if row.trade_id is not None else ("manual", int(row.manual_trade_id or 0))
        out.setdefault(key, []).append(row)
    return out


def size_limit_bytes(s: DisciplineSettings) -> int:
    return int(float(s.attachment_max_mb) * MB)


async def image_rules(session: AsyncSession, s: DisciplineSettings) -> dict[str, Any]:
    """The image checks of the shared upload path (`chart_analyze.imaging`): its dimension and pixel limits, with the
    trader's own size limit."""
    from app.chart_analyze import settings as upload_settings
    rules = dict(await upload_settings.load(session))
    rules["max_image_bytes"] = size_limit_bytes(s)
    return rules


async def add_attachment(session: AsyncSession, user: User, ref: Ref, data: bytes, s: DisciplineSettings) -> JournalAttachmentRecord:
    """Checks, re-encodes (no metadata survives) and stores one image privately for this organisation."""
    from app.chart_analyze import imaging, storage
    held = int(await session.scalar(select(func.count()).select_from(JournalAttachmentRecord).where(
        JournalAttachmentRecord.user_id == user.id, _attachment_filter(ref))) or 0)
    if held >= int(s.attachments_per_trade):
        raise EntryError(f"This entry already holds {held} images (your limit is {int(s.attachments_per_trade)}). Remove one first.", 409)
    rules = await image_rules(session, s)
    try:
        clean = await asyncio.to_thread(imaging.sanitize, data, rules)
    except imaging.ImageRejected as exc:
        raise EntryError(str(exc), exc.status) from None
    name = storage.new_name(user.tenant_id)
    try:
        storage.write(name, clean.png)
    except storage.StorageUnavailable:
        raise EntryError("Image storage is not available on this server. Ask the administrator.", 503) from None
    row = JournalAttachmentRecord(tenant_id=user.tenant_id, user_id=user.id, trade_id=ref.id if ref.trade is not None else None,
                                  manual_trade_id=ref.id if ref.manual is not None else None, image_path=name,
                                  size_bytes=len(clean.png), width=clean.width, height=clean.height, sha256=clean.sha256)
    session.add(row)
    try:
        await session.commit()
    except Exception:
        storage.delete(name)          # never a file without its row
        raise
    await session.refresh(row)
    return row


async def own_attachment(session: AsyncSession, user: User, attachment_id: int) -> JournalAttachmentRecord | None:
    row = await session.get(JournalAttachmentRecord, attachment_id)
    if row is None or row.user_id != user.id or row.tenant_id != user.tenant_id:
        return None
    return row


async def delete_attachment(session: AsyncSession, row: JournalAttachmentRecord) -> None:
    from app.chart_analyze import storage
    path = row.image_path
    await session.delete(row)
    await session.commit()
    storage.delete(path)


def read_attachment(row: JournalAttachmentRecord) -> bytes | None:
    from app.chart_analyze import storage
    return storage.read(row.image_path)


# --- manual (outside-ATP) trades --------------------------------------------------------------------------------------

def _price(name: str, value: Any, *, required: bool = False) -> float | None:
    if value is None:
        if required:
            raise EntryError(f"{name} is required")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not float(value) > 0:
        raise EntryError(f"{name} must be a number above 0")
    return float(value)


def _text(name: str, value: Any, longest: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EntryError(f"{name} must be text")
    clean = " ".join(value.split())
    if len(clean) > longest:
        raise EntryError(f"{name} may have at most {longest} characters")
    return clean or None


def _when(name: str, value: Any, now: datetime) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise EntryError(f"{name} must be a date and time")
    when = _utc(value)
    assert when is not None
    if when > now + FUTURE_SLACK:
        raise EntryError(f"{name} cannot be in the future")
    return when


def manual_values(body: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """The columns of a manual trade from what the trader typed - checked, never completed with a value he did not give."""
    now = now or datetime.now(UTC)
    symbol = _text("symbol", body.get("symbol"), 50)
    if not symbol:
        raise EntryError("symbol is required")
    direction = str(body.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        raise EntryError("direction must be LONG or SHORT")
    quantity = _price("quantity", body.get("quantity"), required=True)
    entry_time = _when("entry_time", body.get("entry_time"), now)
    if entry_time is None:
        raise EntryError("entry_time is required")
    exit_time = _when("exit_time", body.get("exit_time"), now)
    exit_price = _price("exit_price", body.get("exit_price"))
    if (exit_time is None) != (exit_price is None):
        raise EntryError("exit_time and exit_price go together: give both for a closed trade, neither for an open one")
    if exit_time is not None and exit_time < entry_time:
        raise EntryError("exit_time cannot be before entry_time")
    charges = body.get("charges") or 0.0
    if isinstance(charges, bool) or not isinstance(charges, (int, float)) or float(charges) < 0:
        raise EntryError("charges must be a number of 0 or more")
    kind = str(body.get("instrument_kind") or "UNDERLYING").upper()
    if kind not in KINDS:
        raise EntryError(f"instrument_kind must be one of {list(KINDS)}")
    notes = body.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise EntryError("notes must be text")
    return {"symbol": symbol.upper(), "exchange": (_text("exchange", body.get("exchange"), 20) or "").upper() or None,
            "instrument_kind": kind, "direction": direction, "quantity": quantity, "entry_time": entry_time,
            "entry_price": _price("entry_price", body.get("entry_price"), required=True),
            "stop_loss": _price("stop_loss", body.get("stop_loss")), "target": _price("target", body.get("target")),
            "exit_time": exit_time, "exit_price": exit_price, "exit_reason": _text("exit_reason", body.get("exit_reason"), 100),
            "charges": round(float(charges), 2), "setup": _text("setup", body.get("setup"), 100),
            "timeframe": _text("timeframe", body.get("timeframe"), 10), "area": _text("area", body.get("area"), 200),
            "notes": (notes or "").strip()[:NOTES_MAX] or None}


async def add_manual(session: AsyncSession, user: User, body: dict[str, Any]) -> JournalManualTradeRecord:
    row = JournalManualTradeRecord(tenant_id=user.tenant_id, user_id=user.id, **manual_values(body))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_manual(session: AsyncSession, user: User, *, limit: int = 200) -> list[JournalManualTradeRecord]:
    return list(await session.scalars(select(JournalManualTradeRecord).where(
        JournalManualTradeRecord.user_id == user.id, JournalManualTradeRecord.tenant_id == user.tenant_id,
    ).order_by(JournalManualTradeRecord.entry_time.desc(), JournalManualTradeRecord.id.desc()).limit(limit)))


async def manual_in(session: AsyncSession, user: User, start: datetime, end: datetime) -> list[JournalManualTradeRecord]:
    """The trader's manual trades entered in [start, end), oldest first (the week view)."""
    return list(await session.scalars(select(JournalManualTradeRecord).where(
        JournalManualTradeRecord.user_id == user.id, JournalManualTradeRecord.tenant_id == user.tenant_id,
        JournalManualTradeRecord.entry_time >= start, JournalManualTradeRecord.entry_time < end,
    ).order_by(JournalManualTradeRecord.entry_time, JournalManualTradeRecord.id)))


async def delete_manual(session: AsyncSession, user: User, row: JournalManualTradeRecord) -> None:
    """Removes a manual entry and its attachments (rows and files)."""
    ref = Ref(manual=row)
    for attachment in (await attachments_of(session, user, [ref])).get(("manual", row.id), []):
        await delete_attachment(session, attachment)
    await session.delete(row)
    await session.commit()


def manual_pnl(row: JournalManualTradeRecord) -> float | None:
    if row.exit_price is None:
        return None
    sign = 1 if row.direction == "LONG" else -1
    return round(sign * (float(row.exit_price) - float(row.entry_price)) * float(row.quantity) - float(row.charges or 0.0), 2)


def _risk(direction: str, entry_price: float, stop: float | None, quantity: float) -> float:
    """Rupees at risk from the entry to the stop the trader had (0 when he had none, or it sat on the wrong side)."""
    if stop is None:
        return 0.0
    sign = 1 if direction == "LONG" else -1
    per_unit = sign * (float(entry_price) - float(stop))
    return per_unit * abs(float(quantity)) if per_unit > 0 else 0.0


# --- the payloads -----------------------------------------------------------------------------------------------------

def plan_view(trade: TradeRecord, entry: JournalEntryRecord | None, timeframe: str | None) -> dict[str, Any]:
    """The plan the trade was taken on, as recorded: the setup (his own signal's name when the trade came from it, else
    the strategy), the timeframe it ran on, the area in his signal's own structure words, the invalidation (the
    initial stop) and the target the trade carried. Nothing is computed or filled in: a value not recorded is null."""
    plan = _loads(entry.plan_json, {}) if entry is not None else capture.plan_of(trade)
    raw_signal = plan.get("from_signal")
    signal: dict[str, Any] = raw_signal if isinstance(raw_signal, dict) else {}
    words = [str(w) for w in (signal.get("words") or []) if w]
    return {"setup": signal.get("setup") or trade.strategy_id, "timeframe": timeframe, "area": words or None,
            "invalidation": plan.get("stop"), "target": trade.target1, "entry": plan.get("entry"),
            "entry_source": plan.get("entry_source")}


async def deployment_timeframes(session: AsyncSession, trades: Sequence[TradeRecord]) -> dict[int, str]:
    ids = sorted({t.deployment_id for t in trades if t.deployment_id is not None})
    if not ids:
        return {}
    rows = await session.execute(select(StrategyDeploymentRecord.id, StrategyDeploymentRecord.timeframe).where(
        StrategyDeploymentRecord.id.in_(ids)))
    return {int(i): str(tf) for i, tf in rows.all() if tf}


def trade_payload(trade: TradeRecord, entry: JournalEntryRecord | None, tags: Sequence[JournalTagRecord], s: DisciplineSettings, *,
                  attachments: Sequence[JournalAttachmentRecord] = (), timeframe: str | None = None) -> dict[str, Any]:
    out = service.entry_dict(trade, entry, tags)
    out.update(kind="trade", id=trade.id, manual=False, journal_entry_id=entry.id if entry is not None else None,
               plan_view=plan_view(trade, entry, timeframe),
               user_tags=_loads(entry.user_tags_json, []) if entry is not None else [],
               checklist=checklist_view(entry.checklist_json if entry is not None else None, s),
               attachments=[attachment_dict(a) for a in attachments],
               charts={"entry": f"/api/journal/trades/{trade.id}/chart?at=entry",
                       "exit": f"/api/journal/trades/{trade.id}/chart?at=exit" if trade.exit_time is not None else None})
    return out


def manual_payload(row: JournalManualTradeRecord, s: DisciplineSettings, *, attachments: Sequence[JournalAttachmentRecord] = ()) -> dict[str, Any]:
    entry_time, exit_time = _utc(row.entry_time), _utc(row.exit_time)
    pnl = manual_pnl(row)
    risk = _risk(row.direction, float(row.entry_price), float(row.stop_loss) if row.stop_loss is not None else None, float(row.quantity))
    fills: list[dict[str, Any]] = [{"leg": "ENTRY", "side": "BUY" if row.direction == "LONG" else "SELL", "price": float(row.entry_price),
                                    "quantity": float(row.quantity), "at": _iso(entry_time)}]
    if row.exit_price is not None:
        fills.append({"leg": "EXIT", "side": "SELL" if row.direction == "LONG" else "BUY", "price": float(row.exit_price),
                      "quantity": float(row.quantity), "at": _iso(exit_time), "reason": row.exit_reason})
    return {
        "kind": "manual", "id": row.id, "manual": True, "trade_id": None, "source": MANUAL_SOURCE, "source_label": MANUAL_LABEL,
        "symbol": row.symbol, "exchange": row.exchange, "mode": None, "direction": row.direction, "setup": row.setup,
        "instrument_kind": row.instrument_kind, "entry_time": _iso(entry_time), "exit_time": _iso(exit_time),
        "plan_view": {"setup": row.setup, "timeframe": row.timeframe, "area": [row.area] if row.area else None,
                      "invalidation": row.stop_loss, "target": row.target, "entry": row.entry_price, "entry_source": "manual"},
        "execution": {"entry_price": row.entry_price, "quantity": row.quantity, "slippage": None, "exit_price": row.exit_price,
                      "exit_reason": row.exit_reason},
        "fills": fills, "costs": {"charges": row.charges, "source": "MANUAL"},
        "pnl": pnl, "r_multiple": round(pnl / risk, 2) if pnl is not None and risk > 0 else None, "risk_amount": round(risk, 2),
        "holding_minutes": round((exit_time - entry_time).total_seconds() / 60.0, 1) if exit_time and entry_time else None,
        "notes": row.notes, "tags": [], "user_tags": _loads(row.user_tags_json, []), "checklist": checklist_view(row.checklist_json, s),
        "attachments": [attachment_dict(a) for a in attachments],
        "charts": {"entry": f"/api/journal/manual/{row.id}/chart?at=entry",
                   "exit": f"/api/journal/manual/{row.id}/chart?at=exit" if row.exit_time is not None else None},
        "created_at": _iso(row.created_at),
    }


async def trade_entry(session: AsyncSession, user: User, trade: TradeRecord, s: DisciplineSettings) -> dict[str, Any]:
    """One ATP trade's full journal entry (created from the trade row when missing)."""
    entry = await _journal_entry(session, trade)
    await session.commit()
    tags = list(await session.scalars(select(JournalTagRecord).where(JournalTagRecord.trade_id == trade.id, JournalTagRecord.user_id == user.id)
                                      .order_by(JournalTagRecord.tag)))
    files = (await attachments_of(session, user, [Ref(trade=trade)])).get(("trade", trade.id), [])
    timeframe = (await deployment_timeframes(session, [trade])).get(trade.deployment_id or -1)
    return trade_payload(trade, entry, tags, s, attachments=files, timeframe=timeframe)


async def manual_entry(session: AsyncSession, user: User, row: JournalManualTradeRecord, s: DisciplineSettings) -> dict[str, Any]:
    files = (await attachments_of(session, user, [Ref(manual=row)])).get(("manual", row.id), [])
    return manual_payload(row, s, attachments=files)


async def entry_payload(session: AsyncSession, user: User, ref: Ref, s: DisciplineSettings) -> dict[str, Any]:
    if ref.trade is not None:
        return await trade_entry(session, user, ref.trade, s)
    assert ref.manual is not None
    return await manual_entry(session, user, ref.manual, s)


# --- chart snapshots (server-rendered, never stored) -------------------------------------------------------------------

@dataclass
class ChartPlan:
    exchange: str
    symbol: str
    timeframe: str
    at: datetime
    levels: dict[str, float]


def chart_plan(ref: Ref, at: str, *, timeframe: str | None, greeks: dict[str, Any] | None = None) -> ChartPlan:
    """What to draw for the entry or exit snapshot: whose bars, ending when, with which marks.

    An option or a future is drawn on its underlying (the lake records the underlying), marked with the underlying stop
    and target the trade carried and, at entry, the underlying price recorded at the fill; a premium is never drawn on
    the underlying's scale."""
    if at not in ("entry", "exit"):
        raise EntryError("at must be entry or exit")
    if ref.trade is not None:
        t = ref.trade
        when = _utc(t.entry_time if at == "entry" else t.exit_time)
        if when is None:
            raise EntryError("The trade is still open - there is no exit snapshot yet", 404)
        kind = t.instrument_kind or "UNDERLYING"
        if kind != "UNDERLYING" and t.underlying_symbol:
            from app.instruments.master import INDEX_EXCHANGE, underlying_of
            under = underlying_of(t.underlying_symbol)
            levels = {"SL": t.underlying_stop_loss, "TGT": t.underlying_target1,
                      "ENTRY": (greeks or {}).get("underlying_price")}
            return ChartPlan(INDEX_EXCHANGE.get(under, "NSE"), under, timeframe or "1m", when, _levels(levels))
        stop = t.initial_stop_loss if t.initial_stop_loss is not None else t.stop_loss
        levels = {"ENTRY": t.entry_price, "SL": stop, "TGT": t.target1}
        if at == "exit":
            levels["EXIT"] = t.exit_price
        return ChartPlan((t.exchange or "NSE").upper(), t.symbol, timeframe or "1m", when, _levels(levels))
    m = ref.manual
    assert m is not None
    when = _utc(m.entry_time if at == "entry" else m.exit_time)
    if when is None:
        raise EntryError("The trade is still open - there is no exit snapshot yet", 404)
    levels = {"ENTRY": m.entry_price, "SL": m.stop_loss, "TGT": m.target}
    if at == "exit":
        levels["EXIT"] = m.exit_price
    return ChartPlan((m.exchange or "NSE").upper(), m.symbol, m.timeframe or "1m", when, _levels(levels))


def _levels(raw: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, value in raw.items():
        try:
            if value is not None and float(value) > 0:
                out[name] = float(value)
        except (TypeError, ValueError):
            continue
    return out


async def chart_png(session: AsyncSession, user: User, ref: Ref, at: str) -> bytes:
    """The PNG of the snapshot, drawn from the stored bars that END at or before the instant (nothing after it)."""
    from app.ai import chart_png as renderer
    from app.ai.vision_audit import lake_bars
    from app.market_data import live_access
    greeks = None
    timeframe = None
    if ref.trade is not None:
        timeframe = (await deployment_timeframes(session, [ref.trade])).get(ref.trade.deployment_id or -1)
        entry = await session.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == ref.trade.id))
        greeks = _loads(entry.greeks_entry_json, {}) if entry is not None and entry.greeks_entry_json else None
    plan = chart_plan(ref, at, timeframe=timeframe, greeks=greeks)
    bars = await lake_bars(session, plan.exchange, plan.symbol, plan.timeframe, plan.at,
                           daily_only=await live_access.byob_on(session, user.tenant_id))
    if not bars:
        raise EntryError("No stored candles for this trade's window, so the snapshot cannot be drawn", 404)
    return renderer.render(renderer.ChartSpec(bars, plan.levels))
