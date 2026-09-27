"""Phase P2 / sections 57-61: Indian financial-year tax report for a tenant's closed trades.

The platform is not a tax adviser; this report gives a CA the numbers they ask for, computed the
way the Income Tax Act classifies them, with every rate in one table so a Finance Act change is a
one-line edit:

* **Equity intraday** (UNDERLYING on NSE/BSE closed the same day) - speculative business income.
  Turnover = sum of absolute profit/loss per trade (ICAI guidance note for speculative/derivative
  turnover). STT: 0.025% of the sell side.
* **F&O** (OPTION/FUTURE, NFO/BFO/MCX) - non-speculative business income. Turnover = sum of
  absolute profit/loss plus premium received on options sold. STT: 0.1% of option premium sold,
  0.02% of futures sell value (rates from 1 Oct 2024); CTT for MCX futures 0.01% sell side.
* **Crypto / VDA** (section 115BBH) - gains taxed flat 30% per transfer, losses cannot be set off
  against anything; 1% TDS on sale consideration under section 194S is withheld by the exchange.

`charges` on each trade (estimated or from a contract note) already include STT/CTT; the STT
figures here are informational estimates so the user can reconcile against the broker's annual
tax P&L statement. FIU-IND reporting applies to VDA service providers (exchanges), not to their
customers, so nothing here files anything.
"""
import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TradeRecord
from app.instruments.registry import get_contract_spec
from app.market_data.calendar import IST, session_family

# Rates as percentages of the relevant value. Change here when the Finance Act changes them.
RATES = {
    "EQUITY_INTRADAY": {"stt_sell_pct": 0.025},
    "FNO_OPTION": {"stt_sell_pct": 0.1},
    "FNO_FUTURE": {"stt_sell_pct": 0.02},
    "MCX_FUTURE": {"ctt_sell_pct": 0.01},
    "CRYPTO": {"tds_pct": 1.0, "tax_pct": 30.0},
}
CLASSES = ("EQUITY_INTRADAY", "FNO", "CRYPTO")
DISCLAIMER = ("Estimates for reconciliation with your broker's tax P&L statement; not tax advice. Equity intraday and F&O are "
              "business income (speculative / non-speculative); crypto gains are taxed at 30% under section 115BBH with no loss "
              "set-off and 1% TDS under section 194S withheld by the exchange. Verify with a chartered accountant before filing.")


def financial_year(day: date) -> str:
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def fy_bounds(label: str) -> Tuple[datetime, datetime]:
    """'2026-27' -> [1 Apr 2026 00:00 IST, 1 Apr 2027 00:00 IST) as UTC instants."""
    try:
        start_year = int(label.split("-")[0])
    except (ValueError, IndexError) as exc:
        raise ValueError("Financial year must look like 2026-27") from exc
    start = datetime(start_year, 4, 1, tzinfo=IST).astimezone(timezone.utc)
    end = datetime(start_year + 1, 4, 1, tzinfo=IST).astimezone(timezone.utc)
    return start, end


def classify(trade: TradeRecord) -> str:
    spec = get_contract_spec(trade.symbol)
    family = session_family(trade.exchange or (spec.exchange if spec else "NSE"))
    if family == "CRYPTO" or (spec is not None and spec.asset_class.value == "CRYPTO"):
        return "CRYPTO"
    if (trade.instrument_kind or "UNDERLYING").upper() in ("OPTION", "FUTURE") or family == "MCX" or (trade.exchange or "").upper() in ("NFO", "BFO"):
        return "FNO"
    return "EQUITY_INTRADAY"


@dataclass
class ClassTotals:
    trades: int = 0
    gross_pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    turnover: float = 0.0
    sell_value: float = 0.0
    stt_estimate: float = 0.0
    ctt_estimate: float = 0.0
    tds_estimate: float = 0.0
    taxable_gains: float = 0.0      # crypto: sum of positive gains only
    tax_estimate: float = 0.0       # crypto: 30% of taxable gains
    disallowed_losses: float = 0.0  # crypto: losses that cannot be set off
    winners: int = 0
    by_symbol: Dict[str, float] = field(default_factory=dict)


def _sell_value(trade: TradeRecord) -> float:
    price = float(trade.exit_price if trade.direction == "LONG" else trade.entry_price)
    return price * float(trade.quantity)


