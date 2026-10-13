"""API surface for the Fundamental Analysis & Company Intelligence Engine.

Shared reference data (like an instrument master) is what the platform fetched from a fundamentals
provider (`POST .../refresh`). What a member enters - a company the provider does not have, financial
periods, shareholding, corporate actions, qualitative factors, calendar events, sector metrics - is
that organisation's own: only its members read it (security fix; app/fundamentals/visibility.py), and
a shared company profile changes only through the provider refresh or a platform administrator.
Writes need a trading role of an active organisation (`require_contributor`) and are attributed
(`created_by`); VIEWER and platform SUPPORT accounts read only. An anonymous read sees shared rows only. Analysis endpoints are pure GET/POST
computations over whatever has been persisted; they never fabricate a missing input, they
report it as missing (see FundamentalScoreEngine's neutral-50 behavior, and the ValueError a
sub-engine raises when it has nothing to work with - turned into a 422 here).
"""
from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_contributor
from app.core.enums import UserRole
from app.core.enums import FundamentalGrade, InvestmentHorizon, PeriodType, QualityLabel, SignalDirection, ValuationLabel
from app.db.models import CompanyRecord, User
from app.db.session import get_session
from app.fundamentals import ingest, persistence as db
from app.fundamentals.visibility import check_dates, viewer_tenant
from app.fundamentals.engines.alerts import AlertEngine
from app.fundamentals.engines.business_quality import BusinessQualityEngine
from app.fundamentals.engines.event_impact import EventImpactEngine
from app.fundamentals.engines.peer_comparison import PeerComparisonEngine
from app.fundamentals.engines.post_earnings import PostEarningsEngine
from app.fundamentals.engines.pre_earnings import PreEarningsEngine
from app.fundamentals.engines.red_flags import RedFlagEngine
from app.fundamentals.engines.scenario import ScenarioEngine
from app.fundamentals.engines.score import FundamentalScoreEngine, FusionEngine, score_from_quality_label, score_from_risk_level
from app.fundamentals.engines.sector_specific import SECTOR_METRIC_SPECS, SectorSpecificEngine
from app.fundamentals.engines.statement_analysis import (
    BalanceSheetEngine,
    CashFlowEngine,
    EarningsQualityEngine,
    ProfitabilityEngine,
    QuarterlyComparisonEngine,
    RevenueAnalysisEngine,
)
from app.fundamentals.engines.swot import SWOTEngine
from app.fundamentals.engines.valuation import DCFEngine, ValuationEngine
from app.fundamentals.models import (
    Alert,
    CompanyIntelligenceCard,
    CompanyProfile,
    CorporateAction,
    DCFAssumptions,
    EarningsCalendarEvent,
    EventImpactResult,
    FinalCompanyReport,
    FinancialPeriod,
    PeerMetrics,
    PostEarningsAnalysis,
    PreEarningsAnalysis,
    QualitativeFactor,
    RedFlag,
    SectorMetric,
    SectorSpecificResult,
    ShareholdingSnapshot,
    SourceCitation,
    SWOTResult,
)

router = APIRouter(prefix="/api/fundamentals", tags=["fundamentals"])


async def _get_company_or_404(session: AsyncSession, symbol: str, viewer: Optional[int]) -> CompanyRecord:
    record = await db.get_company_by_symbol(session, symbol, viewer=viewer)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown company symbol '{symbol.upper()}'")
    return record


def _published(source: Optional[SourceCitation]) -> Optional[date]:
    return source.publication_date if source is not None else None


def _check_source(source: Optional[SourceCitation]) -> None:
    """A cited publication date is not in the future."""
    check_dates(publication=_published(source))


def _check_period(period: FinancialPeriod) -> None:
    """A reported period has ended, and a cited publication date is neither in the future nor before the period's end."""
    check_dates(as_of=period.period_end_date, publication=_published(period.source), not_before=period.period_end_date,
                what="period_end_date")


async def _load_periods(session: AsyncSession, company_id: int, viewer: Optional[int]) -> List[FinancialPeriod]:
    rows = await db.list_financial_periods(session, company_id, viewer=viewer)
    return [db.financial_period_to_model(r) for r in rows]


# --- Company profile CRUD --------------------------------------------------------------------


