import { request } from "./client";
import type {
  CompanyIntelligenceCard,
  CompanyProfile,
  CorporateAction,
  DCFAssumptions,
  DCFResult,
  FinancialPeriod,
  FundamentalScoreResult,
  FusionResult,
  QualitativeFactor,
  RedFlag,
  SWOTResult,
  ScenarioResult,
  SectorRotationRow,
  ShareholdingSnapshot,
} from "../types/fundamentals";
import type { BalanceSheetAnalysis, CashFlowAnalysis, EarningsQualityResult, GrowthAnalysis, ProfitabilityAnalysis, QuarterlyResultAnalysis, ValuationResult } from "../types/fundamentals";

export const fundamentalsApi = {
  listCompanies: () => request<CompanyProfile[]>("/fundamentals/companies"),
  getCompany: (symbol: string) => request<CompanyProfile>(`/fundamentals/companies/${symbol}`),
  createCompany: (profile: CompanyProfile) =>
    request<CompanyProfile>("/fundamentals/companies", { method: "POST", body: JSON.stringify(profile) }),
  updateCompany: (symbol: string, profile: CompanyProfile) =>
    request<CompanyProfile>(`/fundamentals/companies/${symbol}`, { method: "PUT", body: JSON.stringify(profile) }),

  listFinancials: (symbol: string) => request<FinancialPeriod[]>(`/fundamentals/companies/${symbol}/financials`),
  addFinancialPeriod: (symbol: string, period: FinancialPeriod) =>
    request<FinancialPeriod>(`/fundamentals/companies/${symbol}/financials`, { method: "POST", body: JSON.stringify(period) }),

  listShareholding: (symbol: string) => request<ShareholdingSnapshot[]>(`/fundamentals/companies/${symbol}/shareholding`),
  addShareholding: (symbol: string, snapshot: ShareholdingSnapshot) =>
    request<ShareholdingSnapshot>(`/fundamentals/companies/${symbol}/shareholding`, { method: "POST", body: JSON.stringify(snapshot) }),

  listCorporateActions: (symbol: string) => request<CorporateAction[]>(`/fundamentals/companies/${symbol}/corporate-actions`),
  addCorporateAction: (symbol: string, action: CorporateAction) =>
    request<CorporateAction>(`/fundamentals/companies/${symbol}/corporate-actions`, { method: "POST", body: JSON.stringify(action) }),

  listQualitativeFactors: (symbol: string) => request<QualitativeFactor[]>(`/fundamentals/companies/${symbol}/qualitative-factors`),
  addQualitativeFactor: (symbol: string, factor: QualitativeFactor) =>
    request<QualitativeFactor>(`/fundamentals/companies/${symbol}/qualitative-factors`, { method: "POST", body: JSON.stringify(factor) }),

  growth: (symbol: string) => request<GrowthAnalysis>(`/fundamentals/companies/${symbol}/analysis/growth`),
  profitability: (symbol: string) => request<ProfitabilityAnalysis>(`/fundamentals/companies/${symbol}/analysis/profitability`),
  earningsQuality: (symbol: string) => request<EarningsQualityResult>(`/fundamentals/companies/${symbol}/analysis/earnings-quality`),
  quarterly: (symbol: string) => request<QuarterlyResultAnalysis>(`/fundamentals/companies/${symbol}/analysis/quarterly`),
  balanceSheet: (symbol: string) => request<BalanceSheetAnalysis>(`/fundamentals/companies/${symbol}/analysis/balance-sheet`),
  cashFlow: (symbol: string) => request<CashFlowAnalysis>(`/fundamentals/companies/${symbol}/analysis/cash-flow`),
  businessQuality: (symbol: string) => request<{ score: number; label: string; factor_breakdown: Record<string, number>; notes: string[] }>(`/fundamentals/companies/${symbol}/analysis/business-quality`),
  swot: (symbol: string) => request<SWOTResult>(`/fundamentals/companies/${symbol}/analysis/swot`),
  redFlags: (symbol: string) => request<RedFlag[]>(`/fundamentals/companies/${symbol}/analysis/red-flags`),
  scenario: (symbol: string) => request<ScenarioResult>(`/fundamentals/companies/${symbol}/analysis/scenario`),

  valuation: (
    symbol: string,
    body: { market_price: number; book_value_per_share?: number; historical_pe_avg_5y?: number; peer_pe_avg?: number; dividend_per_share?: number; forward_eps?: number; eps_cagr_pct?: number },
  ) => request<ValuationResult>(`/fundamentals/companies/${symbol}/analysis/valuation`, { method: "POST", body: JSON.stringify(body) }),

  dcf: (symbol: string, assumptions: DCFAssumptions, currentMarketPrice?: number) =>
    request<DCFResult>(
      `/fundamentals/companies/${symbol}/analysis/dcf${currentMarketPrice ? `?current_market_price=${currentMarketPrice}` : ""}`,
      { method: "POST", body: JSON.stringify(assumptions) },
    ),

  fundamentalScore: (
    symbol: string,
    overrides: { sector_outlook_0_100?: number; macro_event_risk_0_100?: number; management_score_0_100?: number },
  ) => request<FundamentalScoreResult>(`/fundamentals/companies/${symbol}/analysis/score`, { method: "POST", body: JSON.stringify(overrides) }),

  fusion: (body: { fundamental_score: number; technical_score: number; technical_direction: string; sector_score?: number; event_risk_score?: number; horizon?: string }) =>
    request<FusionResult>("/fundamentals/fusion", { method: "POST", body: JSON.stringify(body) }),

  card: (symbol: string) => request<CompanyIntelligenceCard>(`/fundamentals/companies/${symbol}/card`),

  screener: (filters: Record<string, unknown>) =>
    request<{ symbol: string; name: string; sector: string }[]>("/fundamentals/screener", { method: "POST", body: JSON.stringify(filters) }),

  sectors: () => request<SectorRotationRow[]>("/fundamentals/sectors"),
};
