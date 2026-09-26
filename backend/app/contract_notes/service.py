"""Phase D4: matching contract-note legs to trades and applying real charges.

The platform's `trades.charges`/`pnl` at close time are an *estimate* from the NSE cost model
in `app/execution/paper_broker.py`. The broker's contract note is the truth. This module takes
the parsed legs of a note and, per trade in the tenant:

1. matches legs to the trade by broker order id first (`broker_order_id` for the entry,
   `sl_order_id` / `exit_order_id` for the exit - exact, unambiguous), and only then by
   (symbol, side, quantity, date) for legs without an order id or for PAPER-era imports;
2. sums the matched legs' charges into the trade's actual charges, recomputes P&L as
   gross minus those charges, and marks `charges_source = CONTRACT_NOTE`;
3. keeps every leg (matched or not) on the note record, and never edits a trade that no leg
   matched. Re-uploading the same file (same SHA-256) is refused; a corrected file for the same
   day simply re-matches and overwrites the previous actual charges.

A tenant's trades only - the caller passes `tenant_id` derived server-side, as everywhere.
"""
import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.contract_notes.parser import ContractNoteLeg, ParsedContractNote, parse_contract_note_csv
from app.db.models import ContractNoteLineRecord, ContractNoteRecord, TradeRecord

logger = logging.getLogger(__name__)

MATCH_ORDER_ID = "ORDER_ID"
MATCH_FILL = "SYMBOL_QTY"
UNMATCHED = "UNMATCHED"


@dataclass
class TradeUpdate:
    trade_id: int
    symbol: str
    old_charges: float
    new_charges: float
    old_pnl: Optional[float]
    new_pnl: Optional[float]
    legs: int


@dataclass
class IngestResult:
    note_id: Optional[int]
    filename: str
    sha256: str
    lines: int
    matched: int
    trades_updated: List[TradeUpdate] = field(default_factory=list)
    unmatched: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    applied: bool = False
    total_charges: float = 0.0

    def as_dict(self) -> Dict:
        return {
            "note_id": self.note_id, "filename": self.filename, "sha256": self.sha256, "lines": self.lines,
            "matched": self.matched, "applied": self.applied, "total_charges": self.total_charges,
            "trades_updated": [u.__dict__ for u in self.trades_updated], "unmatched": self.unmatched,
            "warnings": self.warnings,
        }


class DuplicateContractNote(ValueError):
    pass


def _trade_side(trade: TradeRecord, leg_is_entry: bool) -> str:
    long = trade.direction == "LONG"
    return ("BUY" if long else "SELL") if leg_is_entry else ("SELL" if long else "BUY")


def _trade_dates(trade: TradeRecord) -> List[date]:
    dates = []
    for ts in (trade.entry_time, trade.exit_time):
        if ts is not None:
            aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            # Indian session dates: convert UTC to IST before taking the date.
            dates.append((aware + timedelta(hours=5, minutes=30)).date())
    return dates


def match_legs(legs: List[ContractNoteLeg], trades: List[TradeRecord]) -> Dict[int, List[tuple]]:
    """Returns {trade_id: [(leg, method), ...]}; legs not in any list are unmatched. A leg is
    used at most once."""
    by_order: Dict[str, TradeRecord] = {}
    for trade in trades:
        for oid in (trade.broker_order_id, trade.sl_order_id, trade.exit_order_id):
            if oid:
                by_order[str(oid)] = trade
    matched: Dict[int, List[tuple]] = defaultdict(list)
    used = set()
    for leg in legs:
        if leg.order_id and leg.order_id in by_order:
            matched[by_order[leg.order_id].id].append((leg, MATCH_ORDER_ID))
            used.add(id(leg))
    # Fallback: symbol + side + quantity + date, entry then exit, one leg per side per trade.
    for trade in trades:
        for is_entry in (True, False):
            if not is_entry and trade.exit_time is None:
                continue
            already = any(m == MATCH_ORDER_ID and (l.side == _trade_side(trade, is_entry)) for l, m in matched.get(trade.id, []))
            if already:
                continue
            want_side = _trade_side(trade, is_entry)
            want_dates = _trade_dates(trade)
            for leg in legs:
                if id(leg) in used or leg.symbol != trade.symbol.upper() or leg.side != want_side:
                    continue
                if abs(leg.quantity - trade.quantity) > 1e-6:
                    continue
                if leg.trade_date is not None and want_dates and leg.trade_date not in want_dates:
                    continue
                matched[trade.id].append((leg, MATCH_FILL))
                used.add(id(leg))
                break
    return matched


