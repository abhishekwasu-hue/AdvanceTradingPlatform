// Types for the Fundamental Analysis & Company Intelligence Engine.
// Mirrors backend/app/fundamentals/models.py - see that file for the authoritative shapes.

export interface SourceCitation {
  source: string;
  source_url?: string | null;
  publication_date?: string | null;
  retrieved_date: string;
  confidence: number;
}

export type CapCategory = "LARGE_CAP" | "MID_CAP" | "SMALL_CAP" | "MICRO_CAP";

export interface CompanyProfile {
  symbol: string;
  name: string;
  bse_code?: string | null;
  isin?: string | null;
  sector: string;
  industry: string;
  sub_industry?: string | null;
  market_cap?: number | null;
  cap_category?: CapCategory | null;
  promoter_holding_pct?: number | null;
  fii_holding_pct?: number | null;
  dii_holding_pct?: number | null;
  public_holding_pct?: number | null;
  promoter_pledge_pct?: number | null;
  face_value?: number | null;
  listing_date?: string | null;
  headquarters?: string | null;
  website?: string | null;
  business_segments: string[];
  business_description?: string | null;
  domestic_revenue_pct?: number | null;
  international_revenue_pct?: number | null;
  cyclical?: boolean | null;
  source?: SourceCitation | null;
}

export type PeriodType = "QUARTER" | "ANNUAL" | "TTM";

export interface FinancialPeriod {
  period_type: PeriodType;
  period_label: string;
  period_end_date: string;
  revenue: number;
  cogs?: number | null;
  ebitda: number;
  depreciation?: number | null;
  ebit?: number | null;
  interest_expense?: number | null;
  other_income: number;
  exceptional_items: number;
  tax_expense?: number | null;
  pat: number;
  eps?: number | null;
  shares_outstanding?: number | null;
  cfo?: number | null;
  cfi?: number | null;
  cff?: number | null;
  capex?: number | null;
  total_debt?: number | null;
  cash_and_equivalents?: number | null;
  current_assets?: number | null;
  current_liabilities?: number | null;
  receivables?: number | null;
  inventory?: number | null;
  payables?: number | null;
  contingent_liabilities?: number | null;
  shareholders_equity?: number | null;
  total_assets?: number | null;
  source?: SourceCitation | null;
}

export interface ShareholdingSnapshot {
  as_of_date: string;
  promoter_pct: number;
  promoter_pledge_pct: number;
  fii_pct?: number | null;
  dii_pct?: number | null;
  public_pct?: number | null;
  source?: SourceCitation | null;
}

export interface CorporateAction {
  action_type: string;
  announced_date: string;
  headline: string;
  description?: string | null;
  expected_revenue_impact?: "Bullish" | "Neutral" | "Bearish" | null;
  expected_margin_impact?: "Bullish" | "Neutral" | "Bearish" | null;
  expected_eps_impact?: "Bullish" | "Neutral" | "Bearish" | null;
  source?: SourceCitation | null;
}

export interface QualitativeFactor {
  category: string;
  label: string;
  score?: number | null;
  note?: string | null;
  source?: SourceCitation | null;
}

export interface GrowthAnalysis {
  latest_period: string;
  yoy_growth_pct?: number | null;
  cagr_3y_pct?: number | null;
  cagr_5y_pct?: number | null;
  sustainable?: boolean | null;
  note: string;
}

export interface ProfitabilityAnalysis {
  gross_margin_pct?: number | null;
  ebitda_margin_pct?: number | null;
  ebit_margin_pct?: number | null;
  net_margin_pct?: number | null;
  roe_pct?: number | null;
  roce_pct?: number | null;
  roa_pct?: number | null;
  margin_trend: "Improving" | "Stable" | "Deteriorating" | "Highly Volatile";
  revenue_vs_margin_note: string;
}

export interface EarningsQualityResult {
  cfo_to_pat_ratio?: number | null;
  other_income_pct_of_pat?: number | null;
  exceptional_items_pct_of_pat?: number | null;
  label: "Strong" | "Good" | "Average" | "Weak" | "Deteriorating";
  warnings: string[];
}

export interface QuarterlyComparison {
  metric: string;
  current: number;
  qoq_prior?: number | null;
  yoy_prior?: number | null;
  qoq_change_pct?: number | null;
  yoy_change_pct?: number | null;
  direction: string;
}

export interface QuarterlyResultAnalysis {
  period_label: string;
  comparisons: QuarterlyComparison[];
  overall_quality: "Bullish" | "Neutral" | "Bearish";
}

