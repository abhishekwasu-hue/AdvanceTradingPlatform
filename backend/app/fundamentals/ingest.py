"""Phase AH: getting data INTO the Fundamentals module without typing it period by period.

Until now a company's financials were entered one period at a time through the form, and the
NSE provider (`providers/nse.py`) was built but wired to nothing. This module adds:

* `parse_financials_csv(text)`: a header-led CSV/TSV of financial periods (one row per period,
  columns named after `FinancialPeriod` fields, case-insensitive, extra columns ignored, blanks
  and commas-in-numbers tolerated). Every bad row is reported with its line number; good rows
  still import.
* `upsert_financial_periods(session, company, periods, user_id)`: create or update on the
  natural key (period_type, period_label), so a re-import corrects numbers instead of failing on
  the unique constraint.
* `refresh_from_provider(session, company, provider, user_id, ...)`: pull the profile,
  shareholding pattern and corporate announcements from a `FundamentalDataProvider` and store
  what is new. Provider-supplied `source` citations are kept; a profile refresh never blanks a
  field the provider does not carry (hand-entered description, website, segments survive).
* `provider_for(name)`: the provider registry (only `nse` today; `FUNDAMENTALS_PROVIDER` picks
  the default). Tests inject a provider through `set_provider_factory`.

Financial statements (P&L, balance sheet, cash flow) are NOT available from NSE's public JSON,
so periods always come through the CSV import or the form; the provider covers profile,
shareholding and announcements only. Nothing here fabricates a number.
"""
from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Dict, List, Optional, Tuple

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import PeriodType
from app.db.models import CompanyRecord, FinancialPeriodRecord
from app.fundamentals import persistence as db
from app.fundamentals.models import CompanyProfile, FinancialPeriod
from app.fundamentals.providers.base import FundamentalDataProvider
from app.fundamentals.providers.exceptions import FundamentalDataProviderError

REQUIRED_COLUMNS = ("period_type", "period_label", "period_end_date", "revenue", "ebitda", "pat")
NUMERIC_COLUMNS = tuple(
    name for name, f in FinancialPeriod.model_fields.items()
    if name not in ("period_type", "period_label", "period_end_date", "source")
)
COLUMN_ALIASES = {
    "type": "period_type", "period": "period_label", "label": "period_label", "end_date": "period_end_date",
    "period_end": "period_end_date", "sales": "revenue", "net_profit": "pat", "profit_after_tax": "pat",
    "net_income": "pat", "equity": "shareholders_equity", "shareholders_funds": "shareholders_equity",
    "debt": "total_debt", "cash": "cash_and_equivalents", "operating_cash_flow": "cfo", "capital_expenditure": "capex",
    "shares": "shares_outstanding",
}
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%b %Y", "%Y-%m")


@dataclass
class ParsedFinancials:
    periods: List[FinancialPeriod] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)   # "line N: reason"
    columns: List[str] = field(default_factory=list)


def _normalise_header(raw: str) -> str:
    key = raw.strip().lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "")
    return COLUMN_ALIASES.get(key, key)


