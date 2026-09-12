"""API surface for the Fundamental Analysis & Company Intelligence Engine.

Company/financial/shareholding/corporate-action/qualitative-factor data is shared reference
data (like an instrument master), not user-private - reads are open to everyone, writes require
auth so every contribution is attributed (`created_by`). Analysis endpoints are pure GET/POST
computations over whatever has been persisted; they never fabricate a missing input, they
report it as missing (see FundamentalScoreEngine's neutral-50 behavior, and the ValueError a
sub-engine raises when it has nothing to work with - turned into a 422 here).
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_current_user_optional
from app.core.enums import FundamentalGrade, InvestmentHorizon, PeriodType, QualityLabel, RiskLevel, SignalDirection, ValuationLabel
from app.db.models import CompanyRecord, User
from app.db.session import get_session
from app.fundamentals import persistence as db
from app.fundamentals.engines.business_quality import BusinessQualityEngine
from app.fundamentals.engines.peer_comparison import PeerComparisonEngine
from app.fundamentals.engines.pre_earnings import PreEarningsEngine
from app.fundamentals.engines.red_flags import RedFlagEngine
from app.fundamentals.engines.scenario import ScenarioEngine
from app.fundamentals.engines.score import FundamentalScoreEngine, FusionEngine, WEIGHTS, score_from_quality_label, score_from_risk_level, score_from_valuation_label
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
    CompanyIntelligenceCard,
    CompanyProfile,
    CorporateAction,
    DCFAssumptions,
    EarningsCalendarEvent,
    FinancialPeriod,
    PeerMetrics,
    PreEarningsAnalysis,
    QualitativeFactor,
    RedFlag,
    ShareholdingSnapshot,
    SWOTResult,
)

router = APIRouter(prefix="/api/fundamentals", tags=["fundamentals"])


async def _get_company_or_404(session: AsyncSession, symbol: str) -> CompanyRecord:
    record = await db.get_company_by_symbol(session, symbol)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown company symbol '{symbol.upper()}'")
    return record


async def _load_periods(session: AsyncSession, company_id: int) -> List[FinancialPeriod]:
    rows = await db.list_financial_periods(session, company_id)
    return [db.financial_period_to_model(r) for r in rows]


# --- Company profile CRUD --------------------------------------------------------------------


@router.post("/companies", response_model=CompanyProfile, status_code=201)
async def create_company(
    profile: CompanyProfile, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> CompanyProfile:
    existing = await db.get_company_by_symbol(session, profile.symbol)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Company '{profile.symbol.upper()}' already exists - use PUT to update it")

    record = CompanyRecord(symbol=profile.symbol.upper(), created_by=user.id, sector=profile.sector, industry=profile.industry, name=profile.name)
    db.apply_company_fields(record, profile)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return db.company_to_model(record)


@router.get("/companies", response_model=List[CompanyProfile])
async def list_companies(session: AsyncSession = Depends(get_session)) -> List[CompanyProfile]:
    rows = await session.scalars(select(CompanyRecord).order_by(CompanyRecord.symbol))
    return [db.company_to_model(r) for r in rows]


@router.get("/companies/{symbol}", response_model=CompanyProfile)
async def get_company(symbol: str, session: AsyncSession = Depends(get_session)) -> CompanyProfile:
    record = await _get_company_or_404(session, symbol)
    return db.company_to_model(record)


@router.put("/companies/{symbol}", response_model=CompanyProfile)
async def update_company(
    symbol: str, profile: CompanyProfile, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> CompanyProfile:
    record = await _get_company_or_404(session, symbol)
    db.apply_company_fields(record, profile)
    await session.commit()
    await session.refresh(record)
    return db.company_to_model(record)


# --- Financial periods, shareholding, corporate actions, qualitative factors ------------------


@router.post("/companies/{symbol}/financials", response_model=FinancialPeriod, status_code=201)
async def add_financial_period(
    symbol: str, period: FinancialPeriod, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> FinancialPeriod:
    company = await _get_company_or_404(session, symbol)
    record = db.financial_period_from_model(company.id, period, user.id)
    session.add(record)
    await session.commit()
    return period


@router.get("/companies/{symbol}/financials", response_model=List[FinancialPeriod])
async def list_financials(symbol: str, session: AsyncSession = Depends(get_session)) -> List[FinancialPeriod]:
    company = await _get_company_or_404(session, symbol)
    return await _load_periods(session, company.id)


@router.post("/companies/{symbol}/shareholding", response_model=ShareholdingSnapshot, status_code=201)
async def add_shareholding_snapshot(
    symbol: str, snapshot: ShareholdingSnapshot, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> ShareholdingSnapshot:
    company = await _get_company_or_404(session, symbol)
    session.add(db.shareholding_from_model(company.id, snapshot, user.id))
    await session.commit()
    return snapshot


@router.get("/companies/{symbol}/shareholding", response_model=List[ShareholdingSnapshot])
async def list_shareholding(symbol: str, session: AsyncSession = Depends(get_session)) -> List[ShareholdingSnapshot]:
    company = await _get_company_or_404(session, symbol)
    rows = await db.list_shareholding_history(session, company.id)
    return [db.shareholding_to_model(r) for r in rows]


@router.post("/companies/{symbol}/corporate-actions", response_model=CorporateAction, status_code=201)
async def add_corporate_action(
    symbol: str, action: CorporateAction, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> CorporateAction:
    company = await _get_company_or_404(session, symbol)
    session.add(db.corporate_action_from_model(company.id, action, user.id))
    await session.commit()
    return action


@router.get("/companies/{symbol}/corporate-actions", response_model=List[CorporateAction])
async def list_corporate_actions_route(symbol: str, session: AsyncSession = Depends(get_session)) -> List[CorporateAction]:
    company = await _get_company_or_404(session, symbol)
    rows = await db.list_corporate_actions(session, company.id)
    return [db.corporate_action_to_model(r) for r in rows]


@router.post("/companies/{symbol}/qualitative-factors", response_model=QualitativeFactor, status_code=201)
async def add_qualitative_factor(
    symbol: str, factor: QualitativeFactor, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> QualitativeFactor:
    company = await _get_company_or_404(session, symbol)
    session.add(db.qualitative_factor_from_model(company.id, factor, user.id))
    await session.commit()
    return factor


@router.get("/companies/{symbol}/qualitative-factors", response_model=List[QualitativeFactor])
async def list_qualitative_factors_route(symbol: str, session: AsyncSession = Depends(get_session)) -> List[QualitativeFactor]:
    company = await _get_company_or_404(session, symbol)
    rows = await db.list_qualitative_factors(session, company.id)
    return [db.qualitative_factor_to_model(r) for r in rows]


@router.post("/companies/{symbol}/calendar", response_model=EarningsCalendarEvent, status_code=201)
async def add_calendar_event(
    symbol: str, event: EarningsCalendarEvent, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> EarningsCalendarEvent:
    company = await _get_company_or_404(session, symbol)
    session.add(db.calendar_event_from_model(company.id, event, user.id))
    await session.commit()
    return event


@router.get("/companies/{symbol}/calendar", response_model=List[EarningsCalendarEvent])
async def list_calendar_events_route(symbol: str, session: AsyncSession = Depends(get_session)) -> List[EarningsCalendarEvent]:
    company = await _get_company_or_404(session, symbol)
    rows = await db.list_calendar_events(session, company.id)
    return [db.calendar_event_to_model(r) for r in rows]


@router.get("/calendar/upcoming")
async def upcoming_calendar_events(session: AsyncSession = Depends(get_session)) -> List[Dict[str, object]]:
    """Every scheduled event, across every company entered, from today onward - a dashboard-
    ready feed (spec section 30). Sorted soonest first.
    """
    from datetime import date as date_cls

    today = date_cls.today()
    companies = await session.scalars(select(CompanyRecord).order_by(CompanyRecord.symbol))
    upcoming: List[Dict[str, object]] = []
    for company in companies:
        rows = await db.list_calendar_events(session, company.id)
        for row in rows:
            if row.event_date >= today:
                upcoming.append({
                    "symbol": company.symbol, "event_type": row.event_type,
                    "event_date": row.event_date.isoformat(), "description": row.description,
                })
    upcoming.sort(key=lambda e: e["event_date"])
    return upcoming


# --- Analysis endpoints -----------------------------------------------------------------------


def _require_periods(periods: List[FinancialPeriod]) -> None:
    if not periods:
        raise HTTPException(status_code=422, detail="No financial periods have been entered for this company yet - POST at least one to /financials first.")


@router.get("/companies/{symbol}/analysis/growth")
async def analysis_growth(symbol: str, session: AsyncSession = Depends(get_session)):
    company = await _get_company_or_404(session, symbol)
    periods = await _load_periods(session, company.id)
    _require_periods(periods)
    return RevenueAnalysisEngine().analyze(periods)


@router.get("/companies/{symbol}/analysis/profitability")
async def analysis_profitability(symbol: str, session: AsyncSession = Depends(get_session)):
    company = await _get_company_or_404(session, symbol)
    periods = await _load_periods(session, company.id)
    _require_periods(periods)
    return ProfitabilityEngine().analyze(periods)


@router.get("/companies/{symbol}/analysis/earnings-quality")
async def analysis_earnings_quality(symbol: str, session: AsyncSession = Depends(get_session)):
    company = await _get_company_or_404(session, symbol)
    periods = await _load_periods(session, company.id)
    _require_periods(periods)
    return EarningsQualityEngine().analyze(periods[-1])


@router.get("/companies/{symbol}/analysis/quarterly")
async def analysis_quarterly(symbol: str, session: AsyncSession = Depends(get_session)):
    company = await _get_company_or_404(session, symbol)
    periods = await _load_periods(session, company.id)
    quarters = [p for p in periods if p.period_type == PeriodType.QUARTER]
    if not quarters:
        raise HTTPException(status_code=422, detail="No quarterly periods have been entered for this company yet.")
    return QuarterlyComparisonEngine().analyze(periods)


@router.get("/companies/{symbol}/analysis/balance-sheet")
async def analysis_balance_sheet(symbol: str, session: AsyncSession = Depends(get_session)):
    company = await _get_company_or_404(session, symbol)
    periods = await _load_periods(session, company.id)
    _require_periods(periods)
    return BalanceSheetEngine().analyze(periods[-1])


@router.get("/companies/{symbol}/analysis/cash-flow")
async def analysis_cash_flow(symbol: str, session: AsyncSession = Depends(get_session)):
    company = await _get_company_or_404(session, symbol)
    periods = await _load_periods(session, company.id)
    _require_periods(periods)
    return CashFlowEngine().analyze(periods[-1], market_cap=company.market_cap)


@router.get("/companies/{symbol}/analysis/business-quality")
async def analysis_business_quality(symbol: str, session: AsyncSession = Depends(get_session)):
    company = await _get_company_or_404(session, symbol)
    rows = await db.list_qualitative_factors(session, company.id)
    factors = [db.qualitative_factor_to_model(r) for r in rows]
    try:
        return BusinessQualityEngine().analyze(factors)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/companies/{symbol}/analysis/swot", response_model=SWOTResult)
async def analysis_swot(symbol: str, session: AsyncSession = Depends(get_session)) -> SWOTResult:
    company = await _get_company_or_404(session, symbol)
    qual_rows = await db.list_qualitative_factors(session, company.id)
    factors = [db.qualitative_factor_to_model(r) for r in qual_rows]
    periods = await _load_periods(session, company.id)

    profitability = ProfitabilityEngine().analyze(periods) if periods else None
    balance_sheet = BalanceSheetEngine().analyze(periods[-1]) if periods else None
    earnings_quality = EarningsQualityEngine().analyze(periods[-1]) if periods else None
    return SWOTEngine().analyze(factors, profitability=profitability, balance_sheet=balance_sheet, earnings_quality=earnings_quality)


@router.get("/companies/{symbol}/analysis/red-flags", response_model=List[RedFlag])
async def analysis_red_flags(symbol: str, session: AsyncSession = Depends(get_session)) -> List[RedFlag]:
    company = await _get_company_or_404(session, symbol)
    periods = await _load_periods(session, company.id)
    shareholding_rows = await db.list_shareholding_history(session, company.id)
    action_rows = await db.list_corporate_actions(session, company.id)

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
async def analysis_scenario(symbol: str, session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    company = await _get_company_or_404(session, symbol)
    periods = await _load_periods(session, company.id)
    _require_periods(periods)
    return ScenarioEngine().project(periods[-1])


@router.get("/companies/{symbol}/analysis/pre-earnings", response_model=PreEarningsAnalysis)
async def analysis_pre_earnings(symbol: str, session: AsyncSession = Depends(get_session)) -> PreEarningsAnalysis:
    """Pre-earnings read (spec section 31), anchored to the nearest upcoming RESULTS event on
    this company's calendar. 404s if no such event has been entered - there's nothing to be
    "pre-" of otherwise.
    """
    from datetime import date as date_cls

    company = await _get_company_or_404(session, symbol)
    periods = await _load_periods(session, company.id)
    _require_periods(periods)

    calendar_rows = await db.list_calendar_events(session, company.id)
    today = date_cls.today()
    upcoming_results = [
        db.calendar_event_to_model(r) for r in calendar_rows if r.event_type == "RESULTS" and r.event_date >= today
    ]
    if not upcoming_results:
        raise HTTPException(status_code=404, detail="No upcoming RESULTS event on this company's calendar - add one via POST /companies/{symbol}/calendar first.")
    next_event = min(upcoming_results, key=lambda e: e.event_date)

    shareholding_rows = await db.list_shareholding_history(session, company.id)
    action_rows = await db.list_corporate_actions(session, company.id)
    earnings_quality = EarningsQualityEngine().analyze(periods[-1])
    balance_sheet = BalanceSheetEngine().analyze(periods[-1])
    cash_flow = CashFlowEngine().analyze(periods[-1], market_cap=company.market_cap)
    red_flags = RedFlagEngine().analyze(
        earnings_quality=earnings_quality, balance_sheet=balance_sheet, cash_flow=cash_flow,
        shareholding_history=[db.shareholding_to_model(r) for r in shareholding_rows],
        corporate_actions=[db.corporate_action_to_model(r) for r in action_rows],
    )
    return PreEarningsEngine().analyze(periods, upcoming_event_date=next_event.event_date, red_flags=red_flags)


class ValuationRequest(BaseModel):
    market_price: float
    book_value_per_share: Optional[float] = None
    dividend_per_share: Optional[float] = None
    forward_eps: Optional[float] = None
    eps_cagr_pct: Optional[float] = None
    historical_pe_avg_5y: Optional[float] = None
    peer_pe_avg: Optional[float] = None


@router.post("/companies/{symbol}/analysis/valuation")
async def analysis_valuation(symbol: str, request: ValuationRequest, session: AsyncSession = Depends(get_session)):
    company = await _get_company_or_404(session, symbol)
    periods = await _load_periods(session, company.id)
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
async def analysis_dcf(symbol: str, assumptions: DCFAssumptions, current_market_price: Optional[float] = None, session: AsyncSession = Depends(get_session)):
    await _get_company_or_404(session, symbol)
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
async def analysis_fundamental_score(symbol: str, request: FundamentalScoreRequest, session: AsyncSession = Depends(get_session)):
    company = await _get_company_or_404(session, symbol)
    periods = await _load_periods(session, company.id)
    qual_rows = await db.list_qualitative_factors(session, company.id)
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
async def screener(filters: ScreenerFilter, session: AsyncSession = Depends(get_session)) -> List[Dict[str, object]]:
    """Filters over every company that's been entered so far (spec section 45). This can only
    ever screen companies already in this platform's database - it has no access to the full
    NSE/BSE universe without a live data feed (see app/fundamentals/providers/).
    """
    companies = await session.scalars(select(CompanyRecord).order_by(CompanyRecord.symbol))
    results: List[Dict[str, object]] = []
    for company in companies:
        if filters.sector and company.sector.lower() != filters.sector.lower():
            continue
        if filters.min_promoter_holding_pct is not None and (
            company.promoter_holding_pct is None or company.promoter_holding_pct < filters.min_promoter_holding_pct
        ):
            continue

        periods = await _load_periods(session, company.id)
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
async def sector_rotation(session: AsyncSession = Depends(get_session)) -> List[Dict[str, object]]:
    """A lightweight sector ranking (spec section 46) over whatever companies have been entered
    so far: average 3Y revenue CAGR and average ROCE per sector, ranked by CAGR. This is real
    aggregation of persisted data, not a live institutional-flow/momentum feed - with only a
    handful of companies entered, treat it as directional, not a market-wide sector call (that
    needs the full NIFTY universe, which requires a live data feed this platform doesn't have).
    """
    companies = await session.scalars(select(CompanyRecord).order_by(CompanyRecord.symbol))
    by_sector: Dict[str, Dict[str, object]] = {}

    for company in companies:
        periods = await _load_periods(session, company.id)
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
async def sector_peer_comparison(sector: str, session: AsyncSession = Depends(get_session)) -> List[PeerMetrics]:
    """Ranks every company entered under `sector` against each other (spec section 20). Only
    ever compares companies already in this platform - see PeerComparisonEngine's docstring.
    """
    companies = await session.scalars(
        select(CompanyRecord).where(CompanyRecord.sector.ilike(sector)).order_by(CompanyRecord.symbol)
    )
    entries = []
    for company in companies:
        periods = await _load_periods(session, company.id)
        entries.append((company.symbol, company.name, periods))
    return PeerComparisonEngine().compare(entries)


@router.get("/companies/{symbol}/card", response_model=CompanyIntelligenceCard)
async def company_intelligence_card(symbol: str, session: AsyncSession = Depends(get_session)) -> CompanyIntelligenceCard:
    """The one-page summary (spec section 39)."""
    company = await _get_company_or_404(session, symbol)
    periods = await _load_periods(session, company.id)
    _require_periods(periods)
    qual_rows = await db.list_qualitative_factors(session, company.id)
    factors = [db.qualitative_factor_to_model(r) for r in qual_rows]

    earnings_quality = EarningsQualityEngine().analyze(periods[-1])
    balance_sheet = BalanceSheetEngine().analyze(periods[-1])

    try:
        business_quality = BusinessQualityEngine().analyze(factors).label
    except ValueError:
        business_quality = QualityLabel.AVERAGE

    score_request = FundamentalScoreRequest()
    score_result = await analysis_fundamental_score(symbol, score_request, session)

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
