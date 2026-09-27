import { useEffect, useMemo, useState } from "react";
import { fundamentalsApi } from "../api/fundamentalsClient";
import { useAuth } from "../auth/AuthContext";
import { Card, StatTile, Disclaimer } from "../components/ui";
import {
  defaultCalendarEvent,
  defaultCompanyProfile,
  defaultFinancialPeriod,
  defaultSectorMetric,
  type Alert,
  type BalanceSheetAnalysis,
  type CashFlowAnalysis,
  type CompanyIntelligenceCard,
  type CompanyProfile,
  type DCFResult,
  type EarningsCalendarEvent,
  type EarningsQualityResult,
  type EventImpactResult,
  type FinalCompanyReport,
  type FinancialPeriod,
  type FundamentalScoreResult,
  type FusionResult,
  type GrowthAnalysis,
  type PeerMetrics,
  type PostEarningsAnalysis,
  type PreEarningsAnalysis,
  type ProfitabilityAnalysis,
  type QuarterlyResultAnalysis,
  type RedFlag,
  type SWOTResult,
  type ScenarioResult,
  type SectorMetric,
  type SectorMetricSpecs,
  type SectorRotationRow,
  type SectorSpecificResult,
  type UpcomingCalendarEvent,
  type ValuationResult,
} from "../types/fundamentals";

const TABS = [
  "Profile", "Financials", "Analysis", "Valuation & DCF", "Quality & Risk", "Score & Fusion",
  "Intelligence Card", "Screener & Sectors", "Calendar & Pre-Earnings", "Peer Comparison",
  "Sector Metrics", "Alerts & Report",
] as const;
type Tab = (typeof TABS)[number];

function toneForRisk(risk?: string) {
  if (risk === "Low") return "up" as const;
  if (risk === "High" || risk === "Extreme") return "down" as const;
  return "default" as const;
}

function toneForNumber(n?: number | null) {
  if (n == null) return "default" as const;
  return n >= 0 ? ("up" as const) : ("down" as const);
}

