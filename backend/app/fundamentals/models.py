"""Domain models for the Fundamental Analysis & Company Intelligence Engine.

Everything here is either (a) a structured input the caller supplies - a real financial
statement figure, a shareholding snapshot, a corporate action, a qualitative judgement an
analyst enters - or (b) a computed result derived purely from those inputs. Nothing in this
module invents a number: nowhere does an engine synthesize a financial figure that wasn't
supplied. Every input model carries a `source` citation (spec section 40/41) precisely so the
platform never presents a guess as a fact.
"""
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.enums import (
    Bias,
    CapCategory,
    ClaimType,
    DataFreshness,
    EarningsGrowthVisibility,
    FundamentalGrade,
    FusionBias,
    InvestmentHorizon,
    PeriodType,
    QualityLabel,
    RiskLevel,
    TrendLabel,
    ValuationLabel,
)


class SourceCitation(BaseModel):
    """Attached to every meaningful input so a claim can always be traced back (spec 40/43)."""

    source: str
    source_url: Optional[str] = None
    publication_date: Optional[date] = None
    retrieved_date: date = Field(default_factory=lambda: datetime.now(timezone.utc).date())
    confidence: float = 80.0

    def freshness(self, as_of: Optional[date] = None) -> DataFreshness:
        as_of = as_of or datetime.now(timezone.utc).date()
        anchor = self.publication_date or self.retrieved_date
        age_days = (as_of - anchor).days
        if age_days <= 1:
            return DataFreshness.TODAY
        if age_days <= 7:
            return DataFreshness.THIS_WEEK
        if age_days <= 30:
            return DataFreshness.RECENT
        if age_days <= 100:
            return DataFreshness.LAST_QUARTER
        if age_days <= 400:
            return DataFreshness.ANNUAL
        return DataFreshness.HISTORICAL


class CompanyProfile(BaseModel):
    symbol: str
    name: str
    bse_code: Optional[str] = None
    isin: Optional[str] = None
    sector: str
    industry: str
    sub_industry: Optional[str] = None
    market_cap: Optional[float] = None
    cap_category: Optional[CapCategory] = None
    promoter_holding_pct: Optional[float] = None
    fii_holding_pct: Optional[float] = None
    dii_holding_pct: Optional[float] = None
    public_holding_pct: Optional[float] = None
    promoter_pledge_pct: Optional[float] = None
    face_value: Optional[float] = None
    listing_date: Optional[date] = None
    headquarters: Optional[str] = None
    website: Optional[str] = None
    business_segments: List[str] = Field(default_factory=list)
    business_description: Optional[str] = None
    domestic_revenue_pct: Optional[float] = None
    international_revenue_pct: Optional[float] = None
    cyclical: Optional[bool] = None
    source: Optional[SourceCitation] = None


class FinancialPeriod(BaseModel):
    """One reported period's raw statement figures - a quarter or a full year. Ratios are never
    stored here; every engine derives them fresh from these raw numbers so there is exactly one
    place a number can be wrong, and it's the one place a human (or a cited filing) supplied it.
    """

    period_type: PeriodType
    period_label: str  # e.g. "Q2FY25", "FY24"
    period_end_date: date

    revenue: float
    cogs: Optional[float] = None
    ebitda: float
    depreciation: Optional[float] = None
    ebit: Optional[float] = None
    interest_expense: Optional[float] = None
    other_income: float = 0.0
    exceptional_items: float = 0.0
    tax_expense: Optional[float] = None
    pat: float
    eps: Optional[float] = None
    shares_outstanding: Optional[float] = None

    cfo: Optional[float] = None
    cfi: Optional[float] = None
    cff: Optional[float] = None
    capex: Optional[float] = None

    total_debt: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    receivables: Optional[float] = None
    inventory: Optional[float] = None
    payables: Optional[float] = None
    contingent_liabilities: Optional[float] = None
    shareholders_equity: Optional[float] = None
    total_assets: Optional[float] = None

    source: Optional[SourceCitation] = None

    @property
    def net_debt(self) -> Optional[float]:
        if self.total_debt is None:
            return None
        return self.total_debt - (self.cash_and_equivalents or 0.0)

    @property
    def fcf(self) -> Optional[float]:
        if self.cfo is None or self.capex is None:
            return None
        return self.cfo - self.capex


class ShareholdingSnapshot(BaseModel):
    as_of_date: date
    promoter_pct: float
    promoter_pledge_pct: float = 0.0
    fii_pct: Optional[float] = None
    dii_pct: Optional[float] = None
    public_pct: Optional[float] = None
    source: Optional[SourceCitation] = None


class CorporateAction(BaseModel):
    action_type: str  # e.g. DIVIDEND, BUYBACK, ACQUISITION, CAPEX, FUND_RAISING, MANAGEMENT_CHANGE...
    announced_date: date
    headline: str
    description: Optional[str] = None
    expected_revenue_impact: Optional[Bias] = None
    expected_margin_impact: Optional[Bias] = None
    expected_eps_impact: Optional[Bias] = None
    source: Optional[SourceCitation] = None


class QualitativeFactor(BaseModel):
    """A single analyst/user-entered judgement - a moat factor rating, a management-quality
    note, a SWOT bullet. Deliberately separate from the statement-driven engines: the platform
    never invents "the company has pricing power" out of thin air, a human enters it, cited.
    """

    category: str
    label: str
    score: Optional[float] = None  # 0-100, when this factor is scored (e.g. moat factors)
    note: Optional[str] = None
    source: Optional[SourceCitation] = None


# --- Computed result models -------------------------------------------------------------


