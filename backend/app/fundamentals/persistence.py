"""Converts between the DB rows (app/db/models.py) and the Pydantic domain models
(app/fundamentals/models.py), and the handful of DB queries the routes need. Kept separate from
routes.py so the analysis engines (which only ever see the Pydantic models) never import
SQLAlchemy.
"""
import json
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CompanyRecord,
    CorporateActionRecord,
    FinancialPeriodRecord,
    QualitativeFactorRecord,
    ShareholdingSnapshotRecord,
)
from app.fundamentals.models import (
    Bias,
    CompanyProfile,
    CorporateAction,
    FinancialPeriod,
    PeriodType,
    QualitativeFactor,
    ShareholdingSnapshot,
    SourceCitation,
)


def _source_to_json(source: Optional[SourceCitation]) -> Optional[str]:
    return source.model_dump_json() if source else None


def _source_from_json(raw: Optional[str]) -> Optional[SourceCitation]:
    return SourceCitation.model_validate_json(raw) if raw else None


def company_to_model(record: CompanyRecord) -> CompanyProfile:
    return CompanyProfile(
        symbol=record.symbol, name=record.name, bse_code=record.bse_code, isin=record.isin,
        sector=record.sector, industry=record.industry, sub_industry=record.sub_industry,
        market_cap=record.market_cap, cap_category=record.cap_category,
        promoter_holding_pct=record.promoter_holding_pct, fii_holding_pct=record.fii_holding_pct,
        dii_holding_pct=record.dii_holding_pct, public_holding_pct=record.public_holding_pct,
        promoter_pledge_pct=record.promoter_pledge_pct, face_value=record.face_value,
        listing_date=record.listing_date, headquarters=record.headquarters, website=record.website,
        business_segments=json.loads(record.business_segments_json), business_description=record.business_description,
        domestic_revenue_pct=record.domestic_revenue_pct, international_revenue_pct=record.international_revenue_pct,
        cyclical=record.cyclical, source=_source_from_json(record.source_json),
    )


def apply_company_fields(record: CompanyRecord, profile: CompanyProfile) -> None:
    record.name = profile.name
    record.bse_code = profile.bse_code
    record.isin = profile.isin
    record.sector = profile.sector
    record.industry = profile.industry
    record.sub_industry = profile.sub_industry
    record.market_cap = profile.market_cap
    record.cap_category = profile.cap_category.value if profile.cap_category else None
    record.promoter_holding_pct = profile.promoter_holding_pct
    record.fii_holding_pct = profile.fii_holding_pct
    record.dii_holding_pct = profile.dii_holding_pct
    record.public_holding_pct = profile.public_holding_pct
    record.promoter_pledge_pct = profile.promoter_pledge_pct
    record.face_value = profile.face_value
    record.listing_date = profile.listing_date
    record.headquarters = profile.headquarters
    record.website = profile.website
    record.business_segments_json = json.dumps(profile.business_segments)
    record.business_description = profile.business_description
    record.domestic_revenue_pct = profile.domestic_revenue_pct
    record.international_revenue_pct = profile.international_revenue_pct
    record.cyclical = profile.cyclical
    record.source_json = _source_to_json(profile.source)


def financial_period_to_model(record: FinancialPeriodRecord) -> FinancialPeriod:
    return FinancialPeriod(
        period_type=PeriodType(record.period_type), period_label=record.period_label,
        period_end_date=record.period_end_date, revenue=record.revenue, cogs=record.cogs,
        ebitda=record.ebitda, depreciation=record.depreciation, ebit=record.ebit,
        interest_expense=record.interest_expense, other_income=record.other_income,
        exceptional_items=record.exceptional_items, tax_expense=record.tax_expense, pat=record.pat,
        eps=record.eps, shares_outstanding=record.shares_outstanding, cfo=record.cfo, cfi=record.cfi,
        cff=record.cff, capex=record.capex, total_debt=record.total_debt,
        cash_and_equivalents=record.cash_and_equivalents, current_assets=record.current_assets,
        current_liabilities=record.current_liabilities, receivables=record.receivables,
        inventory=record.inventory, payables=record.payables,
        contingent_liabilities=record.contingent_liabilities, shareholders_equity=record.shareholders_equity,
        total_assets=record.total_assets, source=_source_from_json(record.source_json),
    )