def _accumulate(totals: ClassTotals, trade: TradeRecord, kind: str) -> None:
    net = float(trade.pnl or 0.0)
    charges = float(trade.charges or 0.0)
    gross = net + charges
    totals.trades += 1
    totals.gross_pnl += gross
    totals.charges += charges
    totals.net_pnl += net
    totals.winners += int(net > 0)
    totals.by_symbol[trade.symbol] = totals.by_symbol.get(trade.symbol, 0.0) + net
    sell_value = _sell_value(trade)
    totals.sell_value += sell_value
    if kind == "CRYPTO":
        totals.turnover += sell_value
        totals.tds_estimate += sell_value * RATES["CRYPTO"]["tds_pct"] / 100
        if net > 0:
            totals.taxable_gains += net
        else:
            totals.disallowed_losses += -net
        totals.tax_estimate = totals.taxable_gains * RATES["CRYPTO"]["tax_pct"] / 100
        return
    totals.turnover += abs(gross)
    instrument = (trade.instrument_kind or "UNDERLYING").upper()
    if kind == "FNO":
        if instrument == "OPTION":
            premium_sold = sell_value
            totals.turnover += premium_sold
            totals.stt_estimate += premium_sold * RATES["FNO_OPTION"]["stt_sell_pct"] / 100
        elif session_family(trade.exchange) == "MCX":
            totals.ctt_estimate += sell_value * RATES["MCX_FUTURE"]["ctt_sell_pct"] / 100
        else:
            totals.stt_estimate += sell_value * RATES["FNO_FUTURE"]["stt_sell_pct"] / 100
    else:
        totals.stt_estimate += sell_value * RATES["EQUITY_INTRADAY"]["stt_sell_pct"] / 100


async def closed_trades(session: AsyncSession, tenant_id: int, fy: str, *, mode: Optional[str] = "LIVE") -> List[TradeRecord]:
    start, end = fy_bounds(fy)
    query = select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_not(None),
                                      TradeRecord.exit_time >= start, TradeRecord.exit_time < end)
    if mode:
        query = query.where(TradeRecord.mode == mode)
    return list(await session.scalars(query.order_by(TradeRecord.exit_time)))


def build_report(trades: List[TradeRecord], fy: str, *, mode: Optional[str]) -> Dict:
    totals = {kind: ClassTotals() for kind in CLASSES}
    for trade in trades:
        _accumulate(totals[classify(trade)], trade, classify(trade))
    classes = {}
    for kind, t in totals.items():
        classes[kind] = {
            "trades": t.trades, "winners": t.winners, "gross_pnl": round(t.gross_pnl, 2), "charges": round(t.charges, 2), "net_pnl": round(t.net_pnl, 2),
            "turnover": round(t.turnover, 2), "sell_value": round(t.sell_value, 2), "stt_estimate": round(t.stt_estimate, 2),
            "ctt_estimate": round(t.ctt_estimate, 2), "tds_estimate": round(t.tds_estimate, 2), "taxable_gains": round(t.taxable_gains, 2),
            "tax_estimate": round(t.tax_estimate, 2), "disallowed_losses": round(t.disallowed_losses, 2),
            "top_symbols": sorted(({"symbol": s, "net_pnl": round(v, 2)} for s, v in t.by_symbol.items()), key=lambda r: -abs(r["net_pnl"]))[:10],
            "income_head": {"EQUITY_INTRADAY": "Speculative business income", "FNO": "Non-speculative business income",
                            "CRYPTO": "Income from transfer of VDA (s.115BBH)"}[kind],
        }
    audit_requirement = (classes["EQUITY_INTRADAY"]["turnover"] + classes["FNO"]["turnover"]) > 10_00_00_000   # 10 crore digital-turnover threshold
    return {
        "financial_year": fy, "mode": mode or "ALL", "generated_at": datetime.now(timezone.utc).isoformat(), "trades": len(trades),
        "net_pnl": round(sum(c["net_pnl"] for c in classes.values()), 2), "classes": classes,
        "notes": [DISCLAIMER,
                  "Turnover for intraday equity and F&O follows the ICAI guidance note (absolute profit/loss, plus premium on options sold).",
                  "Charges are what the platform recorded per trade (ESTIMATED until a contract note replaces them); STT/CTT/TDS columns are "
                  "independent estimates at the rates in RATES.",
                  "Tax audit under section 44AB is generally required above 10 crore of turnover when receipts and payments are digital; "
                  + ("your turnover crosses that line." if audit_requirement else "your turnover is below that line.")],
        "rates": RATES,
    }


def to_csv(report: Dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["financial_year", report["financial_year"], "mode", report["mode"], "generated_at", report["generated_at"]])
    writer.writerow([])
    header = ["class", "income_head", "trades", "winners", "gross_pnl", "charges", "net_pnl", "turnover", "sell_value", "stt_estimate",
              "ctt_estimate", "tds_estimate", "taxable_gains", "tax_estimate", "disallowed_losses"]
    writer.writerow(header)
    for kind, c in report["classes"].items():
        writer.writerow([kind] + [c[h] for h in header[1:]])
    writer.writerow([])
    writer.writerow(["class", "symbol", "net_pnl"])
    for kind, c in report["classes"].items():
        for row in c["top_symbols"]:
            writer.writerow([kind, row["symbol"], row["net_pnl"]])
    writer.writerow([])
    for note in report["notes"]:
        writer.writerow(["note", note])
    return out.getvalue()


async def available_years(session: AsyncSession, tenant_id: int) -> List[str]:
    rows = await session.scalars(select(TradeRecord.exit_time).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_not(None)))
    years = {financial_year((t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(IST).date()) for t in rows}
    years.add(financial_year(datetime.now(IST).date()))
    return sorted(years, reverse=True)