export default function FundamentalsPage() {
  const { user } = useAuth();
  const [companies, setCompanies] = useState<CompanyProfile[]>([]);
  const [symbol, setSymbol] = useState<string>("");
  const [tab, setTab] = useState<Tab>("Profile");
  const [error, setError] = useState<string | null>(null);

  const [newCompany, setNewCompany] = useState<CompanyProfile>(defaultCompanyProfile());
  const [showNewCompanyForm, setShowNewCompanyForm] = useState(false);

  const [periods, setPeriods] = useState<FinancialPeriod[]>([]);
  const [newPeriod, setNewPeriod] = useState<FinancialPeriod>(defaultFinancialPeriod());

  const [growth, setGrowth] = useState<GrowthAnalysis | null>(null);
  const [profitability, setProfitability] = useState<ProfitabilityAnalysis | null>(null);
  const [earningsQuality, setEarningsQuality] = useState<EarningsQualityResult | null>(null);
  const [quarterly, setQuarterly] = useState<QuarterlyResultAnalysis | null>(null);
  const [balanceSheet, setBalanceSheet] = useState<BalanceSheetAnalysis | null>(null);
  const [cashFlow, setCashFlow] = useState<CashFlowAnalysis | null>(null);
  const [swot, setSwot] = useState<SWOTResult | null>(null);
  const [redFlags, setRedFlags] = useState<RedFlag[]>([]);
  const [scenario, setScenario] = useState<ScenarioResult | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [screenerError, setScreenerError] = useState<string | null>(null);

  const [marketPrice, setMarketPrice] = useState(300);
  const [valuation, setValuation] = useState<ValuationResult | null>(null);
  const [dcf, setDcf] = useState<DCFResult | null>(null);
  const [dcfAssumptions, setDcfAssumptions] = useState({
    base_revenue: 1000, revenue_growth_pct: [10, 10, 8, 8, 6], ebitda_margin_pct: 25,
    tax_rate_pct: 25, capex_pct_of_revenue: 5, working_capital_pct_of_revenue: 2,
    wacc_pct: 11, terminal_growth_pct: 4, net_debt: 300, shares_outstanding: 50,
  });

  const [sectorOutlook, setSectorOutlook] = useState(60);
  const [macroRisk, setMacroRisk] = useState(60);
  const [managementScore, setManagementScore] = useState(60);
  const [score, setScore] = useState<FundamentalScoreResult | null>(null);
  const [technicalScore, setTechnicalScore] = useState(70);
  const [technicalDirection, setTechnicalDirection] = useState<"LONG" | "SHORT" | "NO_TRADE">("LONG");
  const [fusion, setFusion] = useState<FusionResult | null>(null);

  const [card, setCard] = useState<CompanyIntelligenceCard | null>(null);
  const [sectors, setSectors] = useState<SectorRotationRow[]>([]);
  const [screenerResults, setScreenerResults] = useState<{ symbol: string; name: string; sector: string }[]>([]);
  const [screenerFilters, setScreenerFilters] = useState({ min_roce_pct: "", max_debt_to_equity: "", min_revenue_cagr_3y_pct: "", min_promoter_holding_pct: "" });

  const [calendarEvents, setCalendarEvents] = useState<EarningsCalendarEvent[]>([]);
  const [upcomingEvents, setUpcomingEvents] = useState<UpcomingCalendarEvent[]>([]);
  const [newCalendarEvent, setNewCalendarEvent] = useState<EarningsCalendarEvent>(defaultCalendarEvent());
  const [preEarnings, setPreEarnings] = useState<PreEarningsAnalysis | null>(null);
  const [preEarningsError, setPreEarningsError] = useState<string | null>(null);
  const [postEarnings, setPostEarnings] = useState<PostEarningsAnalysis | null>(null);
  const [postEarningsError, setPostEarningsError] = useState<string | null>(null);

  const [peerMetrics, setPeerMetrics] = useState<PeerMetrics[]>([]);

  const [sectorMetricSpecs, setSectorMetricSpecs] = useState<SectorMetricSpecs>({});
  const [sectorMetrics, setSectorMetrics] = useState<SectorMetric[]>([]);
  const [newSectorMetric, setNewSectorMetric] = useState<SectorMetric>(defaultSectorMetric());
  const [sectorKey, setSectorKey] = useState("BANKING");
  const [sectorSpecificResult, setSectorSpecificResult] = useState<SectorSpecificResult | null>(null);
  const [sectorSpecificError, setSectorSpecificError] = useState<string | null>(null);

  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [eventImpact, setEventImpact] = useState<EventImpactResult | null>(null);
  const [finalReport, setFinalReport] = useState<FinalCompanyReport | null>(null);
  const [alertsError, setAlertsError] = useState<string | null>(null);

  function refreshCompanies() {
    fundamentalsApi.listCompanies().then((list) => {
      setCompanies(list);
      if (!symbol && list.length) setSymbol(list[0].symbol);
    });
  }

  useEffect(refreshCompanies, []);

  useEffect(() => {
    if (!symbol) return;
    fundamentalsApi.listFinancials(symbol).then(setPeriods).catch(() => setPeriods([]));
    setGrowth(null); setProfitability(null); setEarningsQuality(null); setQuarterly(null);
    setBalanceSheet(null); setCashFlow(null); setSwot(null); setRedFlags([]); setScenario(null);
    setValuation(null); setDcf(null); setScore(null); setFusion(null); setCard(null);
  }, [symbol]);

  const selectedCompany = useMemo(() => companies.find((c) => c.symbol === symbol), [companies, symbol]);

  async function handleCreateCompany() {
    setError(null);
    try {
      await fundamentalsApi.createCompany(newCompany);
      setShowNewCompanyForm(false);
      setNewCompany(defaultCompanyProfile());
      refreshCompanies();
      setSymbol(newCompany.symbol);
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleAddPeriod() {
    setError(null);
    try {
      await fundamentalsApi.addFinancialPeriod(symbol, newPeriod);
      const list = await fundamentalsApi.listFinancials(symbol);
      setPeriods(list);
      setNewPeriod(defaultFinancialPeriod());
    } catch (e) {
      setError(String(e));
    }
  }

  async function loadAnalysis() {
    setAnalysisError(null);
    try {
      const [g, p, eq, bs, cf, sw, rf] = await Promise.all([
        fundamentalsApi.growth(symbol), fundamentalsApi.profitability(symbol),
        fundamentalsApi.earningsQuality(symbol), fundamentalsApi.balanceSheet(symbol),
        fundamentalsApi.cashFlow(symbol), fundamentalsApi.swot(symbol), fundamentalsApi.redFlags(symbol),
      ]);
      setGrowth(g); setProfitability(p); setEarningsQuality(eq); setBalanceSheet(bs);
      setCashFlow(cf); setSwot(sw); setRedFlags(rf);
      try { setScenario(await fundamentalsApi.scenario(symbol)); } catch { /* needs at least one period */ }
      try { setQuarterly(await fundamentalsApi.quarterly(symbol)); } catch { /* needs quarterly periods */ }
    } catch (e) {
      setAnalysisError(String(e));
    }
  }

  async function handleValuation() {
    try {
      setValuation(await fundamentalsApi.valuation(symbol, { market_price: marketPrice }));
    } catch (e) {
      setAnalysisError(String(e));
    }
  }

  async function handleDcf() {
    try {
      setDcf(await fundamentalsApi.dcf(symbol, dcfAssumptions, marketPrice));
    } catch (e) {
      setAnalysisError(String(e));
    }
  }

  async function handleScore() {
    try {
      const result = await fundamentalsApi.fundamentalScore(symbol, {
        sector_outlook_0_100: sectorOutlook, macro_event_risk_0_100: macroRisk, management_score_0_100: managementScore,
      });
      setScore(result);
    } catch (e) {
      setAnalysisError(String(e));
    }
  }

  async function handleFusion() {
    if (!score) return;
    try {
      setFusion(await fundamentalsApi.fusion({
        fundamental_score: score.score, technical_score: technicalScore,
        technical_direction: technicalDirection, horizon: "Positional",
      }));
    } catch (e) {
      setAnalysisError(String(e));
    }
  }

  async function handleCard() {
    try {
      setCard(await fundamentalsApi.card(symbol));
    } catch (e) {
      setAnalysisError(String(e));
    }
  }

  async function handleScreener() {
    setScreenerError(null);
    const filters: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(screenerFilters)) {
      if (value !== "") filters[key] = Number(value);
    }
    try {
      setScreenerResults(await fundamentalsApi.screener(filters));
    } catch (e) {
      setScreenerError(String(e));
    }
  }

  useEffect(() => {
    if (tab === "Screener & Sectors") fundamentalsApi.sectors().then(setSectors);
  }, [tab]);

  useEffect(() => {
    if (tab !== "Calendar & Pre-Earnings" || !symbol) return;
    fundamentalsApi.listCalendarEvents(symbol).then(setCalendarEvents).catch(() => setCalendarEvents([]));
    fundamentalsApi.upcomingCalendarEvents().then(setUpcomingEvents).catch(() => setUpcomingEvents([]));
    setPreEarnings(null);
    setPreEarningsError(null);
    setPostEarnings(null);
    setPostEarningsError(null);
  }, [tab, symbol]);

  useEffect(() => {
    if (tab !== "Peer Comparison" || !selectedCompany) return;
    fundamentalsApi.peerComparison(selectedCompany.sector).then(setPeerMetrics).catch(() => setPeerMetrics([]));
  }, [tab, selectedCompany]);

  useEffect(() => {
    if (tab !== "Sector Metrics" || !symbol) return;
    fundamentalsApi.sectorMetricSpecs().then(setSectorMetricSpecs).catch(() => setSectorMetricSpecs({}));
    fundamentalsApi.listSectorMetrics(symbol).then(setSectorMetrics).catch(() => setSectorMetrics([]));
    setSectorSpecificResult(null);
    setSectorSpecificError(null);
  }, [tab, symbol]);

  useEffect(() => {
    if (tab !== "Alerts & Report" || !symbol) return;
    fundamentalsApi.alerts(symbol).then(setAlerts).catch(() => setAlerts(null));
    fundamentalsApi.eventImpact(symbol).then(setEventImpact).catch(() => setEventImpact(null));
    setFinalReport(null);
    setAlertsError(null);
  }, [tab, symbol]);

  async function handleAddCalendarEvent() {
    setError(null);
    try {
      await fundamentalsApi.addCalendarEvent(symbol, newCalendarEvent);
      const [events, upcoming] = await Promise.all([
        fundamentalsApi.listCalendarEvents(symbol), fundamentalsApi.upcomingCalendarEvents(),
      ]);
      setCalendarEvents(events);
      setUpcomingEvents(upcoming);
      setNewCalendarEvent(defaultCalendarEvent());
    } catch (e) {
      setError(String(e));
    }
  }

  async function handlePreEarnings() {
    setPreEarningsError(null);
    try {
      setPreEarnings(await fundamentalsApi.preEarnings(symbol));
    } catch (e) {
      setPreEarningsError(String(e));
    }
  }

  async function handlePostEarnings() {
    setPostEarningsError(null);
    try {
      setPostEarnings(await fundamentalsApi.postEarnings(symbol));
    } catch (e) {
      setPostEarningsError(String(e));
    }
  }

  async function handleAddSectorMetric() {
    setError(null);
    try {
      await fundamentalsApi.addSectorMetric(symbol, newSectorMetric);
      setSectorMetrics(await fundamentalsApi.listSectorMetrics(symbol));
      setNewSectorMetric(defaultSectorMetric());
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleSectorSpecificAnalysis() {
    setSectorSpecificError(null);
    try {
      setSectorSpecificResult(await fundamentalsApi.sectorSpecificAnalysis(symbol, sectorKey));
    } catch (e) {
      setSectorSpecificError(String(e));
    }
  }

  async function handleGenerateReport() {
    setAlertsError(null);
    try {
      setFinalReport(await fundamentalsApi.finalReport(symbol));
    } catch (e) {
      setAlertsError(String(e));
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-amber-400">Fundamental Analysis</h1>
        <p className="text-sm font-semibold text-amber-400/60">
          Institutional-grade company intelligence: business quality, earnings quality, valuation, DCF, red flags, SWOT,
          and a composite Fundamental Score fused with the technical Signal Score. Every input is entered and cited -
          nothing here is fetched from a live feed (no SEBI/NSE/BSE credentials are wired in yet).
        </p>
      </div>
      <Disclaimer kind="score" />

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs text-muted mb-1">Company</label>
            <select
              className="w-56 rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            >
              {companies.map((c) => (
                <option key={c.symbol} value={c.symbol}>{c.symbol} — {c.name}</option>
              ))}
            </select>
          </div>
          <button
            onClick={() => setShowNewCompanyForm((v) => !v)}
            disabled={!user}
            title={user ? undefined : "Log in to add a company"}
            className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1.5 text-sm disabled:opacity-50"
          >
            {showNewCompanyForm ? "Cancel" : "+ Add Company"}
          </button>
        </div>

        {showNewCompanyForm && (
          <div className="mt-3 grid sm:grid-cols-3 gap-2">
            <input placeholder="Symbol (e.g. TCS)" className="rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={newCompany.symbol} onChange={(e) => setNewCompany({ ...newCompany, symbol: e.target.value.toUpperCase() })} />
            <input placeholder="Company name" className="rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={newCompany.name} onChange={(e) => setNewCompany({ ...newCompany, name: e.target.value })} />
            <input placeholder="Sector" className="rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={newCompany.sector} onChange={(e) => setNewCompany({ ...newCompany, sector: e.target.value })} />
            <input placeholder="Industry" className="rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={newCompany.industry} onChange={(e) => setNewCompany({ ...newCompany, industry: e.target.value })} />
            <input type="number" placeholder="Promoter holding %" className="rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={newCompany.promoter_holding_pct ?? ""} onChange={(e) => setNewCompany({ ...newCompany, promoter_holding_pct: Number(e.target.value) })} />
            <button onClick={handleCreateCompany} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm">
              Save Company
            </button>
          </div>
        )}
        {error && <div className="mt-2 text-sm text-danger">{error}</div>}
      </Card>

      {!symbol ? (
        <Card><div className="text-sm text-muted py-4 text-center">No companies yet — add one above to get started.</div></Card>
      ) : (
        <>
          <div className="flex flex-wrap gap-1 border-b border-border">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-3 py-2 text-sm ${tab === t ? "text-brand border-b-2 border-brand" : "text-muted hover:text-slate-200"}`}
              >
                {t}
              </button>
            ))}
          </div>

          {tab === "Profile" && selectedCompany && (
            <Card title={`${selectedCompany.symbol} — ${selectedCompany.name}`}>
              <div className="grid sm:grid-cols-3 gap-3 text-sm">
                <div><span className="text-muted">Sector:</span> {selectedCompany.sector}</div>
                <div><span className="text-muted">Industry:</span> {selectedCompany.industry}</div>
                <div><span className="text-muted">Promoter Holding:</span> {selectedCompany.promoter_holding_pct ?? "-"}%</div>
                <div><span className="text-muted">Face Value:</span> {selectedCompany.face_value ?? "-"}</div>
                <div><span className="text-muted">ISIN:</span> {selectedCompany.isin ?? "-"}</div>
                <div><span className="text-muted">Market Cap:</span> {selectedCompany.market_cap ?? "-"}</div>
              </div>
              {selectedCompany.source && (
                <div className="mt-3 text-xs text-muted">Source: {selectedCompany.source.source} (retrieved {selectedCompany.source.retrieved_date})</div>
              )}
            </Card>
          )}

          {tab === "Financials" && (
            <div className="space-y-4">
              <Card title={`Financial periods (${periods.length})`}>
                {periods.length === 0 ? (
                  <div className="text-sm text-muted py-2">No periods entered yet — add one below.</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead className="text-muted uppercase text-[10px]"><tr className="text-left">
                        <th className="py-1 pr-3">Period</th><th className="py-1 pr-3">Revenue</th><th className="py-1 pr-3">EBITDA</th>
                        <th className="py-1 pr-3">PAT</th><th className="py-1 pr-3">EPS</th><th className="py-1 pr-3">CFO</th>
                      </tr></thead>
                      <tbody>
                        {periods.map((p) => (
                          <tr key={p.period_label} className="border-t border-border">
                            <td className="py-1 pr-3 font-medium text-slate-200">{p.period_label} ({p.period_type})</td>
                            <td className="py-1 pr-3">{p.revenue}</td><td className="py-1 pr-3">{p.ebitda}</td>
                            <td className="py-1 pr-3">{p.pat}</td><td className="py-1 pr-3">{p.eps ?? "-"}</td><td className="py-1 pr-3">{p.cfo ?? "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              <Card title="Add a financial period">
                <div className="grid sm:grid-cols-4 gap-2 text-sm">
                  <select className="rounded bg-panel2 border border-border px-2 py-1.5" value={newPeriod.period_type}
                    onChange={(e) => setNewPeriod({ ...newPeriod, period_type: e.target.value as FinancialPeriod["period_type"] })}>
                    <option value="ANNUAL">Annual</option><option value="QUARTER">Quarter</option>
                  </select>
                  <input placeholder="Label (e.g. FY24 / Q2FY25)" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.period_label} onChange={(e) => setNewPeriod({ ...newPeriod, period_label: e.target.value })} />
                  <input type="date" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.period_end_date} onChange={(e) => setNewPeriod({ ...newPeriod, period_end_date: e.target.value })} />
                  <input type="number" placeholder="Revenue" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.revenue} onChange={(e) => setNewPeriod({ ...newPeriod, revenue: Number(e.target.value) })} />
                  <input type="number" placeholder="EBITDA" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.ebitda} onChange={(e) => setNewPeriod({ ...newPeriod, ebitda: Number(e.target.value) })} />
                  <input type="number" placeholder="EBIT" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.ebit ?? ""} onChange={(e) => setNewPeriod({ ...newPeriod, ebit: Number(e.target.value) })} />
                  <input type="number" placeholder="PAT" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.pat} onChange={(e) => setNewPeriod({ ...newPeriod, pat: Number(e.target.value) })} />
                  <input type="number" placeholder="EPS" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.eps ?? ""} onChange={(e) => setNewPeriod({ ...newPeriod, eps: Number(e.target.value) })} />
                  <input type="number" placeholder="Shares Outstanding" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.shares_outstanding ?? ""} onChange={(e) => setNewPeriod({ ...newPeriod, shares_outstanding: Number(e.target.value) })} />
                  <input type="number" placeholder="CFO" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.cfo ?? ""} onChange={(e) => setNewPeriod({ ...newPeriod, cfo: Number(e.target.value) })} />
                  <input type="number" placeholder="Capex" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.capex ?? ""} onChange={(e) => setNewPeriod({ ...newPeriod, capex: Number(e.target.value) })} />
                  <input type="number" placeholder="Total Debt" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.total_debt ?? ""} onChange={(e) => setNewPeriod({ ...newPeriod, total_debt: Number(e.target.value) })} />
                  <input type="number" placeholder="Cash & Equivalents" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.cash_and_equivalents ?? ""} onChange={(e) => setNewPeriod({ ...newPeriod, cash_and_equivalents: Number(e.target.value) })} />
                  <input type="number" placeholder="Current Assets" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.current_assets ?? ""} onChange={(e) => setNewPeriod({ ...newPeriod, current_assets: Number(e.target.value) })} />
                  <input type="number" placeholder="Current Liabilities" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.current_liabilities ?? ""} onChange={(e) => setNewPeriod({ ...newPeriod, current_liabilities: Number(e.target.value) })} />
                  <input type="number" placeholder="Shareholders Equity" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.shareholders_equity ?? ""} onChange={(e) => setNewPeriod({ ...newPeriod, shareholders_equity: Number(e.target.value) })} />
                  <input type="number" placeholder="Total Assets" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.total_assets ?? ""} onChange={(e) => setNewPeriod({ ...newPeriod, total_assets: Number(e.target.value) })} />
                  <input type="number" placeholder="Interest Expense" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newPeriod.interest_expense ?? ""} onChange={(e) => setNewPeriod({ ...newPeriod, interest_expense: Number(e.target.value) })} />
                </div>
                <button onClick={handleAddPeriod} disabled={!user} className="mt-3 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm disabled:opacity-50">
                  Save Period
                </button>
              </Card>
            </div>
          )}

          {tab === "Analysis" && (
            <div className="space-y-4">
              <button onClick={loadAnalysis} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm">
                Run Analysis
              </button>
              {analysisError && <div className="text-sm text-danger">{analysisError}</div>}

              {growth && (
                <Card title="Growth">
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                    <StatTile label="YoY Growth" value={growth.yoy_growth_pct != null ? `${growth.yoy_growth_pct.toFixed(1)}%` : "-"} tone={toneForNumber(growth.yoy_growth_pct)} />
                    <StatTile label="3Y CAGR" value={growth.cagr_3y_pct != null ? `${growth.cagr_3y_pct.toFixed(1)}%` : "-"} tone={toneForNumber(growth.cagr_3y_pct)} />
                    <StatTile label="5Y CAGR" value={growth.cagr_5y_pct != null ? `${growth.cagr_5y_pct.toFixed(1)}%` : "-"} tone={toneForNumber(growth.cagr_5y_pct)} />
                  </div>
                  <div className="mt-2 text-xs text-muted">{growth.note}</div>
                </Card>
              )}

              {profitability && (
                <Card title="Profitability">
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <StatTile label="EBITDA Margin" value={profitability.ebitda_margin_pct != null ? `${profitability.ebitda_margin_pct.toFixed(1)}%` : "-"} />
                    <StatTile label="Net Margin" value={profitability.net_margin_pct != null ? `${profitability.net_margin_pct.toFixed(1)}%` : "-"} />
                    <StatTile label="ROE" value={profitability.roe_pct != null ? `${profitability.roe_pct.toFixed(1)}%` : "-"} />
                    <StatTile label="ROCE" value={profitability.roce_pct != null ? `${profitability.roce_pct.toFixed(1)}%` : "-"} />
                  </div>
                  <div className="mt-2 text-xs text-muted">Margin trend: <span className="text-slate-200">{profitability.margin_trend}</span> — {profitability.revenue_vs_margin_note}</div>
                </Card>
              )}

              {earningsQuality && (
                <Card title="Earnings Quality">
                  <div className="text-sm">Label: <span className="text-slate-200 font-medium">{earningsQuality.label}</span></div>
                  {earningsQuality.cfo_to_pat_ratio != null && <div className="text-xs text-muted mt-1">CFO/PAT: {earningsQuality.cfo_to_pat_ratio.toFixed(2)}</div>}
                  {earningsQuality.warnings.map((w, i) => <div key={i} className="text-xs text-warn mt-1">⚠ {w}</div>)}
                </Card>
              )}

              {quarterly && (
                <Card title={`Quarterly Comparison — ${quarterly.period_label} (${quarterly.overall_quality})`}>
                  <table className="w-full text-xs">
                    <thead className="text-muted uppercase text-[10px]"><tr className="text-left">
                      <th className="py-1 pr-3">Metric</th><th className="py-1 pr-3">Current</th><th className="py-1 pr-3">QoQ %</th><th className="py-1 pr-3">YoY %</th><th className="py-1 pr-3"></th>
                    </tr></thead>
                    <tbody>
                      {quarterly.comparisons.map((c) => (
                        <tr key={c.metric} className="border-t border-border">
                          <td className="py-1 pr-3">{c.metric}</td><td className="py-1 pr-3">{c.current.toFixed(1)}</td>
                          <td className="py-1 pr-3">{c.qoq_change_pct != null ? `${c.qoq_change_pct.toFixed(1)}%` : "-"}</td>
                          <td className="py-1 pr-3">{c.yoy_change_pct != null ? `${c.yoy_change_pct.toFixed(1)}%` : "-"}</td>
                          <td className="py-1 pr-3">{c.direction}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}

              {balanceSheet && (
                <Card title="Balance Sheet">
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <StatTile label="Debt Risk" value={balanceSheet.debt_risk} tone={toneForRisk(balanceSheet.debt_risk)} />
                    <StatTile label="Liquidity Risk" value={balanceSheet.liquidity_risk} tone={toneForRisk(balanceSheet.liquidity_risk)} />
                    <StatTile label="Net Debt/EBITDA" value={balanceSheet.net_debt_to_ebitda?.toFixed(2) ?? "-"} />
                    <StatTile label="Current Ratio" value={balanceSheet.current_ratio?.toFixed(2) ?? "-"} />
                  </div>
                  {balanceSheet.notes.map((n, i) => <div key={i} className="text-xs text-muted mt-1">{n}</div>)}
                </Card>
              )}

              {cashFlow && (
                <Card title="Cash Flow">
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                    <StatTile label="CFO" value={cashFlow.cfo?.toFixed(1) ?? "-"} />
                    <StatTile label="FCF" value={cashFlow.fcf?.toFixed(1) ?? "-"} tone={toneForNumber(cashFlow.fcf)} />
                    <StatTile label="FCF Yield" value={cashFlow.fcf_yield_pct != null ? `${cashFlow.fcf_yield_pct.toFixed(1)}%` : "-"} />
                  </div>
                  {cashFlow.warning && <div className="mt-2 text-xs text-warn">⚠ {cashFlow.warning}</div>}
                </Card>
              )}

              {scenario && (
                <Card title="Bull / Base / Bear (next period)">
                  <table className="w-full text-xs">
                    <thead className="text-muted uppercase text-[10px]"><tr className="text-left">
                      <th className="py-1 pr-3">Scenario</th><th className="py-1 pr-3">Revenue</th><th className="py-1 pr-3">EBITDA</th><th className="py-1 pr-3">PAT</th><th className="py-1 pr-3">EPS</th>
                    </tr></thead>
                    <tbody>
                      {[scenario.bull, scenario.base, scenario.bear].map((s) => (
                        <tr key={s.scenario} className="border-t border-border">
                          <td className="py-1 pr-3 font-medium text-slate-200">{s.scenario}</td>
                          <td className="py-1 pr-3">{s.revenue.toFixed(0)}</td><td className="py-1 pr-3">{s.ebitda.toFixed(0)}</td>
                          <td className="py-1 pr-3">{s.pat.toFixed(0)}</td><td className="py-1 pr-3">{s.eps ?? "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}
            </div>
          )}

          {tab === "Valuation & DCF" && (
            <div className="space-y-4">
              <Card title="Relative Valuation">
                <div className="flex items-end gap-2 mb-3">
                  <div>
                    <label className="block text-xs text-muted mb-1">Market Price</label>
                    <input type="number" className="w-32 rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                      value={marketPrice} onChange={(e) => setMarketPrice(Number(e.target.value))} />
                  </div>
                  <button onClick={handleValuation} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm">
                    Compute Valuation
                  </button>
                </div>
                {valuation && (
                  <>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                      <StatTile label="P/E" value={valuation.pe?.toFixed(1) ?? "-"} />
                      <StatTile label="EV/EBITDA" value={valuation.ev_ebitda?.toFixed(1) ?? "-"} />
                      <StatTile label="P/B" value={valuation.pb?.toFixed(1) ?? "-"} />
                      <StatTile label="Classification" value={valuation.classification} />
                    </div>
                    {valuation.notes.map((n, i) => <div key={i} className="text-xs text-muted mt-1">{n}</div>)}
                  </>
                )}
              </Card>

              <Card title="DCF Calculator">
                <div className="grid sm:grid-cols-4 gap-2 text-sm mb-3">
                  <input type="number" placeholder="Base Revenue" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={dcfAssumptions.base_revenue} onChange={(e) => setDcfAssumptions({ ...dcfAssumptions, base_revenue: Number(e.target.value) })} />
                  <input type="number" placeholder="EBITDA Margin %" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={dcfAssumptions.ebitda_margin_pct} onChange={(e) => setDcfAssumptions({ ...dcfAssumptions, ebitda_margin_pct: Number(e.target.value) })} />
                  <input type="number" placeholder="WACC %" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={dcfAssumptions.wacc_pct} onChange={(e) => setDcfAssumptions({ ...dcfAssumptions, wacc_pct: Number(e.target.value) })} />
                  <input type="number" placeholder="Terminal Growth %" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={dcfAssumptions.terminal_growth_pct} onChange={(e) => setDcfAssumptions({ ...dcfAssumptions, terminal_growth_pct: Number(e.target.value) })} />
                  <input type="number" placeholder="Net Debt" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={dcfAssumptions.net_debt} onChange={(e) => setDcfAssumptions({ ...dcfAssumptions, net_debt: Number(e.target.value) })} />
                  <input type="number" placeholder="Shares Outstanding" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={dcfAssumptions.shares_outstanding} onChange={(e) => setDcfAssumptions({ ...dcfAssumptions, shares_outstanding: Number(e.target.value) })} />
                </div>
                <button onClick={handleDcf} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm">
                  Run DCF
                </button>
                {dcf && (
                  <div className="mt-3 grid grid-cols-3 gap-3">
                    <StatTile label="Bear" value={`₹${dcf.bear.intrinsic_value_per_share}`} tone="down" />
                    <StatTile label="Base" value={`₹${dcf.base.intrinsic_value_per_share}`} />
                    <StatTile label="Bull" value={`₹${dcf.bull.intrinsic_value_per_share}`} tone="up" />
                    {dcf.upside_pct != null && (
                      <div className="col-span-3 text-xs text-muted mt-1">
                        Upside vs current price: <span className={dcf.upside_pct >= 0 ? "text-accent" : "text-danger"}>{dcf.upside_pct.toFixed(1)}%</span>
                        {dcf.margin_of_safety_pct != null && ` · Margin of safety: ${dcf.margin_of_safety_pct.toFixed(1)}%`}
                      </div>
                    )}
                  </div>
                )}
              </Card>
            </div>
          )}

          {tab === "Quality & Risk" && (
            <div className="space-y-4">
              <Card title="SWOT">
                {swot ? (
                  <div className="grid sm:grid-cols-2 gap-4 text-sm">
                    <div><div className="text-xs uppercase text-muted mb-1">Strengths</div>{swot.strengths.length ? swot.strengths.map((s, i) => <div key={i} className="text-accent">+ {s}</div>) : <div className="text-muted">None entered</div>}</div>
                    <div><div className="text-xs uppercase text-muted mb-1">Weaknesses</div>{swot.weaknesses.length ? swot.weaknesses.map((s, i) => <div key={i} className="text-danger">− {s}</div>) : <div className="text-muted">None entered</div>}</div>
                    <div><div className="text-xs uppercase text-muted mb-1">Opportunities</div>{swot.opportunities.length ? swot.opportunities.map((s, i) => <div key={i} className="text-sky-400">↑ {s}</div>) : <div className="text-muted">None entered</div>}</div>
                    <div><div className="text-xs uppercase text-muted mb-1">Threats</div>{swot.threats.length ? swot.threats.map((s, i) => <div key={i} className="text-warn">⚠ {s}</div>) : <div className="text-muted">None entered</div>}</div>
                  </div>
                ) : <div className="text-sm text-muted py-2">Run Analysis on the Analysis tab first, or add qualitative factors below.</div>}
              </Card>

              <Card title="Red Flags">
                {redFlags.length === 0 ? <div className="text-sm text-muted py-2">None detected from persisted data.</div> : (
                  redFlags.map((f, i) => (
                    <div key={i} className={`text-xs mb-1 ${f.severity === "High" || f.severity === "Extreme" ? "text-danger" : "text-warn"}`}>
                      [{f.severity}] {f.description}
                    </div>
                  ))
                )}
              </Card>

              <QualitativeFactorForm symbol={symbol} disabled={!user} onSaved={loadAnalysis} />
            </div>
          )}

          {tab === "Score & Fusion" && (
            <div className="space-y-4">
              <Card title="Fundamental Score inputs (analyst judgment - not computed automatically)">
                <div className="grid sm:grid-cols-3 gap-3 text-sm">
                  <div><label className="block text-xs text-muted mb-1">Sector Outlook (0-100)</label>
                    <input type="number" className="w-full rounded bg-panel2 border border-border px-2 py-1.5" value={sectorOutlook} onChange={(e) => setSectorOutlook(Number(e.target.value))} /></div>
                  <div><label className="block text-xs text-muted mb-1">Macro/Event Risk (0-100)</label>
                    <input type="number" className="w-full rounded bg-panel2 border border-border px-2 py-1.5" value={macroRisk} onChange={(e) => setMacroRisk(Number(e.target.value))} /></div>
                  <div><label className="block text-xs text-muted mb-1">Management Quality (0-100)</label>
                    <input type="number" className="w-full rounded bg-panel2 border border-border px-2 py-1.5" value={managementScore} onChange={(e) => setManagementScore(Number(e.target.value))} /></div>
                </div>
                <button onClick={handleScore} className="mt-3 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm">
                  Compute Fundamental Score
                </button>
              </Card>

              {score && (
                <Card title={`Fundamental Score: ${score.score} (${score.grade})`}>
                  <table className="w-full text-xs">
                    <thead className="text-muted uppercase text-[10px]"><tr className="text-left">
                      <th className="py-1 pr-3">Component</th><th className="py-1 pr-3">Weight</th><th className="py-1 pr-3">Score</th><th className="py-1 pr-3">Contribution</th><th className="py-1 pr-3">Note</th>
                    </tr></thead>
                    <tbody>
                      {score.breakdown.map((b) => (
                        <tr key={b.component} className="border-t border-border">
                          <td className="py-1 pr-3">{b.component}</td><td className="py-1 pr-3">{b.weight_pct}%</td>
                          <td className="py-1 pr-3">{b.score_0_100}</td><td className="py-1 pr-3">{b.contribution}</td>
                          <td className="py-1 pr-3 text-muted">{b.note}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}

              <Card title="Fusion with Technical Signal">
                <div className="grid sm:grid-cols-3 gap-3 text-sm items-end">
                  <div><label className="block text-xs text-muted mb-1">Technical Score (0-100)</label>
                    <input type="number" className="w-full rounded bg-panel2 border border-border px-2 py-1.5" value={technicalScore} onChange={(e) => setTechnicalScore(Number(e.target.value))} /></div>
                  <div><label className="block text-xs text-muted mb-1">Technical Direction</label>
                    <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5" value={technicalDirection} onChange={(e) => setTechnicalDirection(e.target.value as typeof technicalDirection)}>
                      <option value="LONG">LONG</option><option value="SHORT">SHORT</option><option value="NO_TRADE">NO_TRADE</option>
                    </select></div>
                  <button onClick={handleFusion} disabled={!score} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm disabled:opacity-50">
                    Compute Final Bias
                  </button>
                </div>
                {fusion && (
                  <div className="mt-3">
                    <div className="text-lg font-semibold text-slate-100">{fusion.bias}</div>
                    <div className="text-xs text-muted mt-1">Final composite score: {fusion.final_score} — {fusion.note}</div>
                  </div>
                )}
              </Card>
            </div>
          )}

          {tab === "Intelligence Card" && (
            <Card title="One-Page Company Intelligence Card">
              <button onClick={handleCard} className="mb-3 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm">
                Generate Card
              </button>
              {card && (
                <div className="max-w-md rounded border border-border p-4 space-y-1 text-sm">
                  <div className="text-lg font-semibold text-slate-100">{card.symbol}</div>
                  <div className="text-2xl font-bold text-accent">{card.fundamental_score}/100 <span className="text-sm text-slate-300">({card.fundamental_grade})</span></div>
                  <div>Business Quality: <span className="text-slate-200">{card.business_quality}</span></div>
                  <div>Earnings Quality: <span className="text-slate-200">{card.earnings_quality}</span></div>
                  <div>Balance Sheet Risk: <span className="text-slate-200">{card.balance_sheet_risk}</span></div>
                  <div>Valuation: <span className="text-slate-200">{card.valuation}</span></div>
                  <div>Earnings Outlook: <span className="text-slate-200">{card.earnings_outlook}</span></div>
                </div>
              )}
            </Card>
          )}

          {tab === "Screener & Sectors" && (
            <div className="space-y-4">
              <Card title="Fundamental Screener">
                <div className="grid sm:grid-cols-4 gap-2 text-sm">
                  <input placeholder="Min ROCE %" className="rounded bg-panel2 border border-border px-2 py-1.5" value={screenerFilters.min_roce_pct} onChange={(e) => setScreenerFilters({ ...screenerFilters, min_roce_pct: e.target.value })} />
                  <input placeholder="Max Debt/Equity" className="rounded bg-panel2 border border-border px-2 py-1.5" value={screenerFilters.max_debt_to_equity} onChange={(e) => setScreenerFilters({ ...screenerFilters, max_debt_to_equity: e.target.value })} />
                  <input placeholder="Min 3Y Revenue CAGR %" className="rounded bg-panel2 border border-border px-2 py-1.5" value={screenerFilters.min_revenue_cagr_3y_pct} onChange={(e) => setScreenerFilters({ ...screenerFilters, min_revenue_cagr_3y_pct: e.target.value })} />
                  <input placeholder="Min Promoter Holding %" className="rounded bg-panel2 border border-border px-2 py-1.5" value={screenerFilters.min_promoter_holding_pct} onChange={(e) => setScreenerFilters({ ...screenerFilters, min_promoter_holding_pct: e.target.value })} />
                </div>
                <button onClick={handleScreener} className="mt-3 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm">
                  Run Screener
                </button>
                {screenerError && <div className="mt-2 text-sm text-danger">{screenerError}</div>}
                {screenerResults.length > 0 && (
                  <div className="mt-3 text-sm space-y-1">
                    {screenerResults.map((r) => <div key={r.symbol}>{r.symbol} — {r.name} ({r.sector})</div>)}
                  </div>
                )}
              </Card>

              <Card title="Sector Rotation (from companies tracked in this platform)">
                <table className="w-full text-xs">
                  <thead className="text-muted uppercase text-[10px]"><tr className="text-left">
                    <th className="py-1 pr-3">Sector</th><th className="py-1 pr-3">Companies</th><th className="py-1 pr-3">Avg 3Y Revenue CAGR</th><th className="py-1 pr-3">Avg ROCE</th>
                  </tr></thead>
                  <tbody>
                    {sectors.map((s) => (
                      <tr key={s.sector} className="border-t border-border">
                        <td className="py-1 pr-3 font-medium text-slate-200">{s.sector}</td>
                        <td className="py-1 pr-3">{s.companies_tracked}</td>
                        <td className="py-1 pr-3">{s.avg_revenue_cagr_3y_pct != null ? `${s.avg_revenue_cagr_3y_pct}%` : "-"}</td>
                        <td className="py-1 pr-3">{s.avg_roce_pct != null ? `${s.avg_roce_pct}%` : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            </div>
          )}

          {tab === "Calendar & Pre-Earnings" && (
            <div className="space-y-4">
              <Card title={`Earnings Calendar — ${symbol}`}>
                {calendarEvents.length === 0 ? (
                  <div className="text-sm text-muted py-2">No events entered yet — add one below.</div>
                ) : (
                  <table className="w-full text-xs">
                    <thead className="text-muted uppercase text-[10px]"><tr className="text-left">
                      <th className="py-1 pr-3">Type</th><th className="py-1 pr-3">Date</th><th className="py-1 pr-3">Description</th>
                    </tr></thead>
                    <tbody>
                      {calendarEvents.map((e, i) => (
                        <tr key={i} className="border-t border-border">
                          <td className="py-1 pr-3 font-medium text-slate-200">{e.event_type}</td>
                          <td className="py-1 pr-3">{e.event_date}</td>
                          <td className="py-1 pr-3 text-muted">{e.description ?? "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                <div className="mt-3 grid sm:grid-cols-4 gap-2 text-sm">
                  <select className="rounded bg-panel2 border border-border px-2 py-1.5" value={newCalendarEvent.event_type}
                    onChange={(e) => setNewCalendarEvent({ ...newCalendarEvent, event_type: e.target.value })}>
                    <option value="RESULTS">Results</option>
                    <option value="AGM">AGM</option>
                    <option value="BOARD_MEETING">Board Meeting</option>
                    <option value="DIVIDEND">Dividend</option>
                    <option value="BONUS">Bonus</option>
                    <option value="SPLIT">Split</option>
                    <option value="BUYBACK">Buyback</option>
                    <option value="RECORD_DATE">Record Date</option>
                    <option value="INVESTOR_DAY">Investor Day</option>
                    <option value="PRODUCT_LAUNCH">Product Launch</option>
                    <option value="REGULATORY_DECISION">Regulatory Decision</option>
                  </select>
                  <input type="date" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newCalendarEvent.event_date} onChange={(e) => setNewCalendarEvent({ ...newCalendarEvent, event_date: e.target.value })} />
                  <input placeholder="Description" className="rounded bg-panel2 border border-border px-2 py-1.5 sm:col-span-2"
                    value={newCalendarEvent.description ?? ""} onChange={(e) => setNewCalendarEvent({ ...newCalendarEvent, description: e.target.value })} />
                </div>
                <button onClick={handleAddCalendarEvent} disabled={!user} className="mt-3 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm disabled:opacity-50">
                  Add Event
                </button>
              </Card>

              <Card title="Upcoming Across All Companies">
                {upcomingEvents.length === 0 ? <div className="text-sm text-muted py-2">Nothing scheduled.</div> : (
                  <div className="text-sm space-y-1">
                    {upcomingEvents.map((e, i) => (
                      <div key={i}><span className="font-medium text-slate-200">{e.symbol}</span> — {e.event_type} on {e.event_date}{e.description ? ` (${e.description})` : ""}</div>
                    ))}
                  </div>
                )}
              </Card>

              <Card title="Pre-Earnings Analysis">
                <button onClick={handlePreEarnings} className="mb-3 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm">
                  Analyze Nearest Upcoming Results
                </button>
                {preEarningsError && <div className="text-sm text-danger">{preEarningsError}</div>}
                {preEarnings && (
                  <div className="space-y-2 text-sm">
                    <div>Upcoming Results: <span className="text-slate-200">{preEarnings.upcoming_event_date}</span></div>
                    <div className="grid grid-cols-2 gap-3">
                      <StatTile label="Earnings Bias" value={preEarnings.earnings_bias} tone={preEarnings.earnings_bias === "Bullish" ? "up" : preEarnings.earnings_bias === "Bearish" ? "down" : "default"} />
                      <StatTile label="Risk Level" value={preEarnings.risk_level} tone={toneForRisk(preEarnings.risk_level)} />
                    </div>
                    <div className="text-xs text-muted">{preEarnings.note}</div>
                  </div>
                )}
              </Card>

              <Card title="Post-Earnings Analysis">
                <button onClick={handlePostEarnings} className="mb-3 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm">
                  Analyze Most Recent Past Results
                </button>
                {postEarningsError && <div className="text-sm text-danger">{postEarningsError}</div>}
                {postEarnings && (
                  <div className="space-y-2 text-sm">
                    <div>Results Date: <span className="text-slate-200">{postEarnings.results_event_date}</span></div>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                      <StatTile label="Verdict" value={postEarnings.verdict} tone={postEarnings.verdict === "Bullish" ? "up" : postEarnings.verdict === "Bearish" ? "down" : "default"} />
                      <StatTile label="Revenue Surprise" value={`${postEarnings.revenue_surprise_pct >= 0 ? "+" : ""}${postEarnings.revenue_surprise_pct.toFixed(1)}%`} tone={toneForNumber(postEarnings.revenue_surprise_pct)} />
                      <StatTile label="PAT Surprise" value={`${postEarnings.pat_surprise_pct >= 0 ? "+" : ""}${postEarnings.pat_surprise_pct.toFixed(1)}%`} tone={toneForNumber(postEarnings.pat_surprise_pct)} />
                    </div>
                    <div className="text-xs text-muted">
                      Actual revenue {postEarnings.revenue_actual} vs. own-trend expected {postEarnings.revenue_expected_trend} · Actual PAT {postEarnings.pat_actual} vs. expected {postEarnings.pat_expected_trend}
                    </div>
                    <div className="text-xs text-muted">{postEarnings.note}</div>
                  </div>
                )}
              </Card>
            </div>
          )}

          {tab === "Peer Comparison" && (
            <Card title={selectedCompany ? `Peers in ${selectedCompany.sector}` : "Peer Comparison"}>
              {peerMetrics.length === 0 ? (
                <div className="text-sm text-muted py-2">No peer companies with financial data in this sector yet.</div>
              ) : (
                <table className="w-full text-xs">
                  <thead className="text-muted uppercase text-[10px]"><tr className="text-left">
                    <th className="py-1 pr-3">Symbol</th><th className="py-1 pr-3">Name</th><th className="py-1 pr-3">Revenue YoY</th>
                    <th className="py-1 pr-3">EBITDA Margin</th><th className="py-1 pr-3">ROE</th><th className="py-1 pr-3">ROCE</th><th className="py-1 pr-3">D/E</th>
                  </tr></thead>
                  <tbody>
                    {peerMetrics.map((p) => (
                      <tr key={p.symbol} className={`border-t border-border ${p.symbol === symbol ? "bg-panel2" : ""}`}>
                        <td className="py-1 pr-3 font-medium text-slate-200">{p.symbol}</td>
                        <td className="py-1 pr-3">{p.name}</td>
                        <td className="py-1 pr-3">{p.revenue_yoy_growth_pct != null ? `${p.revenue_yoy_growth_pct.toFixed(1)}%` : "-"}</td>
                        <td className="py-1 pr-3">{p.ebitda_margin_pct != null ? `${p.ebitda_margin_pct.toFixed(1)}%` : "-"}</td>
                        <td className="py-1 pr-3">{p.roe_pct != null ? `${p.roe_pct.toFixed(1)}%` : "-"}</td>
                        <td className="py-1 pr-3">{p.roce_pct != null ? `${p.roce_pct.toFixed(1)}%` : "-"}</td>
                        <td className="py-1 pr-3">{p.debt_to_equity != null ? p.debt_to_equity.toFixed(2) : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          )}

          {tab === "Sector Metrics" && (
            <div className="space-y-4">
              <Card title="Sector-Specific Fundamentals (banking / IT / auto / pharma / oil & gas / cement)">
                <div className="flex flex-wrap items-end gap-3 mb-3">
                  <div>
                    <label className="block text-xs text-muted mb-1">Sector</label>
                    <select className="w-56 rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={sectorKey} onChange={(e) => setSectorKey(e.target.value)}>
                      {Object.keys(sectorMetricSpecs).map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
                    </select>
                  </div>
                  <button onClick={handleSectorSpecificAnalysis} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm">
                    Analyze
                  </button>
                </div>
                {sectorSpecificError && <div className="text-sm text-danger">{sectorSpecificError}</div>}
                {sectorSpecificResult && (
                  <div className="mb-3">
                    <table className="w-full text-xs">
                      <thead className="text-muted uppercase text-[10px]"><tr className="text-left">
                        <th className="py-1 pr-3">Metric</th><th className="py-1 pr-3">Value</th><th className="py-1 pr-3">Classification</th>
                      </tr></thead>
                      <tbody>
                        {sectorSpecificResult.metrics.map((m) => (
                          <tr key={m.metric_code} className="border-t border-border">
                            <td className="py-1 pr-3 font-medium text-slate-200">{m.label}</td>
                            <td className="py-1 pr-3">{m.value}{m.unit}</td>
                            <td className={`py-1 pr-3 ${m.classification === "Strong" || m.classification === "Good" ? "text-accent" : m.classification === "Weak" ? "text-danger" : ""}`}>{m.classification}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div className="mt-2 text-xs text-muted">{sectorSpecificResult.overall_note}</div>
                  </div>
                )}

                <div className="text-sm mb-2">Entered metrics for {symbol}:</div>
                {sectorMetrics.length === 0 ? (
                  <div className="text-sm text-muted py-2">None entered yet — add one below.</div>
                ) : (
                  <table className="w-full text-xs mb-3">
                    <thead className="text-muted uppercase text-[10px]"><tr className="text-left">
                      <th className="py-1 pr-3">Period</th><th className="py-1 pr-3">Metric Code</th><th className="py-1 pr-3">Value</th>
                    </tr></thead>
                    <tbody>
                      {sectorMetrics.map((m, i) => (
                        <tr key={i} className="border-t border-border">
                          <td className="py-1 pr-3">{m.period_label}</td><td className="py-1 pr-3">{m.metric_code}</td><td className="py-1 pr-3">{m.value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                <div className="grid sm:grid-cols-4 gap-2 text-sm">
                  <input placeholder="Period (e.g. FY24)" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newSectorMetric.period_label} onChange={(e) => setNewSectorMetric({ ...newSectorMetric, period_label: e.target.value })} />
                  <select className="rounded bg-panel2 border border-border px-2 py-1.5" value={newSectorMetric.metric_code}
                    onChange={(e) => setNewSectorMetric({ ...newSectorMetric, metric_code: e.target.value })}>
                    <option value="">Metric code…</option>
                    {Object.entries(sectorMetricSpecs[sectorKey] ?? {}).map(([code, spec]) => (
                      <option key={code} value={code}>{spec.label} ({code})</option>
                    ))}
                  </select>
                  <input type="number" placeholder="Value" className="rounded bg-panel2 border border-border px-2 py-1.5"
                    value={newSectorMetric.value} onChange={(e) => setNewSectorMetric({ ...newSectorMetric, value: Number(e.target.value) })} />
                  <button onClick={handleAddSectorMetric} disabled={!user || !newSectorMetric.metric_code || !newSectorMetric.period_label} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm disabled:opacity-50">
                    Add Metric
                  </button>
                </div>
              </Card>
            </div>
          )}

          {tab === "Alerts & Report" && (
            <div className="space-y-4">
              <Card title="Fundamental Alerts">
                {alertsError && <div className="text-sm text-danger">{alertsError}</div>}
                {alerts == null ? (
                  <div className="text-sm text-muted py-2">Loading…</div>
                ) : alerts.length === 0 ? (
                  <div className="text-sm text-muted py-2">No alerts — nothing crossing a threshold right now.</div>
                ) : (
                  alerts.map((a, i) => (
                    <div key={i} className={`text-xs mb-1 ${a.severity === "High" || a.severity === "Extreme" ? "text-danger" : a.severity === "Medium" ? "text-warn" : "text-muted"}`}>
                      [{a.severity}] {a.message}
                    </div>
                  ))
                )}
              </Card>

              <Card title="Event Impact Score">
                {eventImpact ? (
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                    <StatTile label="Score (-100..+100)" value={eventImpact.score.toFixed(0)} tone={toneForNumber(eventImpact.score)} />
                    <StatTile label="Bias" value={eventImpact.bias} tone={eventImpact.bias === "Bullish" ? "up" : eventImpact.bias === "Bearish" ? "down" : "default"} />
                    <StatTile label="Events Considered" value={String(eventImpact.events_considered)} />
                  </div>
                ) : (
                  <div className="text-sm text-muted py-2">No corporate actions with a recorded expected impact yet — add one on the Quality & Risk tab's corporate actions, or via the API.</div>
                )}
                {eventImpact && <div className="mt-2 text-xs text-muted">{eventImpact.note}</div>}
              </Card>

              <Card title="Final Company Report">
                <button onClick={handleGenerateReport} className="mb-3 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm">
                  Generate Final Report
                </button>
                {finalReport && (
                  <div className="max-w-2xl space-y-3 text-sm">
                    <div className="text-lg font-semibold text-slate-100">{finalReport.name} ({finalReport.symbol}) — {finalReport.sector}</div>
                    <div className="text-2xl font-bold text-accent">{finalReport.card.fundamental_score}/100 <span className="text-sm text-slate-300">({finalReport.card.fundamental_grade})</span></div>
                    <div className="grid sm:grid-cols-2 gap-4">
                      <div><div className="text-xs uppercase text-muted mb-1">Strengths</div>{finalReport.swot.strengths.length ? finalReport.swot.strengths.map((s, i) => <div key={i} className="text-accent text-xs">+ {s}</div>) : <div className="text-muted text-xs">None entered</div>}</div>
                      <div><div className="text-xs uppercase text-muted mb-1">Weaknesses</div>{finalReport.swot.weaknesses.length ? finalReport.swot.weaknesses.map((s, i) => <div key={i} className="text-danger text-xs">− {s}</div>) : <div className="text-muted text-xs">None entered</div>}</div>
                    </div>
                    <div>
                      <div className="text-xs uppercase text-muted mb-1">Red Flags ({finalReport.red_flags.length})</div>
                      {finalReport.red_flags.length === 0 ? <div className="text-xs text-muted">None detected</div> : finalReport.red_flags.map((f, i) => <div key={i} className="text-xs text-warn">[{f.severity}] {f.description}</div>)}
                    </div>
                    <div>
                      <div className="text-xs uppercase text-muted mb-1">Alerts ({finalReport.alerts.length})</div>
                      {finalReport.alerts.length === 0 ? <div className="text-xs text-muted">None</div> : finalReport.alerts.map((a, i) => <div key={i} className="text-xs text-warn">[{a.severity}] {a.message}</div>)}
                    </div>
                    {finalReport.event_impact && (
                      <div className="text-xs">Event Impact: <span className="text-slate-200">{finalReport.event_impact.score.toFixed(0)} ({finalReport.event_impact.bias})</span></div>
                    )}
                    <div className="text-xs text-muted italic">{finalReport.generated_note}</div>
                  </div>
                )}
              </Card>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function QualitativeFactorForm({ symbol, disabled, onSaved }: { symbol: string; disabled: boolean; onSaved: () => void }) {
  const [category, setCategory] = useState("BUSINESS_QUALITY_MOAT");
  const [label, setLabel] = useState("");
  const [score, setScore] = useState<number | "">(80);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    try {
      await fundamentalsApi.addQualitativeFactor(symbol, {
        category, label, score: score === "" ? null : Number(score), note: note || null,
      });
      setLabel(""); setNote("");
      onSaved();
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title="Add a qualitative judgment (business quality, SWOT, management) — cited by you, never invented">
      <div className="grid sm:grid-cols-4 gap-2 text-sm">
        <select className="rounded bg-panel2 border border-border px-2 py-1.5" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="BUSINESS_QUALITY_MOAT">Business Quality Moat Factor (needs score)</option>
          <option value="MANAGEMENT_QUALITY">Management Quality (needs score)</option>
          <option value="SWOT_STRENGTH">SWOT: Strength</option>
          <option value="SWOT_WEAKNESS">SWOT: Weakness</option>
          <option value="SWOT_OPPORTUNITY">SWOT: Opportunity</option>
          <option value="SWOT_THREAT">SWOT: Threat</option>
        </select>
        <input placeholder="Label (e.g. Pricing Power)" className="rounded bg-panel2 border border-border px-2 py-1.5"
          value={label} onChange={(e) => setLabel(e.target.value)} />
        <input type="number" placeholder="Score 0-100 (if applicable)" className="rounded bg-panel2 border border-border px-2 py-1.5"
          value={score} onChange={(e) => setScore(e.target.value === "" ? "" : Number(e.target.value))} />
        <input placeholder="Note / citation" className="rounded bg-panel2 border border-border px-2 py-1.5"
          value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      <button onClick={handleSave} disabled={disabled || saving || !label} className="mt-3 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm disabled:opacity-50">
        {saving ? "Saving…" : "Save Factor"}
      </button>
    </Card>
  );
}
