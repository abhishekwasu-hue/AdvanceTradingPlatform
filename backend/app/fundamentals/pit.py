"""U7 LG3: point-in-time fundamentals - what a screen may know about a company's reported figures as of a time.

`financial_periods` keeps the latest figures of each period, updated in place, so it cannot answer "what was known on
that day". Every write of a period also appends a version to `financial_period_versions` (`record_version`), with the
time the figures became public (`known_from`):
* a period's FIRST version: from 09:15 IST on the day after its cited publication date (a result filed after the close
  is first tradable the next session), when the citation has one that is on or after the period's end and not after
  the ingestion; otherwise from the ingestion itself;
* a LATER version (a restatement, a correction): only from its ingestion - a restated figure never reaches back.
`as_of(versions, t)` gives, per period, the latest version known by `t`; nothing known after `t` is ever returned.

Scope (app/fundamentals/visibility.py): a version carries its period row's `tenant_id` - NULL for the shared chain
(provider-ingested), set for figures an organisation entered, read by that organisation only. Each (period, tenant) has
its own version chain. A reader passes `viewer`; for a period the viewer's own chain (once a version of it is known)
wins over the shared chain - another organisation's figures are never read.
Periods written before this store existed have no version and are never used (re-import them to give them one).
Reference data only - nothing here touches an order.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CompanyRecord, FinancialPeriodVersionRecord
from app.fundamentals.models import FinancialPeriod

FIGURES = ("revenue", "cogs", "ebitda", "depreciation", "ebit", "interest_expense", "other_income", "exceptional_items", "tax_expense",
           "pat", "eps", "shares_outstanding", "cfo", "cfi", "cff", "capex", "total_debt", "cash_and_equivalents", "current_assets",
           "current_liabilities", "receivables", "inventory", "payables", "contingent_liabilities", "shareholders_equity", "total_assets")
SESSION_OPEN_UTC = time(3, 45)          # 09:15 IST


@dataclass(frozen=True)
class Version:
    period_type: str
    period_label: str
    period_end: date
    version: int
    figures: Dict[str, Optional[float]]
    known_from: datetime
    own: bool = False                   # the viewer organisation's own chain (else the shared one)

    def get(self, name: str) -> Optional[float]:
        v = self.figures.get(name)
        return None if v is None else float(v)


def figures_of(period: FinancialPeriod) -> Dict[str, Optional[float]]:
    return {k: (None if getattr(period, k) is None else float(getattr(period, k))) for k in FIGURES}


def known_from(*, version: int, publication_date: Optional[date], period_end: date, ingested_at: datetime) -> datetime:
    """When a version's figures became public (module docstring)."""
    ingested = ingested_at if ingested_at.tzinfo else ingested_at.replace(tzinfo=timezone.utc)
    if version == 1 and publication_date is not None and publication_date >= period_end:
        public = datetime.combine(publication_date + timedelta(days=1), SESSION_OPEN_UTC, tzinfo=timezone.utc)
        if public <= ingested:
            return public
    return ingested


async def record_version(session: AsyncSession, company_id: int, period: FinancialPeriod, *, tenant_id: Optional[int],
                         now: Optional[datetime] = None) -> Optional[FinancialPeriodVersionRecord]:
    """Append a version to the period's chain for `tenant_id` (the period row's own: None = shared) when the figures
    differ from that chain's latest version (or it has none). Does not commit."""
    now = now or datetime.now(timezone.utc)
    figs = figures_of(period)
    latest = await session.scalar(
        select(FinancialPeriodVersionRecord).where(FinancialPeriodVersionRecord.company_id == company_id,
                                                   FinancialPeriodVersionRecord.period_type == period.period_type.value,
                                                   FinancialPeriodVersionRecord.period_label == period.period_label,
                                                   FinancialPeriodVersionRecord.tenant_id.is_not_distinct_from(tenant_id))
        .order_by(FinancialPeriodVersionRecord.version.desc()).limit(1))
    if latest is not None and json.loads(latest.figures_json) == figs and latest.period_end_date == period.period_end_date:
        return None
    version = 1 if latest is None else latest.version + 1
    pub = period.source.publication_date if period.source is not None else None
    row = FinancialPeriodVersionRecord(
        tenant_id=tenant_id, company_id=company_id, period_type=period.period_type.value, period_label=period.period_label, period_end_date=period.period_end_date,
        version=version, figures_json=json.dumps(figs, sort_keys=True), publication_date=pub,
        known_from=known_from(version=version, publication_date=pub, period_end=period.period_end_date, ingested_at=now), ingested_at=now,
        source_json=period.source.model_dump_json() if period.source is not None else None)
    session.add(row)
    await session.flush()
    return row


async def load_versions(session: AsyncSession, symbols: Iterable[str], until: Optional[datetime] = None, *,
                        viewer: Optional[int]) -> Dict[str, List[Version]]:
    """Every version the `viewer` organisation may read (the shared chains and its own; None = shared only) of the
    symbols' periods known by `until` (all of them when None), oldest knowledge first."""
    from app.fundamentals.visibility import visible
    names = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    if not names:
        return {}
    q = (select(CompanyRecord.symbol, FinancialPeriodVersionRecord)
         .join(CompanyRecord, CompanyRecord.id == FinancialPeriodVersionRecord.company_id)
         .where(func.upper(CompanyRecord.symbol).in_(names), visible(CompanyRecord, viewer), visible(FinancialPeriodVersionRecord, viewer)))
    if until is not None:
        q = q.where(FinancialPeriodVersionRecord.known_from <= until)
    out: Dict[str, List[Version]] = {n: [] for n in names}
    for sym, r in (await session.execute(q.order_by(FinancialPeriodVersionRecord.known_from, FinancialPeriodVersionRecord.id))).all():
        kf = r.known_from if r.known_from.tzinfo else r.known_from.replace(tzinfo=timezone.utc)
        own = viewer is not None and r.tenant_id == viewer
        out[sym.upper()].append(Version(r.period_type, r.period_label, r.period_end_date, r.version, json.loads(r.figures_json), kf, own))
    return out


def latest_per_period(versions: Iterable[Any]) -> List[Any]:
    """Per period, the latest version of its chain - the viewer's own chain when one of its versions is among
    `versions`, else the shared chain (versions are numbered per chain, so chains are never compared by number)."""
    best: Dict[Any, Any] = {}
    for v in versions:
        key = (v.period_type, v.period_label)
        cur = best.get(key)
        mine = bool(getattr(v, "own", False))
        if cur is None or (mine, v.version) > (bool(getattr(cur, "own", False)), cur.version):
            best[key] = v
    return list(best.values())


def as_of(versions: Sequence[Version], t: datetime) -> List[Version]:
    """Per period, the latest version known by `t` (the viewer's own chain over the shared one), by period end."""
    return sorted(latest_per_period(v for v in versions if v.known_from <= t), key=lambda v: (v.period_end, v.period_type))


__all__ = ["FIGURES", "Version", "figures_of", "known_from", "record_version", "load_versions", "latest_per_period", "as_of"]
