"""Phase P2 / sections 57-61: Indian financial-year tax report for a tenant's closed trades.

The platform is not a tax adviser; this report gives a CA the numbers they ask for, computed the
way the Income Tax Act classifies them. Every statutory rate comes from the platform's one dated
table, `app.execution.india_costs`, at the date of the leg it applies to (the sell leg), so the
report, PAPER fills, backtests and the options builder can never disagree:

* **Equity intraday** (UNDERLYING on NSE/BSE closed the same day) - speculative business income.
  Turnover = sum of absolute profit/loss per trade. STT: `india_costs` EQUITY_INTRADAY sell rate.
* **F&O** (OPTION/FUTURE, NFO/BFO/MCX) - non-speculative business income. Turnover depends on the
  F&O turnover method (`TAX_FNO_TURNOVER_METHOD`, docs/journal/OPEN_QUESTIONS.md JM-1):
  `ABS_PNL` (default) = sum of absolute profit/loss per trade; `ABS_PNL_PLUS_PREMIUM` (the older
  method) also adds the premium received on options sold. STT: `india_costs` OPTION (premium sold)
  or FUTURE (sell value) rate of the sell leg's date; CTT for MCX futures: `india_costs.ctt_sell`.
* **Crypto / VDA** (section 115BBH) - gains taxed flat (`india_costs.vda_rates`), losses cannot be
  set off against anything; TDS on sale consideration under section 194S is withheld by the exchange.

`charges` on each trade (estimated or from a contract note) already include STT/CTT; the STT
figures here are informational estimates so the user can reconcile against the broker's annual
tax P&L statement. FIU-IND reporting applies to VDA service providers (exchanges), not to their
customers, so nothing here files anything.
"""
import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import TradeRecord
from app.execution import india_costs
from app.instruments.registry import get_contract_spec
from app.market_data.calendar import IST, session_family

CLASSES = ("EQUITY_INTRADAY", "FNO", "CRYPTO")
TURNOVER_METHODS: Dict[str, str] = {
    "ABS_PNL": "Absolute profit/loss method: F&O turnover is the sum of the absolute profit or loss of every trade.",
    "ABS_PNL_PLUS_PREMIUM": "Absolute profit/loss plus premium method (older): F&O turnover is the sum of the absolute profit or "
                            "loss of every trade, plus the premium received on options sold.",
}
DISCLAIMER = ("This report is not tax advice. Estimates for reconciliation with your broker's tax P&L statement. Equity intraday and "
              "F&O are business income (speculative / non-speculative); crypto gains are taxed at a flat rate under section 115BBH "
              "with no loss set-off and TDS under section 194S withheld by the exchange. Verify with a chartered accountant before "
              "filing.")


def _pct(fraction: float) -> float:
    return round(fraction * 100, 6)


def rates_on(day: date) -> Dict[str, Dict[str, float]]:
    """The statutory rates the report applies on `day`, in percent, read from `india_costs` (nothing is kept here)."""
    vda = india_costs.vda_rates(day)
    return {
        "EQUITY_INTRADAY": {"stt_sell_pct": _pct(india_costs.rates(day, "EQUITY_INTRADAY")["stt_sell"])},
        "FNO_OPTION": {"stt_sell_pct": _pct(india_costs.rates(day, "OPTION")["stt_sell"])},
        "FNO_FUTURE": {"stt_sell_pct": _pct(india_costs.rates(day, "FUTURE")["stt_sell"])},
        "MCX_FUTURE": {"ctt_sell_pct": _pct(india_costs.ctt_sell(day))},
        "CRYPTO": {"tds_pct": _pct(vda["tds"]), "tax_pct": _pct(vda["tax"])},
    }