def _parse_number(raw: str) -> Optional[float]:
    text = raw.strip().replace(",", "").replace("₹", "")
    if text in ("", "-", "na", "n/a", "null", "none"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    value = float(text)
    return -value if negative else value


def _parse_date(raw: str) -> date:
    text = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if fmt in ("%b %Y", "%Y-%m"):                         # month only -> month end
            nxt = date(parsed.year + (parsed.month // 12), parsed.month % 12 + 1, 1)
            parsed = date.fromordinal(nxt.toordinal() - 1)
        return parsed
    raise ValueError(f"unrecognised date '{raw.strip()}' (use YYYY-MM-DD)")


def _parse_period_type(raw: str) -> PeriodType:
    text = raw.strip().upper()
    aliases = {"Q": "QUARTER", "QUARTERLY": "QUARTER", "QTR": "QUARTER", "A": "ANNUAL", "FY": "ANNUAL", "YEAR": "ANNUAL", "YEARLY": "ANNUAL", "ANNUALLY": "ANNUAL"}
    text = aliases.get(text, text)
    try:
        return PeriodType(text)
    except ValueError as exc:
        raise ValueError(f"period_type must be one of {', '.join(p.value for p in PeriodType)}, got '{raw.strip()}'") from exc


def parse_financials_csv(text: str) -> ParsedFinancials:
    """Header-led CSV or TSV -> financial periods. Rows that fail are reported, the rest are kept."""
    out = ParsedFinancials()
    body = text.strip().lstrip("﻿")
    if not body:
        out.errors.append("empty input")
        return out
    sample = body.splitlines()[0]
    delimiter = "\t" if "\t" in sample else ("," if "," in sample else ";")
    reader = csv.reader(io.StringIO(body), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        out.errors.append("empty input")
        return out
    columns = [_normalise_header(h) for h in header]
    out.columns = columns
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        out.errors.append(f"missing required column(s): {', '.join(missing)}")
        return out
    known = set(FinancialPeriod.model_fields) - {"source"}
    for line_no, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        values: Dict[str, object] = {}
        try:
            for col, cell in zip(columns, row):
                if col not in known:
                    continue
                if col == "period_type":
                    values[col] = _parse_period_type(cell)
                elif col == "period_label":
                    values[col] = cell.strip()
                elif col == "period_end_date":
                    values[col] = _parse_date(cell)
                else:
                    number = _parse_number(cell)
                    if number is not None:
                        values[col] = number
            if not values.get("period_label"):
                raise ValueError("period_label is empty")
            out.periods.append(FinancialPeriod(**values))
        except (ValueError, ValidationError) as exc:
            reason = str(exc).splitlines()[0] if isinstance(exc, ValueError) and not isinstance(exc, ValidationError) else _first_validation_error(exc)
            out.errors.append(f"line {line_no}: {reason}")
    return out


def _first_validation_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err.get("loc", ())) or "row"
    return f"{loc}: {err.get('msg', 'invalid')}"


@dataclass
class UpsertSummary:
    created: int = 0
    updated: int = 0
    labels: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"created": self.created, "updated": self.updated, "labels": self.labels}


async def upsert_financial_periods(session: AsyncSession, company: CompanyRecord, periods: List[FinancialPeriod], user_id: Optional[int],
                                   *, tenant_id: int) -> UpsertSummary:
    """Create or update each period on (period_type, period_label) - this organisation's own rows only (a shared row or
    another organisation's is never overwritten; app/fundamentals/visibility.py). Does not commit."""
    own = [r for r in await db.list_financial_periods(session, company.id, viewer=tenant_id) if r.tenant_id == tenant_id]
    existing = {(r.period_type, r.period_label): r for r in own}
    summary = UpsertSummary()
    for period in periods:
        key = (period.period_type.value, period.period_label)
        fresh = db.financial_period_from_model(company.id, period, user_id, tenant_id=tenant_id)
        record = existing.get(key)
        if record is None:
            session.add(fresh)
            existing[key] = fresh
            summary.created += 1
        else:
            for column in FinancialPeriodRecord.__table__.columns.keys():
                if column in ("id", "company_id", "created_by", "created_at", "tenant_id"):
                    continue
                setattr(record, column, getattr(fresh, column))
            summary.updated += 1
        summary.labels.append(period.period_label)
    return summary


# --- Provider refresh -------------------------------------------------------------------------

_PROVIDER_FACTORIES: Dict[str, Callable[[], FundamentalDataProvider]] = {}


def _nse_factory() -> FundamentalDataProvider:
    from app.fundamentals.providers.nse import NSEProvider
    return NSEProvider()


_PROVIDER_FACTORIES["nse"] = _nse_factory


def set_provider_factory(name: str, factory: Optional[Callable[[], FundamentalDataProvider]]) -> None:
    """Register (or with None, remove) a provider; tests use it to inject a mocked one."""
    if factory is None:
        _PROVIDER_FACTORIES.pop(name, None)
    else:
        _PROVIDER_FACTORIES[name] = factory


def default_provider_name() -> str:
    return os.environ.get("FUNDAMENTALS_PROVIDER", "nse").strip().lower() or "nse"


def provider_names() -> List[str]:
    return sorted(_PROVIDER_FACTORIES)


def provider_for(name: Optional[str] = None) -> FundamentalDataProvider:
    key = (name or default_provider_name()).lower()
    factory = _PROVIDER_FACTORIES.get(key)
    if factory is None:
        raise KeyError(f"unknown fundamentals provider '{key}' (known: {', '.join(provider_names()) or 'none'})")
    return factory()


PROFILE_REFRESH_FIELDS = ("name", "isin", "sector", "industry", "sub_industry", "market_cap", "cap_category", "face_value", "listing_date",
                          "bse_code", "promoter_holding_pct", "fii_holding_pct", "dii_holding_pct", "public_holding_pct", "promoter_pledge_pct")


# The profile fields the provider does not give: an organisation's own entries, cleared when its company becomes shared.
HAND_FIELDS = ("headquarters", "website", "business_description", "domestic_revenue_pct", "international_revenue_pct", "sub_industry",
               "market_cap", "cap_category", "fii_holding_pct", "dii_holding_pct", "public_holding_pct", "promoter_pledge_pct",
               "promoter_holding_pct", "face_value", "listing_date", "bse_code", "isin")


def promote_to_shared(record: CompanyRecord, fetched: CompanyProfile) -> None:
    """An organisation's own company profile becomes the shared one, from the provider's profile only."""
    for name in HAND_FIELDS:
        setattr(record, name, None)
    record.business_segments_json = "[]"
    record.cyclical = None
    record.source_json = None
    record.name = fetched.name or record.symbol
    record.sector = fetched.sector or "Unknown"
    record.industry = fetched.industry or "Unknown"
    merge_profile(record, fetched)
    record.tenant_id = None
    record.created_by = None          # the provider's content now, nobody's: who asked for it is in the audit log


def replace_profile(record: CompanyRecord, fetched: CompanyProfile) -> None:
    """Every profile field becomes the provider's (empty where it gives none) - unlike `merge_profile`, nothing entered
    by hand survives. For a shared profile whose content cannot be trusted (`repair_shared --refetch`)."""
    db.apply_company_fields(record, fetched)
    record.name = fetched.name or record.symbol
    record.sector = fetched.sector or "Unknown"
    record.industry = fetched.industry or "Unknown"


def merge_profile(record: CompanyRecord, fetched: CompanyProfile) -> List[str]:
    """Copy the provider's non-empty fields onto the record; hand-entered fields it lacks survive. Returns the changed columns."""
    changed: List[str] = []
    for name in PROFILE_REFRESH_FIELDS:
        value = getattr(fetched, name, None)
        if value is None or value == "" or value == "Unknown":
            continue
        if name == "cap_category":
            value = value.value if hasattr(value, "value") else value
        if getattr(record, name) != value:
            setattr(record, name, value)
            changed.append(name)
    if fetched.source is not None:
        record.source_json = db._source_to_json(fetched.source)
    return changed


@dataclass
class RefreshSummary:
    symbol: str
    provider: str
    created_company: bool = False
    profile_changed: List[str] = field(default_factory=list)
    shareholding_added: bool = False
    announcements_added: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "provider": self.provider, "created_company": self.created_company, "profile_changed": self.profile_changed,
                "shareholding_added": self.shareholding_added, "announcements_added": self.announcements_added, "errors": self.errors}


SHARED_PROFILE_EVENT = "fundamentals.shared_profile_from_provider"


async def _audit_shared_profile(session: AsyncSession, tenant_id: Optional[int], user_id: Optional[int], company: CompanyRecord, how: str,
                                *, private_author: Optional[int] = None) -> None:
    """Who made a shared profile from the provider (the row itself records no author - its content is the provider's)."""
    from app.audit.log import write_audit_log
    detail: Dict[str, object] = {"company_id": company.id, "symbol": company.symbol, "how": how, "requested_by": user_id}
    if private_author is not None:
        detail["private_profile_author"] = private_author
    await write_audit_log(session, tenant_id, user_id, SHARED_PROFILE_EVENT, json.dumps(detail, sort_keys=True))


async def refresh_from_provider(session: AsyncSession, company: Optional[CompanyRecord], symbol: str, provider: FundamentalDataProvider, user_id: Optional[int], *,
                                profile: bool = True, shareholding: bool = True, announcements: bool = True, announcement_limit: int = 20,
                                create_missing: bool = False, tenant_id: Optional[int]) -> Tuple[Optional[CompanyRecord], RefreshSummary]:
    """Pull what the provider offers and store what is new. Does not commit. Each part fails on its own; the
    others still land, and the summary names what failed. `company` is the shared profile or the caller organisation's
    (`tenant_id`) own - never another organisation's (refused here too)."""
    symbol = symbol.upper().strip()
    summary = RefreshSummary(symbol=symbol, provider=getattr(provider, "name", "provider"))
    if company is not None and company.tenant_id is not None and company.tenant_id != tenant_id:
        summary.errors.append("profile: not this organisation's company profile")              # never another organisation's
        return None, summary
    if company is not None and company.tenant_id is not None:
        shared = await db.find_shared_company(session, symbol)
        if shared is not None:
            # The organisation's own profile of a symbol that now has a shared one: its own rows move onto the shared
            # profile (still its own, still private) and its private profile is removed - its own action, its own rows.
            clash = await db.fold_conflicts(session, company.id, shared.id, tenant_id=company.tenant_id)
            if clash:
                summary.errors.append(f"profile: this organisation already has these periods on the shared profile: {', '.join(clash[:5])}")
                return company, summary
            await db.move_children(session, company.id, shared.id, tenant_id=company.tenant_id)
            await session.delete(company)
            await session.flush()
            company = shared
            summary.profile_changed = ["folded into shared"]
    if company is not None and company.tenant_id is not None:
        # The organisation's own profile and no shared one yet: the provider's profile replaces it and it becomes shared
        # reference data (its hand-entered fields are cleared - they were that organisation's, never shown to others).
        # Without the provider's profile nothing is written to a company that is not shared.
        try:
            fetched = await provider.get_company_profile(symbol)
        except FundamentalDataProviderError as exc:
            summary.errors.append(f"profile: {exc} (an organisation's own company profile becomes shared only from the provider)")
            return company, summary
        before = {name: getattr(company, name) for name in PROFILE_REFRESH_FIELDS}
        member = company.created_by
        promote_to_shared(company, fetched)
        summary.profile_changed = [name for name in PROFILE_REFRESH_FIELDS if getattr(company, name) != before[name]] + ["shared"]
        await _audit_shared_profile(session, tenant_id, user_id, company, "promoted", private_author=member)
        profile = False
    if company is None:
        if not create_missing:
            summary.errors.append("no company profile stored; create one or pass create_missing")
            return None, summary
        try:
            fetched = await provider.get_company_profile(symbol)
        except FundamentalDataProviderError as exc:
            summary.errors.append(f"profile: {exc}")
            return None, summary
        # created_by stays empty: the content is the provider's, not the caller's (a member-authored shared profile is
        # what `repair_shared` treats as suspect) - who asked for it is in the audit log
        company = CompanyRecord(symbol=symbol, created_by=None, name=fetched.name or symbol, sector=fetched.sector or "Unknown", industry=fetched.industry or "Unknown")
        db.apply_company_fields(company, fetched)
        session.add(company)
        await session.flush()
        await _audit_shared_profile(session, tenant_id, user_id, company, "created")
        summary.created_company = True
        summary.profile_changed = ["created"]
        profile = False
    if profile:
        try:
            fetched = await provider.get_company_profile(symbol)
            summary.profile_changed += merge_profile(company, fetched)          # after a fold: "folded into shared" stays first
        except FundamentalDataProviderError as exc:
            summary.errors.append(f"profile: {exc}")
    if shareholding:
        try:
            snapshot = await provider.get_shareholding_pattern(symbol)
            known_dates = {r.as_of_date for r in await db.list_shareholding_history(session, company.id, viewer=None)}   # shared rows
            if snapshot.as_of_date not in known_dates and snapshot.promoter_pct > 0:
                session.add(db.shareholding_from_model(company.id, snapshot, user_id, tenant_id=None))   # provider data: shared
                summary.shareholding_added = True
                if company.promoter_holding_pct != snapshot.promoter_pct:
                    company.promoter_holding_pct = snapshot.promoter_pct
                    summary.profile_changed.append("promoter_holding_pct")
        except FundamentalDataProviderError as exc:
            summary.errors.append(f"shareholding: {exc}")
    if announcements:
        try:
            actions = await provider.get_corporate_announcements(symbol, limit=announcement_limit)
            known = {(r.announced_date, r.headline) for r in await db.list_corporate_actions(session, company.id, viewer=None)}   # shared
            for action in actions:
                key = (action.announced_date, action.headline)
                if key in known:
                    continue
                session.add(db.corporate_action_from_model(company.id, action, user_id, tenant_id=None))       # provider data: shared
                known.add(key)
                summary.announcements_added += 1
        except FundamentalDataProviderError as exc:
            summary.errors.append(f"announcements: {exc}")
    return company, summary
