"""Phase AG: fill the Factor Lab's value/quality inputs from the Fundamentals module.

`raw_factors` (factors.py) needs `pe, pb, roe_pct, debt_to_equity, earnings_growth_pct` per symbol
to compute the value and quality factors. Until now the caller had to supply them by hand, so the
Factor Lab page never had them. This module derives them from the stored `CompanyRecord` +
`FinancialPeriodRecord` rows (shared reference data, no tenant scope) with the same arithmetic the
fundamentals engines use:

* pe   = close / eps               (eps = latest.eps, else pat / shares)
* pb   = close / (equity / shares)  (book value per share)
* roe  = pat / equity * 100         (ProfitabilityEngine)
* d/e  = total_debt / equity        (BalanceSheetEngine)
* earnings growth = PAT change vs the previous period of the same type, in percent

"Latest" is the newest ANNUAL period (fallback: newest of any type) and `close` is the last close of
the candles the caller supplied (fallback: market_cap / shares when the company profile carries a
market cap). Anything that cannot be derived is left out, so the factor model treats it as missing
exactly as before; nothing is guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import PeriodType
from app.fundamentals import persistence as db
from app.fundamentals.models import FinancialPeriod

FIELDS = ("pe", "pb", "roe_pct", "debt_to_equity", "earnings_growth_pct")


@dataclass
class FundamentalsFill:
    symbol: str
    period: Optional[str] = None                       # label of the period the numbers come from
    values: Dict[str, float] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)   # fields that could not be derived and why

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "period": self.period, "values": self.values, "missing": self.missing}


def _latest_two(periods: List[FinancialPeriod]) -> tuple[Optional[FinancialPeriod], Optional[FinancialPeriod]]:
    ordered = sorted(periods, key=lambda p: p.period_end_date)
    annual = [p for p in ordered if p.period_type == PeriodType.ANNUAL]
    pool = annual or ordered
    if not pool:
        return None, None
    latest = pool[-1]
    same_type = [p for p in ordered if p.period_type == latest.period_type and p.period_end_date < latest.period_end_date]
    return latest, (same_type[-1] if same_type else None)


def derive(symbol: str, latest: Optional[FinancialPeriod], prior: Optional[FinancialPeriod], *, close: Optional[float],
           shares_outstanding: Optional[float], market_cap: Optional[float]) -> FundamentalsFill:
    """Pure derivation; the async loader below feeds it. Exposed for tests."""
    fill = FundamentalsFill(symbol=symbol)
    if latest is None:
        fill.missing.append("no financial periods stored")
        return fill
    fill.period = latest.period_label
    shares = latest.shares_outstanding or shares_outstanding
    price = close
    if price is None and market_cap and shares:
        price = market_cap / shares
    equity = latest.shareholders_equity

    eps = latest.eps if latest.eps is not None else ((latest.pat / shares) if (latest.pat is not None and shares) else None)
    if price is not None and eps and eps > 0:
        fill.values["pe"] = round(price / eps, 4)
    else:
        fill.missing.append("pe: needs a close and a positive EPS (or PAT and shares)")

    if price is not None and equity and equity > 0 and shares:
        fill.values["pb"] = round(price / (equity / shares), 4)
    else:
        fill.missing.append("pb: needs a close, positive shareholders' equity and shares outstanding")

    if latest.pat is not None and equity:
        fill.values["roe_pct"] = round(latest.pat / equity * 100.0, 4)
    else:
        fill.missing.append("roe_pct: needs PAT and shareholders' equity")

    if latest.total_debt is not None and equity:
        fill.values["debt_to_equity"] = round(latest.total_debt / equity, 4)
    else:
        fill.missing.append("debt_to_equity: needs total debt and shareholders' equity")

    if prior is not None and latest.pat is not None and prior.pat:
        fill.values["earnings_growth_pct"] = round((latest.pat - prior.pat) / abs(prior.pat) * 100.0, 4)
    else:
        fill.missing.append("earnings_growth_pct: needs PAT for two consecutive periods of the same type")
    return fill


async def fundamentals_for(session: AsyncSession, symbols: Iterable[str], closes: Optional[Dict[str, float]] = None) -> Dict[str, FundamentalsFill]:
    """Derive the factor-model fundamentals for each symbol that has a company profile. Symbols without one are omitted."""
    closes = closes or {}
    out: Dict[str, FundamentalsFill] = {}
    for raw in symbols:
        symbol = raw.upper().strip()
        if symbol in out:
            continue
        company = await db.get_company_by_symbol(session, symbol)
        if company is None:
            continue
        periods = [db.financial_period_to_model(r) for r in await db.list_financial_periods(session, company.id)]
        latest, prior = _latest_two(periods)
        out[symbol] = derive(symbol, latest, prior, close=closes.get(symbol), shares_outstanding=None, market_cap=company.market_cap)
    return out