def turnover_method(value: Optional[str] = None) -> str:
    """`value` upper-cased if it names a method; None -> the configured default. Anything else is a ValueError."""
    if value is None or not value.strip():
        return config.TAX_FNO_TURNOVER_METHOD
    method = value.strip().upper()
    if method not in TURNOVER_METHODS:
        raise ValueError(f"turnover_method must be one of {', '.join(TURNOVER_METHODS)}")
    return method


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


def _ist_day(when: datetime) -> date:
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(IST).date()


def _sell_leg(trade: TradeRecord) -> Tuple[date, float]:
    """The sell leg of a closed trade: its IST date and value (price x quantity). A LONG sells at the exit, a SHORT (a
    written option, a short future) at the entry - STT/CTT/TDS apply to that leg, at the rates of that leg's date."""
    if trade.direction == "LONG":
        when, price = trade.exit_time or trade.entry_time, trade.exit_price
    else:
        when, price = trade.entry_time, trade.entry_price
    return _ist_day(when), float(price or 0.0) * float(trade.quantity)


def _accumulate(totals: ClassTotals, trade: TradeRecord, kind: str, method: str) -> None:
    net = float(trade.pnl or 0.0)
    charges = float(trade.charges or 0.0)
    gross = net + charges
    totals.trades += 1
    totals.gross_pnl += gross
    totals.charges += charges
    totals.net_pnl += net
    totals.winners += int(net > 0)
    totals.by_symbol[trade.symbol] = totals.by_symbol.get(trade.symbol, 0.0) + net
    day, sell_value = _sell_leg(trade)
    totals.sell_value += sell_value
    if kind == "CRYPTO":
        vda = india_costs.vda_rates(day)
        totals.turnover += sell_value
        totals.tds_estimate += sell_value * vda["tds"]
        if net > 0:
            totals.taxable_gains += net
            totals.tax_estimate += net * vda["tax"]
        else:
            totals.disallowed_losses += -net
        return
    totals.turnover += abs(gross)
    instrument = (trade.instrument_kind or "UNDERLYING").upper()
    if kind == "FNO":
        if instrument == "OPTION":
            premium_sold = sell_value
            if method == "ABS_PNL_PLUS_PREMIUM":
                totals.turnover += premium_sold
            totals.stt_estimate += premium_sold * india_costs.rates(day, "OPTION")["stt_sell"]
        elif session_family(trade.exchange) == "MCX":
            totals.ctt_estimate += sell_value * india_costs.ctt_sell(day)
        else:
            totals.stt_estimate += sell_value * india_costs.rates(day, "FUTURE")["stt_sell"]
    else:
        totals.stt_estimate += sell_value * india_costs.rates(day, "EQUITY_INTRADAY")["stt_sell"]


def _rate_changes(start: date, end: date) -> List[Dict]:
    """Rate changes in `india_costs` that fall inside the financial year [start, end): each trade already uses the rate of
    its own date; this only tells the reader that the year has more than one rate."""
    tables = [*(india_costs.STT_SELL[s] for s in ("OPTION", "FUTURE", "EQUITY_INTRADAY")), india_costs.CTT_SELL_COMMODITY_FUTURE,
              india_costs.VDA_TDS, india_costs.VDA_TAX]
    days = sorted({since for table in tables for since, _ in table if start < since < end})
    changes = []
    for day in days:
        before, after = rates_on(date.fromordinal(day.toordinal() - 1)), rates_on(day)
        for kind, values in after.items():
            for name, pct in values.items():
                if before[kind][name] != pct:
                    changes.append({"from": day.isoformat(), "class": kind, "rate": name, "before_pct": before[kind][name], "pct": pct})
    return changes


async def closed_trades(session: AsyncSession, tenant_id: int, fy: str, *, mode: Optional[str] = "LIVE") -> List[TradeRecord]:
    start, end = fy_bounds(fy)
    query = select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_not(None),
                                      TradeRecord.exit_time >= start, TradeRecord.exit_time < end)
    if mode:
        query = query.where(TradeRecord.mode == mode)
    return list(await session.scalars(query.order_by(TradeRecord.exit_time)))