class ScoredClaim(BaseModel):
    """A single labelled output value with its type (fact/interpretation/estimate/risk, spec
    43) and a plain-English reason, so the UI never presents a computed number as if it were an
    unquestionable fact.
    """

    label: str
    value: Optional[float] = None
    text: Optional[str] = None
    claim_type: ClaimType = ClaimType.INTERPRETATION
    note: str = ""


class BusinessQualityResult(BaseModel):
    score: float
    label: QualityLabel
    factor_breakdown: Dict[str, float]
    notes: List[str] = Field(default_factory=list)


class GrowthAnalysis(BaseModel):
    latest_period: str
    yoy_growth_pct: Optional[float] = None
    cagr_3y_pct: Optional[float] = None
    cagr_5y_pct: Optional[float] = None
    sustainable: Optional[bool] = None
    note: str = ""


class ProfitabilityAnalysis(BaseModel):
    gross_margin_pct: Optional[float] = None
    ebitda_margin_pct: Optional[float] = None
    ebit_margin_pct: Optional[float] = None
    net_margin_pct: Optional[float] = None
    roe_pct: Optional[float] = None
    roce_pct: Optional[float] = None
    roa_pct: Optional[float] = None
    margin_trend: TrendLabel
    revenue_vs_margin_note: str = ""


class EarningsQualityResult(BaseModel):
    cfo_to_pat_ratio: Optional[float] = None
    other_income_pct_of_pat: Optional[float] = None
    exceptional_items_pct_of_pat: Optional[float] = None
    label: QualityLabel
    warnings: List[str] = Field(default_factory=list)


class QuarterlyComparison(BaseModel):
    metric: str
    current: float
    qoq_prior: Optional[float] = None
    yoy_prior: Optional[float] = None
    qoq_change_pct: Optional[float] = None
    yoy_change_pct: Optional[float] = None
    direction: str = "→"  # ↑ / ↓ / →


class QuarterlyResultAnalysis(BaseModel):
    period_label: str
    comparisons: List[QuarterlyComparison]
    overall_quality: Bias


class BalanceSheetAnalysis(BaseModel):
    debt_to_equity: Optional[float] = None
    net_debt_to_ebitda: Optional[float] = None
    interest_coverage: Optional[float] = None
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    debt_risk: RiskLevel
    liquidity_risk: RiskLevel
    notes: List[str] = Field(default_factory=list)


class CashFlowAnalysis(BaseModel):
    cfo: Optional[float] = None
    fcf: Optional[float] = None
    fcf_yield_pct: Optional[float] = None
    cfo_to_pat_ratio: Optional[float] = None
    warning: Optional[str] = None


class ValuationResult(BaseModel):
    pe: Optional[float] = None
    forward_pe: Optional[float] = None
    peg: Optional[float] = None
    pb: Optional[float] = None
    ev_ebitda: Optional[float] = None
    ev_sales: Optional[float] = None
    price_to_sales: Optional[float] = None
    dividend_yield_pct: Optional[float] = None
    fcf_yield_pct: Optional[float] = None
    pe_vs_5y_avg_pct: Optional[float] = None
    pe_vs_peers_pct: Optional[float] = None
    classification: ValuationLabel
    notes: List[str] = Field(default_factory=list)


class DCFAssumptions(BaseModel):
    base_revenue: float
    revenue_growth_pct: List[float]  # one per projection year
    ebitda_margin_pct: float
    tax_rate_pct: float = 25.0
    capex_pct_of_revenue: float = 5.0
    working_capital_pct_of_revenue: float = 2.0
    wacc_pct: float = 11.0
    terminal_growth_pct: float = 4.0
    net_debt: float = 0.0
    shares_outstanding: float = 1.0


class DCFScenarioResult(BaseModel):
    scenario: str
    intrinsic_value_per_share: float
    projected_fcf: List[float]
    terminal_value: float
    enterprise_value: float
    equity_value: float


class DCFResult(BaseModel):
    current_market_price: Optional[float] = None
    base: DCFScenarioResult
    bull: DCFScenarioResult
    bear: DCFScenarioResult
    upside_pct: Optional[float] = None
    margin_of_safety_pct: Optional[float] = None


class RedFlag(BaseModel):
    code: str
    severity: RiskLevel
    description: str
    claim_type: ClaimType = ClaimType.RISK


class SWOTResult(BaseModel):
    strengths: List[str]
    weaknesses: List[str]
    opportunities: List[str]
    threats: List[str]


class ScenarioProjection(BaseModel):
    scenario: str  # Bull / Base / Bear
    revenue: float
    ebitda: float
    pat: float
    eps: Optional[float] = None
    assumptions_note: str = ""


class FundamentalScoreBreakdown(BaseModel):
    component: str
    weight_pct: float
    score_0_100: float
    contribution: float
    note: str = ""


class FundamentalScoreResult(BaseModel):
    score: float
    grade: FundamentalGrade
    breakdown: List[FundamentalScoreBreakdown]


class FusionResult(BaseModel):
    fundamental_score: float
    technical_score: float
    sector_score: Optional[float] = None
    event_risk_score: Optional[float] = None
    final_score: float
    bias: FusionBias
    horizon: InvestmentHorizon
    note: str = ""


class CompanyIntelligenceCard(BaseModel):
    """The one-page summary (spec section 39)."""

    symbol: str
    name: str
    fundamental_score: float
    fundamental_grade: FundamentalGrade
    business_quality: QualityLabel
    earnings_quality: QualityLabel
    balance_sheet_risk: RiskLevel
    valuation: ValuationLabel
    key_risk: Optional[str] = None
    earnings_outlook: EarningsGrowthVisibility
    technical_bias: Optional[str] = None
    final_bias: Optional[FusionBias] = None