@router.post("/companies", response_model=CompanyProfile, status_code=201, dependencies=[Depends(require_contributor)])
async def create_company(
    profile: CompanyProfile, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> CompanyProfile:
    """A company the platform does not have yet, as this organisation's own (only its members see it; a provider refresh
    later replaces it with the shared profile)."""
    existing = await db.find_company_any(session, profile.symbol)
    if existing is not None:
        mine = existing.tenant_id is None or existing.tenant_id == user.tenant_id
        raise HTTPException(status_code=409, detail=(f"Company '{profile.symbol.upper()}' already exists - use PUT to update it" if mine else
                                                     f"Company '{profile.symbol.upper()}' cannot be added here - refresh it from the provider instead"))
    _check_source(profile.source)
    record = CompanyRecord(symbol=profile.symbol.upper(), created_by=user.id, tenant_id=user.tenant_id, sector=profile.sector,
                           industry=profile.industry, name=profile.name)
    db.apply_company_fields(record, profile)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return db.company_to_model(record)


@router.get("/companies", response_model=List[CompanyProfile])
async def list_companies(session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[CompanyProfile]:
    rows = await db.list_visible_companies(session, viewer=viewer)
    return [db.company_to_model(r) for r in rows]


@router.get("/companies/{symbol}", response_model=CompanyProfile)
async def get_company(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> CompanyProfile:
    record = await _get_company_or_404(session, symbol, viewer)
    return db.company_to_model(record)


@router.put("/companies/{symbol}", response_model=CompanyProfile, dependencies=[Depends(require_contributor)])
async def update_company(
    symbol: str, profile: CompanyProfile, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> CompanyProfile:
    """Edits this organisation's own company profile. A shared profile changes only through the provider refresh (or a
    platform administrator) - one organisation never rewrites what every other organisation reads."""
    record = await _get_company_or_404(session, symbol, user.tenant_id)
    if record.tenant_id is None and user.role != UserRole.SUPER_ADMIN.value:
        raise HTTPException(status_code=403, detail="This company's profile is shared reference data: it changes only through the provider "
                                                    "refresh (POST /companies/{symbol}/refresh)")
    _check_source(profile.source)
    db.apply_company_fields(record, profile)
    await session.commit()
    await session.refresh(record)
    return db.company_to_model(record)


# --- Financial periods, shareholding, corporate actions, qualitative factors ------------------


@router.post("/companies/{symbol}/financials", response_model=FinancialPeriod, status_code=201, dependencies=[Depends(require_contributor)])
async def add_financial_period(
    symbol: str, period: FinancialPeriod, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> FinancialPeriod:
    company = await _get_company_or_404(session, symbol, user.tenant_id)
    company_symbol = company.symbol
    _check_period(period)
    record = db.financial_period_from_model(company.id, period, user.id, tenant_id=user.tenant_id)
    session.add(record)
    try:
        await session.flush()
        from app.fundamentals import pit
        await pit.record_version(session, company.id, period, tenant_id=record.tenant_id)   # U7 LG3: the row's own chain
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Period {period.period_type.value} {period.period_label} already exists for {company_symbol}; import it to update") from exc
    return period


# --- Phase AH: bulk import and provider refresh ----------------------------------------------


class FinancialsImportBody(BaseModel):
    csv: Optional[str] = Field(default=None, max_length=2_000_000, description="header-led CSV/TSV, one period per row")
    periods: Optional[List[FinancialPeriod]] = Field(default=None, max_length=400)


@router.post("/companies/{symbol}/financials/import", dependencies=[Depends(require_contributor)])
async def import_financials(
    symbol: str, body: FinancialsImportBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> dict:
    """Create-or-update this organisation's financial periods from a CSV paste or a JSON list; the natural key is
    (period_type, period_label). Another organisation's figures are never touched."""
    company = await _get_company_or_404(session, symbol, user.tenant_id)
    periods: List[FinancialPeriod] = list(body.periods or [])
    errors: List[str] = []
    columns: List[str] = []
    if body.csv:
        parsed = ingest.parse_financials_csv(body.csv)
        periods.extend(parsed.periods)
        errors.extend(parsed.errors)
        columns = parsed.columns
    if not periods and not errors:
        raise HTTPException(status_code=400, detail="Nothing to import: supply csv text or a periods list")
    company_symbol = company.symbol
    for period in periods:
        _check_period(period)
    summary = await ingest.upsert_financial_periods(session, company, periods, user.id, tenant_id=user.tenant_id)
    await session.commit()
    return {"symbol": company_symbol, **summary.as_dict(), "errors": errors, "columns": columns, "total_rows": len(periods) + len(errors)}


class RefreshBody(BaseModel):
    provider: Optional[str] = Field(default=None, max_length=30)
    profile: bool = True
    shareholding: bool = True
    announcements: bool = True
    announcement_limit: int = Field(default=20, ge=1, le=100)


class BulkRefreshBody(RefreshBody):
    symbols: List[str] = Field(min_length=1, max_length=50)
    create_missing: bool = True


def _provider_or_400(name: Optional[str]):
    try:
        return ingest.provider_for(name)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/providers")
async def list_providers() -> dict:
    return {"providers": ingest.provider_names(), "default": ingest.default_provider_name(),
            "covers": ["profile", "shareholding", "announcements"], "note": "Financial statements are not published as JSON by NSE; import them as CSV."}


@router.post("/companies/{symbol}/refresh", dependencies=[Depends(require_contributor)])
async def refresh_company(
    symbol: str, body: RefreshBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> dict:
    """Pull profile, shareholding and announcements from the fundamentals provider and store what is new - as shared
    reference data (it is the provider's, not this organisation's)."""
    company = await _get_company_or_404(session, symbol, user.tenant_id)
    provider = _provider_or_400(body.provider)
    _, summary = await ingest.refresh_from_provider(session, company, company.symbol, provider, user.id, profile=body.profile,
                                                    shareholding=body.shareholding, announcements=body.announcements, announcement_limit=body.announcement_limit)
    await session.commit()
    if summary.errors and not (summary.profile_changed or summary.shareholding_added or summary.announcements_added):
        raise HTTPException(status_code=502, detail={"message": "provider returned nothing usable", **summary.as_dict()})
    return summary.as_dict()


@router.post("/refresh", dependencies=[Depends(require_contributor)])
async def refresh_many(
    body: BulkRefreshBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> dict:
    """Refresh several symbols in one call, creating the company profile from the provider when it is missing."""
    provider = _provider_or_400(body.provider)
    results = []
    for raw in dict.fromkeys(s.upper().strip() for s in body.symbols if s.strip()):
        # the row whoever owns it (the symbol is unique): another organisation's own profile is replaced by the
        # provider's and becomes shared, or left alone when the provider has no profile (refresh_from_provider)
        company = await db.find_company_any(session, raw)
        _, summary = await ingest.refresh_from_provider(session, company, raw, provider, user.id, profile=body.profile, shareholding=body.shareholding,
                                                        announcements=body.announcements, announcement_limit=body.announcement_limit, create_missing=body.create_missing)
        results.append(summary.as_dict())
    await session.commit()
    return {"provider": getattr(provider, "name", "provider"), "results": results,
            "created": sum(1 for r in results if r["created_company"]), "failed": sum(1 for r in results if r["errors"] and not r["created_company"] and not r["profile_changed"])}


@router.get("/companies/{symbol}/financials", response_model=List[FinancialPeriod])
async def list_financials(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[FinancialPeriod]:
    company = await _get_company_or_404(session, symbol, viewer)
    return await _load_periods(session, company.id, viewer)


@router.post("/companies/{symbol}/shareholding", response_model=ShareholdingSnapshot, status_code=201, dependencies=[Depends(require_contributor)])
async def add_shareholding_snapshot(
    symbol: str, snapshot: ShareholdingSnapshot, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> ShareholdingSnapshot:
    """This organisation's own shareholding pattern (only its members see it): the quarter end is not in the future and a
    cited publication date is not before it."""
    company = await _get_company_or_404(session, symbol, user.tenant_id)
    check_dates(as_of=snapshot.as_of_date, publication=_published(snapshot.source), not_before=snapshot.as_of_date)
    session.add(db.shareholding_from_model(company.id, snapshot, user.id, tenant_id=user.tenant_id))
    await session.commit()
    return snapshot


@router.get("/companies/{symbol}/shareholding", response_model=List[ShareholdingSnapshot])
async def list_shareholding(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[ShareholdingSnapshot]:
    company = await _get_company_or_404(session, symbol, viewer)
    rows = await db.list_shareholding_history(session, company.id, viewer=viewer)
    return [db.shareholding_to_model(r) for r in rows]


@router.post("/companies/{symbol}/corporate-actions", response_model=CorporateAction, status_code=201, dependencies=[Depends(require_contributor)])
async def add_corporate_action(
    symbol: str, action: CorporateAction, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> CorporateAction:
    """This organisation's own corporate action (only its members see it); announced on or before today."""
    company = await _get_company_or_404(session, symbol, user.tenant_id)
    check_dates(as_of=action.announced_date, publication=_published(action.source), what="announced_date")
    session.add(db.corporate_action_from_model(company.id, action, user.id, tenant_id=user.tenant_id))
    await session.commit()
    return action


@router.get("/companies/{symbol}/corporate-actions", response_model=List[CorporateAction])
async def list_corporate_actions_route(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[CorporateAction]:
    company = await _get_company_or_404(session, symbol, viewer)
    rows = await db.list_corporate_actions(session, company.id, viewer=viewer)
    return [db.corporate_action_to_model(r) for r in rows]


@router.post("/companies/{symbol}/qualitative-factors", response_model=QualitativeFactor, status_code=201, dependencies=[Depends(require_contributor)])
async def add_qualitative_factor(
    symbol: str, factor: QualitativeFactor, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> QualitativeFactor:
    """This organisation's own qualitative factor (only its members see it)."""
    company = await _get_company_or_404(session, symbol, user.tenant_id)
    _check_source(factor.source)
    session.add(db.qualitative_factor_from_model(company.id, factor, user.id, tenant_id=user.tenant_id))
    await session.commit()
    return factor


@router.get("/companies/{symbol}/qualitative-factors", response_model=List[QualitativeFactor])
async def list_qualitative_factors_route(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[QualitativeFactor]:
    company = await _get_company_or_404(session, symbol, viewer)
    rows = await db.list_qualitative_factors(session, company.id, viewer=viewer)
    return [db.qualitative_factor_to_model(r) for r in rows]


@router.post("/companies/{symbol}/calendar", response_model=EarningsCalendarEvent, status_code=201, dependencies=[Depends(require_contributor)])
async def add_calendar_event(
    symbol: str, event: EarningsCalendarEvent, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> EarningsCalendarEvent:
    """This organisation's own calendar event (only its members see it). The event may be ahead; its cited publication
    date may not."""
    company = await _get_company_or_404(session, symbol, user.tenant_id)
    _check_source(event.source)
    session.add(db.calendar_event_from_model(company.id, event, user.id, tenant_id=user.tenant_id))
    await session.commit()
    return event


@router.get("/companies/{symbol}/calendar", response_model=List[EarningsCalendarEvent])
async def list_calendar_events_route(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[EarningsCalendarEvent]:
    company = await _get_company_or_404(session, symbol, viewer)
    rows = await db.list_calendar_events(session, company.id, viewer=viewer)
    return [db.calendar_event_to_model(r) for r in rows]


@router.get("/calendar/upcoming")
async def upcoming_calendar_events(session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[Dict[str, object]]:
    """Every scheduled event, across every company entered, from today onward - a dashboard-
    ready feed (spec section 30). Sorted soonest first.
    """
    from datetime import date as date_cls

    today = date_cls.today()
    companies = await db.list_visible_companies(session, viewer=viewer)
    upcoming: List[Dict[str, object]] = []
    for company in companies:
        rows = await db.list_calendar_events(session, company.id, viewer=viewer)
        for row in rows:
            if row.event_date >= today:
                upcoming.append({
                    "symbol": company.symbol, "event_type": row.event_type,
                    "event_date": row.event_date.isoformat(), "description": row.description,
                })
    upcoming.sort(key=lambda e: e["event_date"])
    return upcoming


@router.post("/companies/{symbol}/sector-metrics", response_model=SectorMetric, status_code=201, dependencies=[Depends(require_contributor)])
async def add_sector_metric(
    symbol: str, metric: SectorMetric, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> SectorMetric:
    """This organisation's own sector metric (only its members see it)."""
    company = await _get_company_or_404(session, symbol, user.tenant_id)
    _check_source(metric.source)
    session.add(db.sector_metric_from_model(company.id, metric, user.id, tenant_id=user.tenant_id))
    await session.commit()
    return metric


@router.get("/companies/{symbol}/sector-metrics", response_model=List[SectorMetric])
async def list_sector_metrics_route(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[SectorMetric]:
    company = await _get_company_or_404(session, symbol, viewer)
    rows = await db.list_sector_metrics(session, company.id, viewer=viewer)
    return [db.sector_metric_to_model(r) for r in rows]


@router.get("/sector-metrics/specs")
async def sector_metric_specs() -> Dict[str, Dict[str, Dict[str, object]]]:
    """The known sector keys and metric codes SectorSpecificEngine can classify, with their unit
    and whether higher is better - lets the frontend build the entry form without hardcoding it.
    """
    return {
        sector: {
            code: {"label": spec.label, "unit": spec.unit, "higher_is_better": spec.higher_is_better}
            for code, spec in specs.items()
        }
        for sector, specs in SECTOR_METRIC_SPECS.items()
    }


# --- Analysis endpoints -----------------------------------------------------------------------


def _require_periods(periods: List[FinancialPeriod]) -> None:
    if not periods:
        raise HTTPException(status_code=422, detail="No financial periods have been entered for this company yet - POST at least one to /financials first.")


@router.get("/companies/{symbol}/analysis/growth")
async def analysis_growth(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)):
    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    _require_periods(periods)
    return RevenueAnalysisEngine().analyze(periods)


@router.get("/companies/{symbol}/analysis/profitability")
async def analysis_profitability(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)):
    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    _require_periods(periods)
    return ProfitabilityEngine().analyze(periods)


@router.get("/companies/{symbol}/analysis/earnings-quality")
async def analysis_earnings_quality(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)):
    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    _require_periods(periods)
    return EarningsQualityEngine().analyze(periods[-1])


@router.get("/companies/{symbol}/analysis/quarterly")
async def analysis_quarterly(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)):
    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    quarters = [p for p in periods if p.period_type == PeriodType.QUARTER]
    if not quarters:
        raise HTTPException(status_code=422, detail="No quarterly periods have been entered for this company yet.")
    return QuarterlyComparisonEngine().analyze(periods)


@router.get("/companies/{symbol}/analysis/balance-sheet")
async def analysis_balance_sheet(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)):
    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    _require_periods(periods)
    return BalanceSheetEngine().analyze(periods[-1])


@router.get("/companies/{symbol}/analysis/cash-flow")
async def analysis_cash_flow(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)):
    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    _require_periods(periods)
    return CashFlowEngine().analyze(periods[-1], market_cap=company.market_cap)


@router.get("/companies/{symbol}/analysis/business-quality")
async def analysis_business_quality(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)):
    company = await _get_company_or_404(session, symbol, viewer)
    rows = await db.list_qualitative_factors(session, company.id, viewer=viewer)
    factors = [db.qualitative_factor_to_model(r) for r in rows]
    try:
        return BusinessQualityEngine().analyze(factors)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/companies/{symbol}/analysis/swot", response_model=SWOTResult)
async def analysis_swot(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> SWOTResult:
    company = await _get_company_or_404(session, symbol, viewer)
    qual_rows = await db.list_qualitative_factors(session, company.id, viewer=viewer)
    factors = [db.qualitative_factor_to_model(r) for r in qual_rows]
    periods = await _load_periods(session, company.id, viewer)

    profitability = ProfitabilityEngine().analyze(periods) if periods else None
    balance_sheet = BalanceSheetEngine().analyze(periods[-1]) if periods else None
    earnings_quality = EarningsQualityEngine().analyze(periods[-1]) if periods else None
    return SWOTEngine().analyze(factors, profitability=profitability, balance_sheet=balance_sheet, earnings_quality=earnings_quality)


@router.get("/companies/{symbol}/analysis/red-flags", response_model=List[RedFlag])
async def analysis_red_flags(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[RedFlag]:
    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    shareholding_rows = await db.list_shareholding_history(session, company.id, viewer=viewer)
    action_rows = await db.list_corporate_actions(session, company.id, viewer=viewer)

    earnings_quality = EarningsQualityEngine().analyze(periods[-1]) if periods else None
    balance_sheet = BalanceSheetEngine().analyze(periods[-1]) if periods else None
    cash_flow = CashFlowEngine().analyze(periods[-1], market_cap=company.market_cap) if periods else None
    shareholding = [db.shareholding_to_model(r) for r in shareholding_rows]
    actions = [db.corporate_action_to_model(r) for r in action_rows]

    return RedFlagEngine().analyze(
        earnings_quality=earnings_quality, balance_sheet=balance_sheet, cash_flow=cash_flow,
        shareholding_history=shareholding, corporate_actions=actions,
    )


@router.get("/companies/{symbol}/analysis/scenario")
async def analysis_scenario(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> Dict[str, object]:
    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    _require_periods(periods)
    return ScenarioEngine().project(periods[-1])


@router.get("/companies/{symbol}/analysis/pre-earnings", response_model=PreEarningsAnalysis)
async def analysis_pre_earnings(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> PreEarningsAnalysis:
    """Pre-earnings read (spec section 31), anchored to the nearest upcoming RESULTS event on
    this company's calendar. 404s if no such event has been entered - there's nothing to be
    "pre-" of otherwise.
    """
    from datetime import date as date_cls

    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    _require_periods(periods)

    calendar_rows = await db.list_calendar_events(session, company.id, viewer=viewer)
    today = date_cls.today()
    upcoming_results = [
        db.calendar_event_to_model(r) for r in calendar_rows if r.event_type == "RESULTS" and r.event_date >= today
    ]
    if not upcoming_results:
        raise HTTPException(status_code=404, detail="No upcoming RESULTS event on this company's calendar - add one via POST /companies/{symbol}/calendar first.")
    next_event = min(upcoming_results, key=lambda e: e.event_date)

    shareholding_rows = await db.list_shareholding_history(session, company.id, viewer=viewer)
    action_rows = await db.list_corporate_actions(session, company.id, viewer=viewer)
    earnings_quality = EarningsQualityEngine().analyze(periods[-1])
    balance_sheet = BalanceSheetEngine().analyze(periods[-1])
    cash_flow = CashFlowEngine().analyze(periods[-1], market_cap=company.market_cap)
    red_flags = RedFlagEngine().analyze(
        earnings_quality=earnings_quality, balance_sheet=balance_sheet, cash_flow=cash_flow,
        shareholding_history=[db.shareholding_to_model(r) for r in shareholding_rows],
        corporate_actions=[db.corporate_action_to_model(r) for r in action_rows],
    )
    return PreEarningsEngine().analyze(periods, upcoming_event_date=next_event.event_date, red_flags=red_flags)


@router.get("/companies/{symbol}/analysis/post-earnings", response_model=PostEarningsAnalysis)
async def analysis_post_earnings(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> PostEarningsAnalysis:
    """Post-earnings read - the follow-up to pre-earnings once results are actually in, anchored
    to the most recent past RESULTS event on this company's calendar. 404s if none has been
    entered; 422s if fewer than 3 financial periods exist (a trend needs at least two periods
    before the just-reported one).
    """
    from datetime import date as date_cls

    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)

    calendar_rows = await db.list_calendar_events(session, company.id, viewer=viewer)
    today = date_cls.today()
    past_results = [
        db.calendar_event_to_model(r) for r in calendar_rows if r.event_type == "RESULTS" and r.event_date < today
    ]
    if not past_results:
        raise HTTPException(status_code=404, detail="No past RESULTS event on this company's calendar - add one via POST /companies/{symbol}/calendar first.")
    last_event = max(past_results, key=lambda e: e.event_date)

    try:
        return PostEarningsEngine().analyze(periods, results_event_date=last_event.event_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/companies/{symbol}/analysis/sector-specific", response_model=SectorSpecificResult)
async def analysis_sector_specific(
    symbol: str, sector_key: str, period_label: Optional[str] = None, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant),
) -> SectorSpecificResult:
    """Classifies sector-specific KPIs entered via POST /companies/{symbol}/sector-metrics
    (banking/IT/auto/pharma/oil & gas/cement). `sector_key` is explicit, not inferred from the
    company's free-text sector/industry fields - see GET /sector-metrics/specs for valid keys
    and metric codes. Optionally scope to one `period_label`; otherwise every metric on record
    is classified together.
    """
    company = await _get_company_or_404(session, symbol, viewer)
    rows = await db.list_sector_metrics(session, company.id, viewer=viewer)
    metrics = [db.sector_metric_to_model(r) for r in rows]
    if period_label:
        metrics = [m for m in metrics if m.period_label == period_label]
    try:
        return SectorSpecificEngine().analyze(sector_key, metrics)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/companies/{symbol}/analysis/event-impact", response_model=EventImpactResult)
async def analysis_event_impact(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> EventImpactResult:
    company = await _get_company_or_404(session, symbol, viewer)
    action_rows = await db.list_corporate_actions(session, company.id, viewer=viewer)
    actions = [db.corporate_action_to_model(r) for r in action_rows]
    try:
        return EventImpactEngine().analyze(actions)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/companies/{symbol}/alerts", response_model=List[Alert])
async def company_alerts(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[Alert]:
    """Fundamental Alert Engine feed - re-packages red flags, an imminent earnings date, and
    negative event momentum into one prioritized list. Skips valuation/DCF-derived alerts here
    since those need a market price (not persisted) - use POST /analysis/valuation and
    /analysis/dcf directly for that read.
    """
    from datetime import date as date_cls

    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    action_rows = await db.list_corporate_actions(session, company.id, viewer=viewer)
    actions = [db.corporate_action_to_model(r) for r in action_rows]

    red_flags: List[RedFlag] = []
    if periods:
        earnings_quality = EarningsQualityEngine().analyze(periods[-1])
        balance_sheet = BalanceSheetEngine().analyze(periods[-1])
        cash_flow = CashFlowEngine().analyze(periods[-1], market_cap=company.market_cap)
        shareholding_rows = await db.list_shareholding_history(session, company.id, viewer=viewer)
        red_flags = RedFlagEngine().analyze(
            earnings_quality=earnings_quality, balance_sheet=balance_sheet, cash_flow=cash_flow,
            shareholding_history=[db.shareholding_to_model(r) for r in shareholding_rows],
            corporate_actions=actions,
        )

    event_impact = None
    try:
        event_impact = EventImpactEngine().analyze(actions)
    except ValueError:
        pass

    calendar_rows = await db.list_calendar_events(session, company.id, viewer=viewer)
    today = date_cls.today()
    upcoming_results = [r.event_date for r in calendar_rows if r.event_type == "RESULTS" and r.event_date >= today]
    days_to_next_results = (min(upcoming_results) - today).days if upcoming_results else None

    return AlertEngine().analyze(red_flags=red_flags, days_to_next_results=days_to_next_results, event_impact=event_impact)


class ValuationRequest(BaseModel):
    market_price: float
    book_value_per_share: Optional[float] = None
    dividend_per_share: Optional[float] = None
    forward_eps: Optional[float] = None
    eps_cagr_pct: Optional[float] = None
    historical_pe_avg_5y: Optional[float] = None
    peer_pe_avg: Optional[float] = None


@router.post("/companies/{symbol}/analysis/valuation")
async def analysis_valuation(symbol: str, request: ValuationRequest, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)):
    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    _require_periods(periods)
    latest = periods[-1]
    if not latest.shares_outstanding:
        raise HTTPException(status_code=422, detail="Latest financial period has no shares_outstanding - required for valuation multiples.")
    return ValuationEngine().analyze(
        market_price=request.market_price, shares_outstanding=latest.shares_outstanding, latest=latest,
        book_value_per_share=request.book_value_per_share, net_debt=latest.net_debt,
        dividend_per_share=request.dividend_per_share, forward_eps=request.forward_eps,
        eps_cagr_pct=request.eps_cagr_pct, historical_pe_avg_5y=request.historical_pe_avg_5y, peer_pe_avg=request.peer_pe_avg,
    )


@router.post("/companies/{symbol}/analysis/dcf")
async def analysis_dcf(symbol: str, assumptions: DCFAssumptions, current_market_price: Optional[float] = None, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)):
    await _get_company_or_404(session, symbol, viewer)
    return DCFEngine().run(assumptions, current_market_price=current_market_price)


class FundamentalScoreRequest(BaseModel):
    """Explicit overrides for the two components no engine can compute on its own - sector
    outlook and macro/event risk are calls a human analyst makes, not derived from a filing.
    Everything else is computed live from persisted data when available.
    """

    sector_outlook_0_100: Optional[float] = None
    macro_event_risk_0_100: Optional[float] = None
    management_score_0_100: Optional[float] = None


@router.post("/companies/{symbol}/analysis/score")
async def analysis_fundamental_score(symbol: str, request: FundamentalScoreRequest, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)):
    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    qual_rows = await db.list_qualitative_factors(session, company.id, viewer=viewer)
    factors = [db.qualitative_factor_to_model(r) for r in qual_rows]

    components: Dict[str, float] = {}
    notes: Dict[str, str] = {}

    try:
        bq = BusinessQualityEngine().analyze(factors)
        components["business_quality"] = bq.score
    except ValueError:
        pass

    if periods:
        eq = EarningsQualityEngine().analyze(periods[-1])
        components["earnings_quality"] = score_from_quality_label(eq.label)

        growth = RevenueAnalysisEngine().analyze(periods)
        if growth.cagr_3y_pct is not None:
            components["growth"] = max(0.0, min(100.0, 50.0 + growth.cagr_3y_pct * 2.0))
        elif growth.yoy_growth_pct is not None:
            components["growth"] = max(0.0, min(100.0, 50.0 + growth.yoy_growth_pct * 2.0))

        profitability = ProfitabilityEngine().analyze(periods)
        if profitability.roce_pct is not None:
            components["profitability"] = max(0.0, min(100.0, profitability.roce_pct * 4.0))
        elif profitability.ebitda_margin_pct is not None:
            components["profitability"] = max(0.0, min(100.0, profitability.ebitda_margin_pct * 3.0))

        balance_sheet = BalanceSheetEngine().analyze(periods[-1])
        components["balance_sheet"] = score_from_risk_level(balance_sheet.debt_risk)

        cash_flow = CashFlowEngine().analyze(periods[-1], market_cap=company.market_cap)
        if cash_flow.cfo_to_pat_ratio is not None:
            components["cash_flow"] = max(0.0, min(100.0, cash_flow.cfo_to_pat_ratio * 80.0))

    if request.management_score_0_100 is not None:
        components["management"] = request.management_score_0_100
        notes["management"] = "Supplied directly in the request."
    else:
        mgmt_factors = [f for f in factors if f.category == "MANAGEMENT_QUALITY" and f.score is not None]
        if mgmt_factors:
            components["management"] = sum(f.score for f in mgmt_factors) / len(mgmt_factors)

    if request.sector_outlook_0_100 is not None:
        components["sector_outlook"] = request.sector_outlook_0_100
    if request.macro_event_risk_0_100 is not None:
        components["macro_event_risk"] = request.macro_event_risk_0_100

    return FundamentalScoreEngine().compute(components, notes)


class FusionRequest(BaseModel):
    fundamental_score: float
    technical_score: float
    technical_direction: SignalDirection
    sector_score: Optional[float] = None
    event_risk_score: Optional[float] = None
    horizon: InvestmentHorizon = InvestmentHorizon.POSITIONAL


@router.post("/fusion")
async def fusion_score(request: FusionRequest):
    return FusionEngine().compute(
        fundamental_score=request.fundamental_score, technical_score=request.technical_score,
        technical_direction=request.technical_direction, sector_score=request.sector_score,
        event_risk_score=request.event_risk_score, horizon=request.horizon,
    )


class ScreenerFilter(BaseModel):
    min_roce_pct: Optional[float] = None
    max_debt_to_equity: Optional[float] = None
    min_revenue_cagr_3y_pct: Optional[float] = None
    min_promoter_holding_pct: Optional[float] = None
    max_pe: Optional[float] = None
    sector: Optional[str] = None


@router.post("/screener")
async def screener(filters: ScreenerFilter, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[Dict[str, object]]:
    """Filters over every company that's been entered so far (spec section 45). This can only
    ever screen companies already in this platform's database - it has no access to the full
    NSE/BSE universe without a live data feed (see app/fundamentals/providers/).
    """
    companies = await db.list_visible_companies(session, viewer=viewer)
    results: List[Dict[str, object]] = []
    for company in companies:
        if filters.sector and company.sector.lower() != filters.sector.lower():
            continue
        if filters.min_promoter_holding_pct is not None and (
            company.promoter_holding_pct is None or company.promoter_holding_pct < filters.min_promoter_holding_pct
        ):
            continue

        periods = await _load_periods(session, company.id, viewer)
        if not periods:
            continue
        latest = periods[-1]

        if filters.min_roce_pct is not None:
            capital_employed = (latest.total_assets - latest.current_liabilities) if (latest.total_assets and latest.current_liabilities) else None
            roce = (latest.ebit / capital_employed * 100.0) if (latest.ebit and capital_employed) else None
            if roce is None or roce < filters.min_roce_pct:
                continue
        if filters.max_debt_to_equity is not None:
            de = (latest.total_debt / latest.shareholders_equity) if (latest.total_debt is not None and latest.shareholders_equity) else None
            if de is None or de > filters.max_debt_to_equity:
                continue
        if filters.min_revenue_cagr_3y_pct is not None:
            growth = RevenueAnalysisEngine().analyze(periods)
            if growth.cagr_3y_pct is None or growth.cagr_3y_pct < filters.min_revenue_cagr_3y_pct:
                continue

        results.append({"symbol": company.symbol, "name": company.name, "sector": company.sector})

    return results


@router.get("/sectors")
async def sector_rotation(session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[Dict[str, object]]:
    """A lightweight sector ranking (spec section 46) over whatever companies have been entered
    so far: average 3Y revenue CAGR and average ROCE per sector, ranked by CAGR. This is real
    aggregation of persisted data, not a live institutional-flow/momentum feed - with only a
    handful of companies entered, treat it as directional, not a market-wide sector call (that
    needs the full NIFTY universe, which requires a live data feed this platform doesn't have).
    """
    companies = await db.list_visible_companies(session, viewer=viewer)
    by_sector: Dict[str, Dict[str, object]] = {}

    for company in companies:
        periods = await _load_periods(session, company.id, viewer)
        if not periods:
            continue
        bucket = by_sector.setdefault(company.sector, {"companies": 0, "cagr_values": [], "roce_values": []})
        bucket["companies"] += 1

        try:
            growth = RevenueAnalysisEngine().analyze(periods)
            if growth.cagr_3y_pct is not None:
                bucket["cagr_values"].append(growth.cagr_3y_pct)
        except ValueError:
            pass

        profitability = ProfitabilityEngine().analyze(periods)
        if profitability.roce_pct is not None:
            bucket["roce_values"].append(profitability.roce_pct)

    ranking = []
    for sector, bucket in by_sector.items():
        cagr_values = bucket["cagr_values"]
        roce_values = bucket["roce_values"]
        ranking.append({
            "sector": sector,
            "companies_tracked": bucket["companies"],
            "avg_revenue_cagr_3y_pct": round(sum(cagr_values) / len(cagr_values), 1) if cagr_values else None,
            "avg_roce_pct": round(sum(roce_values) / len(roce_values), 1) if roce_values else None,
        })

    ranking.sort(key=lambda r: (r["avg_revenue_cagr_3y_pct"] is None, -(r["avg_revenue_cagr_3y_pct"] or 0)))
    return ranking


@router.get("/sectors/{sector}/peers", response_model=List[PeerMetrics])
async def sector_peer_comparison(sector: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> List[PeerMetrics]:
    """Ranks every company entered under `sector` against each other (spec section 20). Only
    ever compares companies already in this platform - see PeerComparisonEngine's docstring.
    """
    companies = await db.list_visible_companies(session, viewer=viewer, sector=sector)
    entries = []
    for company in companies:
        periods = await _load_periods(session, company.id, viewer)
        entries.append((company.symbol, company.name, periods))
    return PeerComparisonEngine().compare(entries)


@router.get("/companies/{symbol}/card", response_model=CompanyIntelligenceCard)
async def company_intelligence_card(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> CompanyIntelligenceCard:
    """The one-page summary (spec section 39)."""
    company = await _get_company_or_404(session, symbol, viewer)
    periods = await _load_periods(session, company.id, viewer)
    _require_periods(periods)
    qual_rows = await db.list_qualitative_factors(session, company.id, viewer=viewer)
    factors = [db.qualitative_factor_to_model(r) for r in qual_rows]

    earnings_quality = EarningsQualityEngine().analyze(periods[-1])
    balance_sheet = BalanceSheetEngine().analyze(periods[-1])

    try:
        business_quality = BusinessQualityEngine().analyze(factors).label
    except ValueError:
        business_quality = QualityLabel.AVERAGE

    score_request = FundamentalScoreRequest()
    score_result = await analysis_fundamental_score(symbol, score_request, session, viewer)

    return CompanyIntelligenceCard(
        symbol=company.symbol, name=company.name, fundamental_score=score_result.score,
        fundamental_grade=score_result.grade, business_quality=business_quality,
        earnings_quality=earnings_quality.label, balance_sheet_risk=balance_sheet.debt_risk,
        valuation=ValuationLabel.FAIRLY_VALUED, earnings_outlook=_earnings_outlook_from_score(score_result.grade),
    )


def _earnings_outlook_from_score(grade: FundamentalGrade):
    from app.core.enums import EarningsGrowthVisibility
    mapping = {
        FundamentalGrade.EXCEPTIONAL: EarningsGrowthVisibility.VERY_HIGH,
        FundamentalGrade.STRONG: EarningsGrowthVisibility.HIGH,
        FundamentalGrade.GOOD: EarningsGrowthVisibility.MODERATE,
        FundamentalGrade.AVERAGE: EarningsGrowthVisibility.MODERATE,
        FundamentalGrade.WEAK: EarningsGrowthVisibility.LOW,
        FundamentalGrade.POOR: EarningsGrowthVisibility.DETERIORATING,
    }
    return mapping[grade]


@router.get("/companies/{symbol}/report", response_model=FinalCompanyReport)
async def final_company_report(symbol: str, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)) -> FinalCompanyReport:
    """The Final Company Report - the spec's closing section. Assembles the Intelligence Card,
    SWOT, red flags, alerts, and event impact into one document; it computes nothing new of its
    own, so it inherits every other engine's never-fabricate guarantee. Valuation/DCF are left
    out (they need a market price supplied separately) rather than guessed.
    """
    company = await _get_company_or_404(session, symbol, viewer)
    card = await company_intelligence_card(symbol, session, viewer)

    qual_rows = await db.list_qualitative_factors(session, company.id, viewer=viewer)
    factors = [db.qualitative_factor_to_model(r) for r in qual_rows]
    periods = await _load_periods(session, company.id, viewer)
    profitability = ProfitabilityEngine().analyze(periods) if periods else None
    balance_sheet = BalanceSheetEngine().analyze(periods[-1]) if periods else None
    earnings_quality = EarningsQualityEngine().analyze(periods[-1]) if periods else None
    swot = SWOTEngine().analyze(factors, profitability=profitability, balance_sheet=balance_sheet, earnings_quality=earnings_quality)

    red_flags = await analysis_red_flags(symbol, session, viewer)
    alerts = await company_alerts(symbol, session, viewer)

    action_rows = await db.list_corporate_actions(session, company.id, viewer=viewer)
    actions = [db.corporate_action_to_model(r) for r in action_rows]
    event_impact = None
    try:
        event_impact = EventImpactEngine().analyze(actions)
    except ValueError:
        pass

    return FinalCompanyReport(
        symbol=company.symbol, name=company.name, sector=company.sector, card=card, swot=swot,
        red_flags=red_flags, alerts=alerts, event_impact=event_impact,
        generated_note=(
            "Valuation and DCF are not included here - both require a market price, supplied "
            "separately via POST /analysis/valuation and /analysis/dcf."
        ),
    )