def build_report(trades: List[TradeRecord], fy: str, *, mode: Optional[str], method: Optional[str] = None) -> Dict:
    """`method`: the F&O turnover method (`TURNOVER_METHODS`); None = the configured `TAX_FNO_TURNOVER_METHOD`."""
    method = turnover_method(method)
    fy_start, fy_end = (_ist_day(t) for t in fy_bounds(fy))
    totals = {kind: ClassTotals() for kind in CLASSES}
    for trade in trades:
        kind = classify(trade)
        _accumulate(totals[kind], trade, kind, method)
    classes: Dict[str, Dict[str, Any]] = {}
    for kind, t in totals.items():
        classes[kind] = {
            "trades": t.trades, "winners": t.winners, "gross_pnl": round(t.gross_pnl, 2), "charges": round(t.charges, 2), "net_pnl": round(t.net_pnl, 2),
            "turnover": round(t.turnover, 2), "sell_value": round(t.sell_value, 2), "stt_estimate": round(t.stt_estimate, 2),
            "ctt_estimate": round(t.ctt_estimate, 2), "tds_estimate": round(t.tds_estimate, 2), "taxable_gains": round(t.taxable_gains, 2),
            "tax_estimate": round(t.tax_estimate, 2), "disallowed_losses": round(t.disallowed_losses, 2),
            "top_symbols": [{"symbol": s, "net_pnl": round(v, 2)} for s, v in sorted(t.by_symbol.items(), key=lambda r: -abs(round(r[1], 2)))][:10],
            "income_head": {"EQUITY_INTRADAY": "Speculative business income", "FNO": "Non-speculative business income",
                            "CRYPTO": "Income from transfer of VDA (s.115BBH)"}[kind],
        }
    audit_requirement = (classes["EQUITY_INTRADAY"]["turnover"] + classes["FNO"]["turnover"]) > 10_00_00_000   # 10 crore digital-turnover threshold
    changes = _rate_changes(fy_start, fy_end)
    rate_note = ("Rates changed during this year: " + "; ".join(f"{c['class']} {c['rate']} {c['before_pct']}% -> {c['pct']}% from {c['from']}"
                                                                 for c in changes) + ". Each trade uses the rate of its own sell date."
                 if changes else "")
    return {
        "financial_year": fy, "mode": mode or "ALL", "generated_at": datetime.now(timezone.utc).isoformat(), "trades": len(trades),
        "net_pnl": round(sum(c["net_pnl"] for c in classes.values()), 2), "classes": classes,
        "turnover_method": method, "turnover_method_label": TURNOVER_METHODS[method],
        "notes": [DISCLAIMER,
                  f"Turnover method: {method}. {TURNOVER_METHODS[method]} Intraday equity turnover is the sum of absolute profit/loss. "
                  "The method is a setting (TAX_FNO_TURNOVER_METHOD); a chartered accountant should confirm which one applies.",
                  "Charges are what the platform recorded per trade (ESTIMATED until a contract note replaces them); STT/CTT/TDS columns are "
                  "independent estimates at the statutory rates of each trade's sell date, from the platform's one dated charges table.",
                  *([rate_note] if rate_note else []),
                  "Tax audit under section 44AB is generally required above 10 crore of turnover when receipts and payments are digital; "
                  + ("your turnover crosses that line." if audit_requirement else "your turnover is below that line.")],
        "rates": rates_on(fy_start), "rates_as_of": fy_start.isoformat(), "rate_changes": changes,
    }


def to_csv(report: Dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["financial_year", report["financial_year"], "mode", report["mode"], "generated_at", report["generated_at"],
                     "turnover_method", report["turnover_method"]])
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
    years = {financial_year(_ist_day(t)) for t in rows if t is not None}
    years.add(financial_year(datetime.now(IST).date()))
    return sorted(years, reverse=True)