async def ingest_contract_note(
    session: AsyncSession, *, tenant_id: int, user_id: Optional[int], content: bytes, filename: str,
    broker_name: str = "", apply: bool = True,
) -> IngestResult:
    """Parses, matches and (when `apply`) writes the note, its lines and the trade updates, then
    commits. With `apply=False` it is a dry run: same result shape, nothing written."""
    sha = hashlib.sha256(content).hexdigest()
    parsed: ParsedContractNote = parse_contract_note_csv(content)
    result = IngestResult(note_id=None, filename=filename, sha256=sha, lines=len(parsed.legs), matched=0,
                          warnings=list(parsed.warnings), total_charges=parsed.total_charges)

    if apply:
        existing = await session.scalar(select(ContractNoteRecord).where(
            ContractNoteRecord.tenant_id == tenant_id, ContractNoteRecord.sha256 == sha))
        if existing is not None:
            raise DuplicateContractNote(f"This file was already uploaded on {existing.uploaded_at.date()} (note #{existing.id})")

    # Candidate trades: this tenant, closed or open, LIVE and PAPER (a PAPER-era import is still
    # a valid reconciliation exercise), limited to the note's date window plus a day each side.
    query = select(TradeRecord).where(TradeRecord.tenant_id == tenant_id)
    dates = [l.trade_date for l in parsed.legs if l.trade_date]
    if dates:
        lo, hi = min(dates) - timedelta(days=2), max(dates) + timedelta(days=2)
        from datetime import datetime as _dt, time as _time
        query = query.where(TradeRecord.entry_time >= _dt.combine(lo, _time.min, tzinfo=timezone.utc),
                            TradeRecord.entry_time <= _dt.combine(hi, _time.max, tzinfo=timezone.utc))
    trades = list(await session.scalars(query.order_by(TradeRecord.id)))
    matches = match_legs(parsed.legs, trades)
    matched_leg_ids = {id(l) for pairs in matches.values() for l, _ in pairs}
    result.matched = len(matched_leg_ids)
    result.unmatched = [
        {"row": l.row_number, "symbol": l.symbol, "side": l.side, "quantity": l.quantity, "price": l.price,
         "order_id": l.order_id, "date": l.trade_date.isoformat() if l.trade_date else None, "charges": l.charges}
        for l in parsed.legs if id(l) not in matched_leg_ids
    ]

    trades_by_id = {t.id: t for t in trades}
    for trade_id, pairs in matches.items():
        trade = trades_by_id[trade_id]
        new_charges = round(sum(l.charges for l, _ in pairs), 2)
        new_pnl = trade.pnl
        if trade.exit_price is not None:
            sign = 1 if trade.direction == "LONG" else -1
            new_pnl = round(sign * (trade.exit_price - trade.entry_price) * trade.quantity - new_charges, 2)
        result.trades_updated.append(TradeUpdate(
            trade_id=trade.id, symbol=trade.symbol, old_charges=trade.charges, new_charges=new_charges,
            old_pnl=trade.pnl, new_pnl=new_pnl, legs=len(pairs),
        ))

    if not apply:
        return result

    note = ContractNoteRecord(
        tenant_id=tenant_id, uploaded_by=user_id, broker_name=broker_name[:50], filename=filename[:255], sha256=sha,
        note_date=parsed.note_date, line_count=len(parsed.legs), matched_lines=result.matched,
        trades_updated=len(result.trades_updated), total_charges=parsed.total_charges,
    )
    session.add(note)
    await session.flush()
    leg_to_trade = {id(l): (tid, m) for tid, pairs in matches.items() for l, m in pairs}
    for leg in parsed.legs:
        tid, method = leg_to_trade.get(id(leg), (None, UNMATCHED))
        session.add(ContractNoteLineRecord(
            contract_note_id=note.id, tenant_id=tenant_id, trade_id=tid, trade_date=leg.trade_date, symbol=leg.symbol,
            side=leg.side, quantity=leg.quantity, price=leg.price, order_id=leg.order_id, charges=leg.charges,
            breakdown_json=json.dumps(leg.breakdown), match_method=method,
        ))
    for update in result.trades_updated:
        trade = trades_by_id[update.trade_id]
        trade.charges = update.new_charges
        trade.pnl = update.new_pnl
        trade.charges_source = "CONTRACT_NOTE"
        trade.contract_note_id = note.id
    await write_audit_log(
        session, tenant_id, user_id, "contract_note_ingested",
        f"{filename} sha256={sha[:16]} legs={len(parsed.legs)} matched={result.matched} trades={len(result.trades_updated)} charges={parsed.total_charges}",
    )
    await session.commit()
    result.note_id = note.id
    result.applied = True
    return result