export interface BalanceSheetAnalysis {
  debt_to_equity?: number | null;
  net_debt_to_ebitda?: number | null;
  interest_coverage?: number | null;
  current_ratio?: number | null;
  quick_ratio?: number | null;
  debt_risk: "Low" | "Medium" | "High" | "Extreme";
  liquidity_risk: "Low" | "Medium" | "High" | "Extreme";
  notes: string[];
}

export interface CashFlowAnalysis {
  cfo?: number | null;
  fcf?: number | null;
  fcf_yield_pct?: number | null;
  cfo_to_pat_ratio?: number | null;
  warning?: string | null;
}

export interface ValuationResult {
  pe?: number | null;
  forward_pe?: number | null;
  peg?: number | null;
  pb?: number | null;
  ev_ebitda?: number | null;
  ev_sales?: number | null;
  price_to_sales?: number | null;
  dividend_yield_pct?: number | null;
  fcf_yield_pct?: number | null;
  pe_vs_5y_avg_pct?: number | null;
  pe_vs_peers_pct?: number | null;
  classification: "Deeply Undervalued" | "Undervalued" | "Fairly Valued" | "Expensive" | "Extremely Expensive";
  notes: string[];
}

export interface DCFAssumptions {
  base_revenue: number;
  revenue_growth_pct: number[];
  ebitda_margin_pct: number;
  tax_rate_pct: number;
  capex_pct_of_revenue: number;
  working_capital_pct_of_revenue: number;
  wacc_pct: number;
  terminal_growth_pct: number;
  net_debt: number;
  shares_outstanding: number;
}

export interface DCFScenarioResult {
  scenario: string;
  intrinsic_value_per_share: number;
  projected_fcf: number[];
  terminal_value: number;
  enterprise_value: number;
  equity_value: number;
}

export interface DCFResult {
  current_market_price?: number | null;
  base: DCFScenarioResult;
  bull: DCFScenarioResult;
  bear: DCFScenarioResult;
  upside_pct?: number | null;
  margin_of_safety_pct?: number | null;
}

export interface RedFlag {
  code: string;
  severity: "Low" | "Medium" | "High" | "Extreme";
  description: string;
}

export interface SWOTResult {
  strengths: string[];
  weaknesses: string[];
  opportunities: string[];
  threats: string[];
}

export interface ScenarioProjection {
  scenario: string;
  revenue: number;
  ebitda: number;
  pat: number;
  eps?: number | null;
  assumptions_note: string;
}

export interface ScenarioResult {
  bull: ScenarioProjection;
  base: ScenarioProjection;
  bear: ScenarioProjection;
}

export interface FundamentalScoreBreakdown {
  component: string;
  weight_pct: number;
  score_0_100: number;
  contribution: number;
  note: string;
}

export interface FundamentalScoreResult {
  score: number;
  grade: "Exceptional" | "Strong" | "Good" | "Average" | "Weak" | "Poor";
  breakdown: FundamentalScoreBreakdown[];
}

export interface FusionResult {
  fundamental_score: number;
  technical_score: number;
  sector_score?: number | null;
  event_risk_score?: number | null;
  final_score: number;
  bias: "A1 LONG BIAS" | "A1 SHORT BIAS" | "WATCHLIST" | "NO TRADE" | "CAUTION";
  horizon: "Intraday" | "Swing" | "Positional" | "Long Term";
  note: string;
}

export interface CompanyIntelligenceCard {
  symbol: string;
  name: string;
  fundamental_score: number;
  fundamental_grade: string;
  business_quality: string;
  earnings_quality: string;
  balance_sheet_risk: string;
  valuation: string;
  key_risk?: string | null;
  earnings_outlook: string;
  technical_bias?: string | null;
  final_bias?: string | null;
}

export interface SectorRotationRow {
  sector: string;
  companies_tracked: number;
  avg_revenue_cagr_3y_pct: number | null;
  avg_roce_pct: number | null;
}

export function defaultCompanyProfile(): CompanyProfile {
  return { symbol: "", name: "", sector: "", industry: "", business_segments: [] };
}

export function defaultFinancialPeriod(): FinancialPeriod {
  return {
    period_type: "ANNUAL", period_label: "", period_end_date: new Date().toISOString().slice(0, 10),
    revenue: 0, ebitda: 0, pat: 0, other_income: 0, exceptional_items: 0,
  };
}