def financial_period_from_model(company_id: int, period: FinancialPeriod, created_by: Optional[int]) -> FinancialPeriodRecord:
    return FinancialPeriodRecord(
        company_id=company_id, period_type=period.period_type.value, period_label=period.period_label,
        period_end_date=period.period_end_date, revenue=period.revenue, cogs=period.cogs,
        ebitda=period.ebitda, depreciation=period.depreciation, ebit=period.ebit,
        interest_expense=period.interest_expense, other_income=period.other_income,
        exceptional_items=period.exceptional_items, tax_expense=period.tax_expense, pat=period.pat,
        eps=period.eps, shares_outstanding=period.shares_outstanding, cfo=period.cfo, cfi=period.cfi,
        cff=period.cff, capex=period.capex, total_debt=period.total_debt,
        cash_and_equivalents=period.cash_and_equivalents, current_assets=period.current_assets,
        current_liabilities=period.current_liabilities, receivables=period.receivables,
        inventory=period.inventory, payables=period.payables,
        contingent_liabilities=period.contingent_liabilities, shareholders_equity=period.shareholders_equity,
        total_assets=period.total_assets, source_json=_source_to_json(period.source),
        created_by=created_by,
    )


def shareholding_to_model(record: ShareholdingSnapshotRecord) -> ShareholdingSnapshot:
    return ShareholdingSnapshot(
        as_of_date=record.as_of_date, promoter_pct=record.promoter_pct,
        promoter_pledge_pct=record.promoter_pledge_pct, fii_pct=record.fii_pct, dii_pct=record.dii_pct,
        public_pct=record.public_pct, source=_source_from_json(record.source_json),
    )


def shareholding_from_model(company_id: int, snapshot: ShareholdingSnapshot, created_by: Optional[int]) -> ShareholdingSnapshotRecord:
    return ShareholdingSnapshotRecord(
        company_id=company_id, as_of_date=snapshot.as_of_date, promoter_pct=snapshot.promoter_pct,
        promoter_pledge_pct=snapshot.promoter_pledge_pct, fii_pct=snapshot.fii_pct, dii_pct=snapshot.dii_pct,
        public_pct=snapshot.public_pct, source_json=_source_to_json(snapshot.source), created_by=created_by,
    )


def corporate_action_to_model(record: CorporateActionRecord) -> CorporateAction:
    return CorporateAction(
        action_type=record.action_type, announced_date=record.announced_date, headline=record.headline,
        description=record.description,
        expected_revenue_impact=Bias(record.expected_revenue_impact) if record.expected_revenue_impact else None,
        expected_margin_impact=Bias(record.expected_margin_impact) if record.expected_margin_impact else None,
        expected_eps_impact=Bias(record.expected_eps_impact) if record.expected_eps_impact else None,
        source=_source_from_json(record.source_json),
    )


def corporate_action_from_model(company_id: int, action: CorporateAction, created_by: Optional[int]) -> CorporateActionRecord:
    return CorporateActionRecord(
        company_id=company_id, action_type=action.action_type, announced_date=action.announced_date,
        headline=action.headline, description=action.description,
        expected_revenue_impact=action.expected_revenue_impact.value if action.expected_revenue_impact else None,
        expected_margin_impact=action.expected_margin_impact.value if action.expected_margin_impact else None,
        expected_eps_impact=action.expected_eps_impact.value if action.expected_eps_impact else None,
        source_json=_source_to_json(action.source), created_by=created_by,
    )


def qualitative_factor_to_model(record: QualitativeFactorRecord) -> QualitativeFactor:
    return QualitativeFactor(
        category=record.category, label=record.label, score=record.score, note=record.note,
        source=_source_from_json(record.source_json),
    )


def qualitative_factor_from_model(company_id: int, factor: QualitativeFactor, created_by: Optional[int]) -> QualitativeFactorRecord:
    return QualitativeFactorRecord(
        company_id=company_id, category=factor.category, label=factor.label, score=factor.score,
        note=factor.note, source_json=_source_to_json(factor.source), created_by=created_by,
    )


async def get_company_by_symbol(session: AsyncSession, symbol: str) -> Optional[CompanyRecord]:
    return await session.scalar(select(CompanyRecord).where(CompanyRecord.symbol == symbol.upper()))


async def list_financial_periods(session: AsyncSession, company_id: int) -> List[FinancialPeriodRecord]:
    rows = await session.scalars(
        select(FinancialPeriodRecord).where(FinancialPeriodRecord.company_id == company_id).order_by(FinancialPeriodRecord.period_end_date)
    )
    return list(rows)


async def list_shareholding_history(session: AsyncSession, company_id: int) -> List[ShareholdingSnapshotRecord]:
    rows = await session.scalars(
        select(ShareholdingSnapshotRecord).where(ShareholdingSnapshotRecord.company_id == company_id).order_by(ShareholdingSnapshotRecord.as_of_date)
    )
    return list(rows)


async def list_corporate_actions(session: AsyncSession, company_id: int) -> List[CorporateActionRecord]:
    rows = await session.scalars(
        select(CorporateActionRecord).where(CorporateActionRecord.company_id == company_id).order_by(CorporateActionRecord.announced_date.desc())
    )
    return list(rows)


async def list_qualitative_factors(session: AsyncSession, company_id: int) -> List[QualitativeFactorRecord]:
    rows = await session.scalars(
        select(QualitativeFactorRecord).where(QualitativeFactorRecord.company_id == company_id)
    )
    return list(rows)
