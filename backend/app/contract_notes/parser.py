"""Phase D4: broker-agnostic contract-note / tradebook CSV parsing.

Every Indian broker issues a daily contract note (PDF) and most also export the same fills as
CSV ("tradebook", "trade report", "P&L statement"). Column names differ per broker, so the
parser accepts a set of aliases per field and is otherwise strict: a file whose header does not
contain a recognisable symbol, side, quantity and price column is rejected with the header it
saw, rather than silently matching nothing.

Charges: either a `total_charges`-style column, or any subset of the component columns
(brokerage, STT, exchange transaction charges, GST, SEBI turnover fee, stamp duty, clearing),
which are summed per leg and kept as a breakdown. Legs without any charge column parse with
zero charges - that is still useful for matching order ids, and the caller reports it.
"""
import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence

ALIASES: Dict[str, Sequence[str]] = {
    "date": ("trade_date", "date", "trade date", "order_execution_time", "execution_time", "timestamp", "time"),
    "symbol": ("symbol", "tradingsymbol", "trading_symbol", "trading symbol", "scrip", "instrument", "security"),
    "side": ("side", "trade_type", "transaction_type", "transaction type", "buy/sell", "buy_sell", "type", "action"),
    "quantity": ("quantity", "qty", "filled_quantity", "traded_qty", "trade_qty"),
    "price": ("price", "trade_price", "avg_price", "average_price", "fill_price", "rate"),
    "order_id": ("order_id", "orderid", "order id", "order_no", "order number", "broker_order_id", "exchange_order_id"),
    "total_charges": ("total_charges", "charges", "total charges", "net_charges", "total_tax_and_charges"),
    # component charges (summed when total is absent)
    "brokerage": ("brokerage",),
    "stt": ("stt", "stt_ctt", "securities_transaction_tax", "stt/ctt"),
    "exchange_charges": ("exchange_charges", "exchange_transaction_charges", "transaction_charges", "exch_charges", "exchange txn charges"),
    "gst": ("gst", "igst", "cgst_sgst", "tax"),
    "sebi_charges": ("sebi_charges", "sebi_turnover_fees", "sebi fees", "sebi"),
    "stamp_duty": ("stamp_duty", "stamp duty", "stamp_charges"),
    "clearing_charges": ("clearing_charges", "clearing", "ipft"),
}
COMPONENTS = ("brokerage", "stt", "exchange_charges", "gst", "sebi_charges", "stamp_duty", "clearing_charges")
REQUIRED = ("symbol", "side", "quantity", "price")

_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%Y/%m/%d")


class ContractNoteParseError(ValueError):
    pass


@dataclass
class ContractNoteLeg:
    symbol: str
    side: str            # BUY / SELL
    quantity: float
    price: float
    trade_date: Optional[date]
    order_id: Optional[str]
    charges: float
    breakdown: Dict[str, float] = field(default_factory=dict)
    row_number: int = 0


@dataclass
class ParsedContractNote:
    legs: List[ContractNoteLeg]
    columns: Dict[str, str]          # canonical field -> header used
    warnings: List[str] = field(default_factory=list)

    @property
    def total_charges(self) -> float:
        return round(sum(l.charges for l in self.legs), 2)

    @property
    def note_date(self) -> Optional[date]:
        dates = [l.trade_date for l in self.legs if l.trade_date]
        return max(dates) if dates else None


def _norm(header: str) -> str:
    return header.strip().lower().lstrip("﻿")


def _map_columns(headers: Sequence[str]) -> Dict[str, str]:
    normalised = {_norm(h): h for h in headers if h is not None}
    mapping: Dict[str, str] = {}
    for field_name, aliases in ALIASES.items():
        for alias in aliases:
            if alias in normalised and normalised[alias] not in mapping.values():
                mapping[field_name] = normalised[alias]
                break
    return mapping


def _number(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("₹", "").replace("Rs.", "").replace("INR", "")
    if text in ("", "-", "NA", "N/A"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def parse_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    text = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text[:len(datetime.now().strftime(fmt))] if "%H" not in fmt else text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _side(raw: str) -> Optional[str]:
    text = (raw or "").strip().upper()
    if text in ("B", "BUY", "PURCHASE", "LONG"):
        return "BUY"
    if text in ("S", "SELL", "SALE", "SHORT"):
        return "SELL"
    return None


def parse_contract_note_csv(content: bytes | str) -> ParsedContractNote:
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ContractNoteParseError("Empty file - no header row")
    columns = _map_columns(reader.fieldnames)
    missing = [f for f in REQUIRED if f not in columns]
    if missing:
        raise ContractNoteParseError(
            f"Could not find column(s) {missing} in header {list(reader.fieldnames)}. "
            f"Accepted names: " + "; ".join(f"{m}: {', '.join(ALIASES[m])}" for m in missing)
        )

    legs: List[ContractNoteLeg] = []
    warnings: List[str] = []
    has_charge_column = "total_charges" in columns or any(c in columns for c in COMPONENTS)
    for index, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue
        symbol = (row.get(columns["symbol"]) or "").strip().upper()
        side = _side(row.get(columns["side"]) or "")
        quantity = _number(row.get(columns["quantity"]))
        price = _number(row.get(columns["price"]))
        if not symbol or side is None or not quantity or price is None:
            warnings.append(f"row {index}: skipped (symbol/side/quantity/price unreadable)")
            continue
        breakdown: Dict[str, float] = {}
        for component in COMPONENTS:
            if component in columns:
                value = _number(row.get(columns[component]))
                if value is not None:
                    breakdown[component] = round(abs(value), 4)
        total = _number(row.get(columns["total_charges"])) if "total_charges" in columns else None
        charges = abs(total) if total is not None else sum(breakdown.values())
        order_id = (row.get(columns["order_id"]) or "").strip() if "order_id" in columns else ""
        legs.append(ContractNoteLeg(
            symbol=symbol, side=side, quantity=abs(quantity), price=price,
            trade_date=parse_date(row.get(columns["date"])) if "date" in columns else None,
            order_id=order_id or None, charges=round(charges, 2), breakdown=breakdown, row_number=index,
        ))
    if not legs:
        raise ContractNoteParseError("No readable trade rows in the file" + (f" ({warnings[0]})" if warnings else ""))
    if not has_charge_column:
        warnings.append("No charge columns found - legs parsed with zero charges (matching only)")
    return ParsedContractNote(legs=legs, columns=columns, warnings=warnings)
