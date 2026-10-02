import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import ProChart, { directionMarker, type ChartMarker } from "../components/ProChart";
import EquityCurveChart from "../components/EquityCurveChart";
import { Card, StatTile, Disclaimer } from "../components/ui";
import { DataSourceBar, useCandleSource } from "../components/DataSource";
import {
  type BacktestResult, type BacktestRunSummary, type ExitRules, type MonteCarloResult, type OHLCVBar, type OptimizeResult, type StrategyInfo, type WalkForwardResult,
  type CustomLeg, type ExpiryRule, type OptionBacktestConfig, type OptionChainCoverage, type OptionChainSnapshotRow, type OptionPosition, type OptionPricingModel,
  type OptionStrategy, type StrikeRule, DEBIT_STRUCTURES, MAX_CUSTOM_LEGS, PAYOFF_STRUCTURES, WIDTH_STRUCTURES, WINGED_STRUCTURES,
} from "../types";
import { useAuth } from "../auth/AuthContext";

const INDEX_START_PRICES: Record<string, number> = { NIFTY: 24500, "NIFTY 50": 24500, BANKNIFTY: 52500, "NIFTY BANK": 52500, FINNIFTY: 23500, MIDCPNIFTY: 12500, SENSEX: 80500, BANKEX: 60500 };
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];

/** Parses a chain-recorder CSV (timestamp,expiry,strike,right,ltp[,iv,oi,underlying_ltp]) into upload rows. */
function parseSnapshotCsv(text: string): OptionChainSnapshotRow[] {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2) return [];
  const header = lines[0].split(",").map((h) => h.trim().toLowerCase());
  const idx = (name: string) => header.indexOf(name);
  const [ti, ei, si, ri, li, ivi, oii, ui] = ["timestamp", "expiry", "strike", "right", "ltp", "iv", "oi", "underlying_ltp"].map(idx);
  if ([ti, ei, si, ri, li].some((i) => i < 0)) throw new Error("CSV needs timestamp, expiry, strike, right and ltp columns");
  const rows: OptionChainSnapshotRow[] = [];
  for (const line of lines.slice(1)) {
    const c = line.split(",").map((v) => v.trim());
    const right = c[ri]?.toUpperCase();
    if (right !== "CE" && right !== "PE") continue;
    const row: OptionChainSnapshotRow = { timestamp: c[ti], expiry: c[ei], strike: Number(c[si]), right, ltp: Number(c[li]) };
    if (ivi >= 0 && c[ivi]) row.iv = Number(c[ivi]);
    if (oii >= 0 && c[oii]) row.oi = Number(c[oii]);
    if (ui >= 0 && c[ui]) row.underlying_ltp = Number(c[ui]);
    if (Number.isFinite(row.strike) && Number.isFinite(row.ltp) && row.ltp > 0) rows.push(row);
  }
  return rows;
}

export default function BacktestPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [symbol, setSymbol] = useState("NIFTY");
  const [bars, setBars] = useState(600);
  const source = useCandleSource(30);          // Phase AA
  const [dataWarnings, setDataWarnings] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [chartCandles, setChartCandles] = useState<OHLCVBar[]>([]);
  // Phase J: exit rules, robustness checks and run history.
  const { user } = useAuth();
  const [trail, setTrail] = useState("");
  const [breakEven, setBreakEven] = useState("");
  const [timeExitMin, setTimeExitMin] = useState("");
  const [timeExitAt, setTimeExitAt] = useState("");
  const [monteCarlo, setMonteCarlo] = useState<MonteCarloResult | null>(null);
  const [walkForward, setWalkForward] = useState<WalkForwardResult | null>(null);
  const [optGrid, setOptGrid] = useState('{"ema_fast": [9, 12], "rsi_period": [7, 14]}');
  const [optMetric, setOptMetric] = useState("net_pnl");
  const [optResult, setOptResult] = useState<OptimizeResult | null>(null);
  const [optBusy, setOptBusy] = useState(false);
  const [optError, setOptError] = useState<string | null>(null);

  async function runOptimize() {
    if (!selected || chartCandles.length === 0) return;
    setOptBusy(true); setOptError(null);
    try {
      const grid = JSON.parse(optGrid) as Record<string, (number | string)[]>;
      setOptResult(await api.backtestOptimize(selected.id, symbol, selected.timeframes[0], chartCandles, grid, optMetric));
    } catch (e) { setOptError(String(e)); } finally { setOptBusy(false); }
  }
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [robustBusy, setRobustBusy] = useState(false);
  // Phase W: trade the signals as options.
  const [tradeAs, setTradeAs] = useState<"UNDERLYING" | "OPTION">("UNDERLYING");
  const [structure, setStructure] = useState<OptionStrategy>("BULL_PUT_SPREAD");
  const [position, setPosition] = useState<OptionPosition>("BUY");
  const [expiryRule, setExpiryRule] = useState<ExpiryRule>("NEAREST");
  const [strikeRule, setStrikeRule] = useState<StrikeRule>("ATM");
  const [strikeOffset, setStrikeOffset] = useState(0);
  const [spreadWidth, setSpreadWidth] = useState(2);
  const [targetCredit, setTargetCredit] = useState("");
  const [stopCredit, setStopCredit] = useState("");
  const [premiumStop, setPremiumStop] = useState("");
  const [maxLots, setMaxLots] = useState("");
  const [customLegs, setCustomLegs] = useState<CustomLeg[]>([
    { right: "PE", role: "SHORT", strike_rule: "ATM", strike_offset: 0, ratio: 1 },
    { right: "PE", role: "LONG", strike_rule: "OTM", strike_offset: 2, ratio: 1 },
  ]);
  const updateLeg = (i: number, patch: Partial<CustomLeg>) => setCustomLegs((cur) => cur.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const [pricing, setPricing] = useState<OptionPricingModel>("synthetic");
  const [ivPct, setIvPct] = useState("");
  const [lotSize, setLotSize] = useState("");
  const [strikeStep, setStrikeStep] = useState("");
  const [expiryWeekday, setExpiryWeekday] = useState("");
  const [weeklyExpiry, setWeeklyExpiry] = useState("");
  const [intraday, setIntraday] = useState(true);
  const [allowFallback, setAllowFallback] = useState(true);
  const [uploadedRows, setUploadedRows] = useState<OptionChainSnapshotRow[]>([]);
  const [uploadNote, setUploadNote] = useState<string | null>(null);
  const [coverage, setCoverage] = useState<OptionChainCoverage[]>([]);
  const [structureSort, setStructureSort] = useState<"time" | "pnl">("time");

  function optionConfig(): OptionBacktestConfig | null {
    if (tradeAs !== "OPTION") return null;
    const cfg: OptionBacktestConfig = {
      option_strategy: structure, expiry_rule: expiryRule, strike_rule: strikeRule, strike_offset: strikeOffset, spread_width: spreadWidth,
      pricing, intraday, allow_synthetic_fallback: allowFallback,
    };
    if (structure === "SINGLE") { cfg.option_position = position; if (premiumStop) cfg.premium_stop_pct = Number(premiumStop); }
    else { if (targetCredit) cfg.target_credit_pct = Number(targetCredit); if (stopCredit) cfg.stop_credit_pct = Number(stopCredit); }
    if (structure === "CUSTOM") cfg.custom_legs = customLegs;
    if (maxLots) cfg.max_lots = Number(maxLots);
    if (ivPct) cfg.implied_volatility = Number(ivPct) / 100;
    if (lotSize) cfg.lot_size = Number(lotSize);
    if (strikeStep) cfg.strike_step = Number(strikeStep);
    if (expiryWeekday !== "") cfg.expiry_weekday = Number(expiryWeekday);
    if (weeklyExpiry !== "") cfg.weekly_expiry = weeklyExpiry === "yes";
    if (pricing === "uploaded") cfg.option_chain = uploadedRows;
    return cfg;
  }

  async function onSnapshotFile(file: File | null) {
    if (!file) return;
    try {
      const rows = parseSnapshotCsv(await file.text());
      setUploadedRows(rows);
      setUploadNote(`${rows.length} quotes parsed from ${file.name}`);
    } catch (e) { setUploadNote(String(e)); setUploadedRows([]); }
  }

  async function saveUploadedRows() {
    if (!uploadedRows.length) return;
    try {
      const out = await api.uploadOptionChainSnapshots(symbol, uploadedRows);
      setUploadNote(`${out.written} of ${out.received} quotes stored for ${out.underlying}`);
      api.optionChainCoverage().then(setCoverage).catch(() => undefined);
    } catch (e) { setUploadNote(String(e)); }
  }

  function exitRules(): ExitRules | null {
    const r: ExitRules = {};
    if (trail) r.trailing_stop_pct = Number(trail);
    if (breakEven) r.break_even_at_r = Number(breakEven);
    if (timeExitMin) r.time_exit_minutes = Number(timeExitMin);
    if (timeExitAt) r.time_exit_at = timeExitAt;
    return Object.keys(r).length ? r : null;
  }

  function refreshRuns() {
    if (user) api.listBacktests().then(setRuns).catch(() => setRuns([]));
  }

  async function runRobustness() {
    if (!selected) return;
    setRobustBusy(true); setError(null);
    try {
      const primaryTf = selected.timeframes[0];
      const [mc, wf] = await Promise.all([
        api.backtestMonteCarlo(selected.id, symbol, primaryTf, chartCandles, exitRules(), 1000, optionConfig()),
        api.backtestWalkForward(selected.id, symbol, primaryTf, chartCandles, exitRules(), 4, optionConfig()),
      ]);
      setMonteCarlo(mc.monte_carlo);
      setWalkForward(wf);
    } catch (e) {
      setError(String(e));
    } finally {
      setRobustBusy(false);
    }
  }

  useEffect(() => {
    api.listStrategies().then((list) => {
      setStrategies(list);
      if (list.length) setStrategyId(list[0].id);
    });
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(refreshRuns, [user]);
  useEffect(() => {
    if (user && tradeAs === "OPTION") api.optionChainCoverage().then(setCoverage).catch(() => setCoverage([]));
  }, [user, tradeAs]);

  const selected = useMemo(() => strategies.find((s) => s.id === strategyId), [strategies, strategyId]);

  async function runBacktest() {
    if (!selected) return;
    setLoading(true);
    setError(null);
    try {
      // Option runs price off the underlying level, so the sample series starts near the index's.
      const startPrice = tradeAs === "OPTION" ? (INDEX_START_PRICES[symbol.trim().toUpperCase()] ?? 1000) : 100;
      const primaryTf = selected.timeframes[0];
      const fetched = await source.fetch([symbol], primaryTf, { count: bars, startPriceFor: () => startPrice, seedFor: () => 11 });
      setDataWarnings(fetched.warnings);
      const candles = fetched.candles[symbol.trim().toUpperCase()];
      if (!candles?.length) throw new Error(`No candles for ${symbol}`);
      const res = await api.backtest(selected.id, symbol, primaryTf, candles, exitRules(), fetched.label, optionConfig());
      setResult(res);
      setChartCandles(candles);
      setMonteCarlo(null);
      setWalkForward(null);
      refreshRuns();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  // One entry marker (direction-coded arrow) and one exit marker (P&L-coded circle) per trade.
  const tradeMarkers: ChartMarker[] = useMemo(() => {
    if (!result) return [];
    const markers: ChartMarker[] = [];
    for (const t of result.trades) {
      markers.push(directionMarker(t.entry_time, t.direction as "LONG" | "SHORT", `Entry ${t.direction}`));
      if (t.exit_time) {
        const won = (t.pnl ?? 0) >= 0;
        markers.push({
          timestamp: t.exit_time,
          position: t.direction === "LONG" ? "aboveBar" : "belowBar",
          color: won ? "#22c55e" : "#ef4444",
          shape: "circle",
          text: `Exit ${t.pnl?.toFixed(0) ?? ""}`,
        });
      }
    }
    return markers;
  }, [result]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-teal-400">Backtesting</h1>
        <p className="text-sm font-semibold text-teal-400/60">Event-driven simulation with position sizing, SL/target management and realistic costs.</p>
      </div>

      <DataSourceBar source={source} note="Bars below applies to sample data; broker candles use the lookback." />
      {dataWarnings.map((w, i) => <div key={i} className="text-xs text-amber-300">{w}</div>)}
      <Disclaimer kind="backtest" />

      <Card>
        <div className="grid sm:grid-cols-4 gap-3 items-end">
          <div>
            <label className="block text-xs text-muted mb-1">Strategy</label>
            <select
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
            >
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Symbol</label>
            <input
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Sample bars</label>
            <input
              type="number"
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={bars}
              onChange={(e) => setBars(Number(e.target.value))}
            />
          </div>
          <button
            onClick={runBacktest}
            disabled={!selected || loading}
            className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
          >
            {loading ? "Running…" : "Run Backtest"}
          </button>
        </div>
        <div className="mt-3 rounded-lg border border-border bg-panel2/40 p-3">
          <div className="text-[11px] font-bold uppercase tracking-wider text-muted mb-2">Dynamic exits (same rules the live monitor applies)</div>
          <div className="grid sm:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-muted mb-1">Trailing stop %</label>
              <input type="number" step="0.1" min={0} placeholder="off" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={trail} onChange={(e) => setTrail(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Break-even at R</label>
              <input type="number" step="0.1" min={0} placeholder="off" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={breakEven} onChange={(e) => setBreakEven(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Time exit (minutes)</label>
              <input type="number" min={1} placeholder="off" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={timeExitMin} onChange={(e) => setTimeExitMin(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Flat at (HH:MM IST)</label>
              <input placeholder="off" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={timeExitAt} onChange={(e) => setTimeExitAt(e.target.value)} />
            </div>
          </div>
        </div>

        <div className="mt-3 rounded-lg border border-border bg-panel2/40 p-3">
          <div className="flex flex-wrap items-center gap-3 mb-2">
            <div className="text-[11px] font-bold uppercase tracking-wider text-muted">Trade as</div>
            <select className="rounded bg-panel2 border border-border px-2 py-1 text-sm" value={tradeAs} onChange={(e) => setTradeAs(e.target.value as "UNDERLYING" | "OPTION")}>
              <option value="UNDERLYING">Underlying (cash / index level)</option>
              <option value="OPTION">Options (historical option-chain backtest)</option>
            </select>
            {tradeAs === "OPTION" && <span className="text-[11px] text-muted">Same structures, strikes, expiries, exits and lot sizing a deployment uses - priced bar by bar.</span>}
          </div>
          {tradeAs === "OPTION" && (
            <div className="space-y-3">
              <div className="grid sm:grid-cols-4 gap-3">
                <div>
                  <label className="block text-xs text-muted mb-1">Structure</label>
                  <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={structure} onChange={(e) => setStructure(e.target.value as OptionStrategy)}>
                    <option value="SINGLE">Single option</option>
                    <option value="BULL_PUT_SPREAD">Bull put spread (LONG)</option>
                    <option value="BEAR_CALL_SPREAD">Bear call spread (SHORT)</option>
                    <option value="IRON_CONDOR">Iron condor (either)</option>
                    <option value="IRON_BUTTERFLY">Iron butterfly (either)</option>
                    <option value="SHORT_STRADDLE">Short straddle (either, undefined risk)</option>
                    <option value="SHORT_STRANGLE">Short strangle (either, undefined risk)</option>
                    <option value="LONG_STRADDLE">Long straddle (either, debit)</option>
                    <option value="LONG_STRANGLE">Long strangle (either, debit)</option>
                    <option value="CALENDAR_SPREAD">Calendar spread (either, debit)</option>
                    <option value="CALL_RATIO_SPREAD">Call ratio spread 1:2 (LONG)</option>
                    <option value="PUT_RATIO_SPREAD">Put ratio spread 1:2 (SHORT)</option>
                    <option value="LONG_BUTTERFLY">Long butterfly 1:2:1 (either, debit)</option>
                    <option value="CUSTOM">Custom legs (builder)</option>
                  </select>
                </div>
                {structure === "SINGLE" && (
                  <div>
                    <label className="block text-xs text-muted mb-1">Position</label>
                    <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={position} onChange={(e) => setPosition(e.target.value as OptionPosition)}>
                      <option value="BUY">Buy (CE on LONG, PE on SHORT)</option>
                      <option value="WRITE">Write (PE on LONG, CE on SHORT)</option>
                    </select>
                  </div>
                )}
                <div>
                  <label className="block text-xs text-muted mb-1">Expiry</label>
                  <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={expiryRule} onChange={(e) => setExpiryRule(e.target.value as ExpiryRule)}>
                    <option value="NEAREST">Nearest</option><option value="NEXT">Next</option><option value="MONTHLY">Monthly</option>
                  </select>
                </div>
                {structure !== "CUSTOM" && (
                  <div>
                    <label className="block text-xs text-muted mb-1">Strike</label>
                    <div className="flex gap-2">
                      <select className="flex-1 rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={strikeRule} onChange={(e) => setStrikeRule(e.target.value as StrikeRule)}>
                        <option value="ATM">ATM</option><option value="ITM">ITM</option><option value="OTM">OTM</option>
                      </select>
                      <input type="number" min={0} max={10} className="w-16 rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={strikeOffset} onChange={(e) => setStrikeOffset(Number(e.target.value))} title="steps in/out of the money" />
                    </div>
                  </div>
                )}
                {WIDTH_STRUCTURES.includes(structure) && (
                  <div>
                    <label className="block text-xs text-muted mb-1">{WINGED_STRUCTURES.includes(structure) || structure === "LONG_BUTTERFLY" ? "Wing width (steps)" : "Short strike distance (steps)"}</label>
                    <input type="number" min={1} max={20} className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={spreadWidth} onChange={(e) => setSpreadWidth(Number(e.target.value))} />
                  </div>
                )}
                {structure === "SINGLE" ? (
                  <div>
                    <label className="block text-xs text-muted mb-1">Premium {position === "BUY" ? "floor" : "ceiling"} %</label>
                    <input type="number" min={5} max={95} placeholder={position === "BUY" ? "30" : "50"} className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={premiumStop} onChange={(e) => setPremiumStop(e.target.value)} />
                  </div>
                ) : (
                  <div>
                    <label className="block text-xs text-muted mb-1">Target / stop (% of {PAYOFF_STRUCTURES.includes(structure) ? "max profit / risk" : DEBIT_STRUCTURES.includes(structure) ? "debit" : "credit"})</label>
                    <div className="flex gap-2">
                      <input type="number" min={5} max={95} placeholder="50" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={targetCredit} onChange={(e) => setTargetCredit(e.target.value)} />
                      <input type="number" min={10} max={500} placeholder={DEBIT_STRUCTURES.includes(structure) ? "50" : "100"} className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={stopCredit} onChange={(e) => setStopCredit(e.target.value)} />
                    </div>
                  </div>
                )}
                <div>
                  <label className="block text-xs text-muted mb-1">Max lots</label>
                  <input type="number" min={1} placeholder="risk-sized" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={maxLots} onChange={(e) => setMaxLots(e.target.value)} />
                </div>
              </div>

              {structure === "CUSTOM" && (
                <div className="rounded border border-border p-2">
                  <div className="text-[11px] font-bold uppercase tracking-wider text-muted mb-1">Legs ({customLegs.length}/{MAX_CUSTOM_LEGS})</div>
                  <div className="space-y-1 text-xs">
                    {customLegs.map((leg, i) => (
                      <div key={i} className="grid grid-cols-6 gap-2 items-center">
                        <select className="rounded bg-panel2 border border-border px-1 py-1" value={leg.role} onChange={(e) => updateLeg(i, { role: e.target.value as CustomLeg["role"] })}><option value="SHORT">Sell</option><option value="LONG">Buy</option></select>
                        <select className="rounded bg-panel2 border border-border px-1 py-1" value={leg.right} onChange={(e) => updateLeg(i, { right: e.target.value as CustomLeg["right"] })}><option value="CE">CE</option><option value="PE">PE</option></select>
                        <select className="rounded bg-panel2 border border-border px-1 py-1" value={leg.strike_rule} onChange={(e) => updateLeg(i, { strike_rule: e.target.value as StrikeRule })}><option value="ATM">ATM</option><option value="ITM">ITM</option><option value="OTM">OTM</option></select>
                        <input type="number" min={0} max={20} className="rounded bg-panel2 border border-border px-1 py-1" value={leg.strike_offset} onChange={(e) => updateLeg(i, { strike_offset: Number(e.target.value) })} title="steps" />
                        <input type="number" min={1} max={4} className="rounded bg-panel2 border border-border px-1 py-1" value={leg.ratio} onChange={(e) => updateLeg(i, { ratio: Math.max(1, Math.min(4, Number(e.target.value) || 1)) })} title="ratio" />
                        <button className="text-danger text-left" onClick={() => setCustomLegs((cur) => cur.filter((_, j) => j !== i))} disabled={customLegs.length <= 2}>remove</button>
                      </div>
                    ))}
                  </div>
                  <button className="mt-1 text-xs text-brand" disabled={customLegs.length >= MAX_CUSTOM_LEGS} onClick={() => setCustomLegs((cur) => [...cur, { right: "CE", role: "SHORT", strike_rule: "OTM", strike_offset: 1, ratio: 1 }])}>+ add leg</button>
                </div>
              )}

              <div className="grid sm:grid-cols-4 gap-3">
                <div>
                  <label className="block text-xs text-muted mb-1">Premiums from</label>
                  <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={pricing} onChange={(e) => setPricing(e.target.value as OptionPricingModel)}>
                    <option value="synthetic">Synthetic (Black-Scholes)</option>
                    <option value="snapshots">Recorded chain quotes</option>
                    <option value="uploaded">Uploaded chain CSV</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-muted mb-1">Implied volatility % {pricing !== "synthetic" ? "(fallback)" : ""}</label>
                  <input type="number" min={1} max={300} step="0.5" placeholder="realised vol" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={ivPct} onChange={(e) => setIvPct(e.target.value)} title="blank = annualised realised volatility of the trailing closes" />
                </div>
                <div>
                  <label className="block text-xs text-muted mb-1">Lot size / strike step</label>
                  <div className="flex gap-2">
                    <input type="number" min={1} placeholder="auto" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={lotSize} onChange={(e) => setLotSize(e.target.value)} />
                    <input type="number" min={0.05} step="0.05" placeholder="auto" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={strikeStep} onChange={(e) => setStrikeStep(e.target.value)} />
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-muted mb-1">Expiry day / weekly</label>
                  <div className="flex gap-2">
                    <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={expiryWeekday} onChange={(e) => setExpiryWeekday(e.target.value)}>
                      <option value="">auto</option>{WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
                    </select>
                    <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={weeklyExpiry} onChange={(e) => setWeeklyExpiry(e.target.value)}>
                      <option value="">auto</option><option value="yes">weekly</option><option value="no">monthly</option>
                    </select>
                  </div>
                </div>
              </div>
              <div className="flex flex-wrap gap-4 text-xs text-slate-300">
                <label className="flex items-center gap-1"><input type="checkbox" checked={intraday} onChange={(e) => setIntraday(e.target.checked)} /> intraday (no entries after 15:00, square-off 15:15 IST)</label>
                {pricing !== "synthetic" && <label className="flex items-center gap-1"><input type="checkbox" checked={allowFallback} onChange={(e) => setAllowFallback(e.target.checked)} /> price legs without a fresh quote synthetically</label>}
              </div>
              {pricing === "uploaded" && (
                <div className="text-xs">
                  <input type="file" accept=".csv,text/csv" onChange={(e) => onSnapshotFile(e.target.files?.[0] ?? null)} />
                  <span className="ml-2 text-muted">columns: timestamp, expiry, strike, right, ltp [, iv, oi, underlying_ltp]</span>
                  {uploadedRows.length > 0 && user && <button className="ml-2 text-brand" onClick={saveUploadedRows}>store as platform history</button>}
                  {uploadNote && <div className="text-muted mt-1">{uploadNote}</div>}
                </div>
              )}
              {pricing === "snapshots" && (
                <div className="text-xs text-muted">
                  {coverage.length === 0 ? "No recorded chain quotes yet - the worker records the chains of ACTIVE option deployments every few minutes while the market is open." :
                    <>Recorded: {coverage.map((c) => `${c.underlying} ${c.rows} quotes, ${c.expiries} expiries, ${c.from?.slice(0, 10)} to ${c.to?.slice(0, 10)}`).join(" · ")}</>}
                </div>
              )}
              <div className="text-[11px] text-muted">
                Synthetic premiums approximate the market (no smile, no bid/ask, no liquidity): use them to study structure mechanics - strikes, expiries, exits, sizing - not to claim an edge. Recorded quotes are what the market actually showed.
              </div>
            </div>
          )}
        </div>
      </Card>

      {error && <div className="text-sm text-danger">{error}</div>}

      {result && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatTile label="Total Trades" value={result.total_trades} />
            <StatTile label="Win Rate" value={`${result.win_rate.toFixed(1)}%`} />
            <StatTile label="Net P&L" value={result.net_pnl.toFixed(2)} tone={result.net_pnl >= 0 ? "up" : "down"} />
            <StatTile label="Max Drawdown" value={result.max_drawdown.toFixed(2)} tone="down" />
            <StatTile label="Profit Factor" value={result.profit_factor?.toFixed(2) ?? "-"} />
            <StatTile label="Avg Win" value={result.avg_win.toFixed(2)} tone="up" />
            <StatTile label="Avg Loss" value={result.avg_loss.toFixed(2)} tone="down" />
            <StatTile label="Expectancy" value={result.expectancy.toFixed(2)} />
          </div>

          <Card title="Price Chart — strategy indicators, trade entries &amp; exits">
            <ProChart candles={chartCandles} symbol={symbol} timeframe={selected?.timeframes[0]} markers={tradeMarkers} strategyParams={selected?.default_params} />
          </Card>

          <Card title="Equity Curve">
            <EquityCurveChart equity={result.equity_curve} />
          </Card>

          {result.options && (
            <Card title={`Option structures (${result.options.structures_opened})`}>
              <div className="grid sm:grid-cols-4 gap-3 mb-3">
                <StatTile label="Structure" value={result.options.structure.replace(/_/g, " ").toLowerCase() + (result.options.position ? ` · ${result.options.position.toLowerCase()}` : "")} />
                <StatTile label="Lot / strike step" value={`${result.options.lot_size} / ${result.options.strike_step}`} />
                <StatTile label="Expiries" value={result.options.expiry_calendar} />
                <StatTile label="Expiry settlements" value={result.options.expiry_settlements} />
              </div>
              <div className="text-xs text-slate-300 mb-2">Premiums: {result.options.pricing}
                {result.options.snapshot_hits != null ? ` · ${result.options.snapshot_hits} recorded quotes used, ${result.options.synthetic_fallbacks ?? 0} synthetic fallbacks` : ""}
              </div>
              {Object.keys(result.options.signals_skipped).length > 0 && (
                <div className="text-xs text-muted mb-2">Signals not traded: {Object.entries(result.options.signals_skipped).map(([k, v]) => `${k} (${v})`).join("; ")}</div>
              )}
              <div className="flex items-center gap-2 text-xs mb-1">
                <span className="text-muted">Sort</span>
                <button className={`px-2 py-0.5 rounded border border-border ${structureSort === "time" ? "bg-panel2" : ""}`} onClick={() => setStructureSort("time")}>time</button>
                <button className={`px-2 py-0.5 rounded border border-border ${structureSort === "pnl" ? "bg-panel2" : ""}`} onClick={() => setStructureSort("pnl")}>P&amp;L</button>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="text-muted uppercase text-[10px] tracking-wide">
                    <tr className="text-left"><th className="py-1 pr-3">Structure</th><th className="py-1 pr-3">Legs (entry → exit)</th><th className="py-1 pr-3">Lots</th><th className="py-1 pr-3">Credit / debit</th><th className="py-1 pr-3">Max loss</th><th className="py-1 pr-3">Exit</th><th className="py-1 pr-3">P&amp;L</th></tr>
                  </thead>
                  <tbody>
                    {[...result.options.structures].sort((a, b) => (structureSort === "pnl" ? a.pnl - b.pnl : a.entry_time.localeCompare(b.entry_time))).map((st, i) => (
                      <tr key={i} className="border-t border-border align-top">
                        <td className="py-1 pr-3 text-slate-200 whitespace-nowrap">{st.label}<div className="text-muted">{st.entry_time.slice(0, 16).replace("T", " ")} → {st.exit_time.slice(0, 16).replace("T", " ")}</div></td>
                        <td className="py-1 pr-3">{st.legs.map((l, j) => <div key={j}>{l.role === "SHORT" ? "sell" : "buy"} {l.ratio > 1 ? `${l.ratio}x ` : ""}{l.strike} {l.right} @ {l.entry_price.toFixed(2)} → {l.exit_price?.toFixed(2) ?? "-"}</div>)}</td>
                        <td className="py-1 pr-3">{st.lots}</td>
                        <td className="py-1 pr-3">{st.net_credit != null ? (st.net_credit >= 0 ? `credit ${st.net_credit.toFixed(2)}` : `debit ${(-st.net_credit).toFixed(2)}`) : "-"}</td>
                        <td className="py-1 pr-3">{st.max_loss != null ? st.max_loss.toFixed(2) : "undefined"}</td>
                        <td className="py-1 pr-3">{st.exit_reason}</td>
                        <td className={`py-1 pr-3 whitespace-nowrap ${st.pnl >= 0 ? "text-accent" : "text-danger"}`}>{st.pnl.toFixed(0)}<div className="text-muted">charges {st.charges.toFixed(0)}</div></td>
                      </tr>
                    ))}
                    {result.options.structures.length === 0 && <tr><td colSpan={7} className="py-3 text-center text-muted">No structure was opened.</td></tr>}
                  </tbody>
                </table>
              </div>
              <div className="mt-2 text-[11px] text-muted">{result.options.disclaimer}</div>
            </Card>
          )}

          {result.analytics && (
            <Card title="Analytics">
              <div className="grid sm:grid-cols-4 gap-3 mb-3">
                <StatTile label="CAGR" value={result.analytics.ratios.cagr_pct != null ? `${result.analytics.ratios.cagr_pct}%` : "-"} />
                <StatTile label="Sharpe / Sortino" value={`${result.analytics.ratios.sharpe ?? "-"} / ${result.analytics.ratios.sortino ?? "-"}`} />
                <StatTile label="Streaks (W / L)" value={`${result.analytics.streaks.max_consecutive_wins} / ${result.analytics.streaks.max_consecutive_losses}`} />
                <StatTile label="Avg hold" value={result.analytics.holding_minutes.avg != null ? `${result.analytics.holding_minutes.avg} min` : "-"} />
              </div>
              <div className="grid md:grid-cols-2 gap-4 text-xs">
                {([["Monthly", result.analytics.monthly], ["Day of week", result.analytics.day_of_week], ["Hour of day", result.analytics.hour_of_day], ["Exit reasons", result.analytics.exit_reasons]] as const).map(([title, rows]) => (
                  <div key={title}>
                    <div className="text-[10px] uppercase tracking-wide text-muted mb-1">{title}</div>
                    {rows.length === 0 ? <div className="text-muted">-</div> : (
                      <table className="w-full">
                        <tbody>
                          {rows.map((r) => (
                            <tr key={r.key} className="border-t border-border">
                              <td className="py-0.5 pr-2 text-slate-300">{r.key}</td>
                              <td className="py-0.5 pr-2 text-muted">{r.trades} trades</td>
                              <td className="py-0.5 pr-2 text-muted">{r.win_rate}% win</td>
                              <td className={`py-0.5 text-right ${r.pnl >= 0 ? "text-accent" : "text-danger"}`}>{r.pnl.toFixed(0)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                ))}
              </div>
              <div className="mt-3 text-[11px] text-muted">
                Charges {result.analytics.costs.total_charges.toFixed(0)} ({result.analytics.costs.charges_pct_of_gross ?? "-"}% of gross)
                {result.analytics.slippage.avg_per_unit != null ? ` · avg slippage ${result.analytics.slippage.avg_per_unit}/unit` : ""}
                {result.run_id ? ` · saved as run #${result.run_id}` : ""}
              </div>
            </Card>
          )}

          <Card title="Parameter optimisation (in-sample search, out-of-sample ranking)">
            <div className="flex flex-wrap items-center gap-2 text-xs mb-2">
              <input className="flex-1 min-w-[260px] rounded bg-panel2 border border-border px-2 py-1 font-mono" value={optGrid} onChange={(e) => setOptGrid(e.target.value)} />
              <select className="rounded bg-panel2 border border-border px-1 py-1" value={optMetric} onChange={(e) => setOptMetric(e.target.value)}>
                {["net_pnl", "expectancy", "profit_factor", "win_rate"].map((m) => <option key={m}>{m}</option>)}
              </select>
              <button onClick={runOptimize} disabled={optBusy || !result} className="rounded border border-border hover:bg-panel2 px-3 py-1 text-slate-200 disabled:opacity-50">{optBusy ? "Searching…" : "Optimise"}</button>
              <span className="text-muted">Grid as JSON (max 60 combinations). First 70% of the bars fit, last 30% judge.</span>
            </div>
            {optError && <div className="text-xs text-danger">{optError}</div>}
            {optResult && (
              <div className="text-xs">
                <div className="text-muted mb-1">{optResult.combinations} combinations · {optResult.in_sample_bars} in-sample / {optResult.out_of_sample_bars} out-of-sample bars · {optResult.robust_count} robust</div>
                <table className="w-full"><thead className="text-muted uppercase text-[10px]"><tr className="text-left"><th className="py-1 pr-3">Params</th><th className="py-1 pr-3">In-sample {optResult.metric}</th><th className="py-1 pr-3">Out-of-sample {optResult.metric}</th><th className="py-1 pr-3">OOS trades</th><th className="py-1 pr-3">Overfit gap</th><th className="py-1 pr-3">Flags</th></tr></thead>
                  <tbody>{optResult.results.slice(0, 12).map((r: OptimizeResult["results"][number], i: number) => {
                    const key = optResult.metric as keyof OptimizeResult["results"][number]["in_sample"];
                    const ins = r.in_sample[key]; const oos = r.out_of_sample[key];
                    return (
                      <tr key={i} className={`border-t border-border/60 ${i === 0 ? "text-accent" : ""}`}>
                        <td className="py-1 pr-3 font-mono">{JSON.stringify(r.params)}</td>
                        <td className="py-1 pr-3">{typeof ins === "number" ? ins.toFixed(2) : "-"}</td>
                        <td className="py-1 pr-3">{typeof oos === "number" ? oos.toFixed(2) : "-"}</td>
                        <td className="py-1 pr-3">{r.out_of_sample.trades}</td>
                        <td className={`py-1 pr-3 ${(r.overfit_gap ?? 0) > 0 ? "text-amber-400" : ""}`}>{r.overfit_gap?.toFixed(2) ?? "-"}</td>
                        <td className="py-1 pr-3 text-muted">{r.flags.join("; ")}</td>
                      </tr>
                    );
                  })}</tbody></table>
                <div className="text-[11px] text-muted mt-1">{optResult.note}</div>
              </div>
            )}
          </Card>

          <Card title="Robustness">
            <div className="flex flex-wrap items-center gap-3 text-xs mb-3">
              <button onClick={runRobustness} disabled={robustBusy || !result} className="rounded border border-border hover:bg-panel2 px-3 py-1 text-slate-200 disabled:opacity-50">
                {robustBusy ? "Running…" : "Run Monte Carlo + walk-forward"}
              </button>
              <span className="text-muted">Monte Carlo reshuffles the trade sequence; walk-forward reruns the same parameters on consecutive windows.</span>
            </div>
            {monteCarlo && monteCarlo.runs > 0 && monteCarlo.final_pnl && monteCarlo.max_drawdown && (
              <div className="grid sm:grid-cols-4 gap-3 text-xs mb-3">
                <StatTile label="P&L p5 / p50 / p95" value={`${monteCarlo.final_pnl.p5.toFixed(0)} / ${monteCarlo.final_pnl.p50.toFixed(0)} / ${monteCarlo.final_pnl.p95.toFixed(0)}`} />
                <StatTile label="Max DD p50 / p95" value={`${monteCarlo.max_drawdown.p50.toFixed(0)} / ${monteCarlo.max_drawdown.p95.toFixed(0)}`} tone="down" />
                <StatTile label="P(loss)" value={`${monteCarlo.probability_of_loss_pct}%`} tone={(monteCarlo.probability_of_loss_pct ?? 0) > 30 ? "down" : "up"} />
                <StatTile label="P(DD > original)" value={`${monteCarlo.probability_dd_exceeds_original_pct}%`} />
              </div>
            )}
            {monteCarlo && monteCarlo.runs === 0 && <div className="text-xs text-muted">{monteCarlo.note}</div>}
            {walkForward && (
              walkForward.folds === 0 ? <div className="text-xs text-muted">{walkForward.note}</div> : (
                <div className="text-xs">
                  <div className="mb-1 text-slate-300">Consistency {walkForward.consistency_pct}% ({walkForward.profitable_windows}/{walkForward.folds} windows profitable) · mean window P&L {walkForward.mean_window_pnl}</div>
                  <table className="w-full">
                    <thead className="text-muted uppercase text-[10px] tracking-wide"><tr className="text-left"><th className="py-1 pr-3">Window</th><th className="py-1 pr-3">Bars</th><th className="py-1 pr-3">Trades</th><th className="py-1 pr-3">Net P&L</th><th className="py-1 pr-3">Win %</th><th className="py-1 pr-3">Max DD</th></tr></thead>
                    <tbody>
                      {walkForward.windows.map((w) => (
                        <tr key={w.window} className="border-t border-border">
                          <td className="py-1 pr-3">{w.window}</td><td className="py-1 pr-3">{w.bars}</td><td className="py-1 pr-3">{w.trades}</td>
                          <td className={`py-1 pr-3 ${w.net_pnl >= 0 ? "text-accent" : "text-danger"}`}>{w.net_pnl.toFixed(0)}</td>
                          <td className="py-1 pr-3">{w.win_rate}%</td><td className="py-1 pr-3">{w.max_drawdown.toFixed(0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="mt-1 text-muted">{walkForward.note}</div>
                </div>
              )
            )}
          </Card>

          <Card title={`Trades (${result.trades.length})`}>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-muted uppercase text-[10px] tracking-wide">
                  <tr className="text-left">
                    <th className="py-1 pr-3">Direction</th>
                    <th className="py-1 pr-3">Entry</th>
                    <th className="py-1 pr-3">Exit</th>
                    <th className="py-1 pr-3">Qty</th>
                    <th className="py-1 pr-3">Reason</th>
                    <th className="py-1 pr-3">P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((t, i) => (
                    <tr key={i} className="border-t border-border">
                      <td className="py-1 pr-3">{t.direction}{result.options ? <span className="text-muted"> · {t.symbol}</span> : null}</td>
                      <td className="py-1 pr-3">{t.entry_price.toFixed(2)}</td>
                      <td className="py-1 pr-3">{t.exit_price?.toFixed(2) ?? "-"}</td>
                      <td className="py-1 pr-3">{t.quantity}</td>
                      <td className="py-1 pr-3">{t.exit_reason ?? "-"}</td>
                      <td className={`py-1 pr-3 ${(t.pnl ?? 0) >= 0 ? "text-accent" : "text-danger"}`}>
                        {t.pnl?.toFixed(2) ?? "-"}
                      </td>
                    </tr>
                  ))}
                  {result.trades.length === 0 && (
                    <tr>
                      <td colSpan={6} className="py-3 text-center text-muted">
                        No trades were triggered on this sample dataset.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      {user && runs.length > 0 && (
        <Card title={`Backtest history (${runs.length})`}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted uppercase text-[10px] tracking-wide">
                <tr className="text-left"><th className="py-1 pr-3">When</th><th className="py-1 pr-3">Strategy</th><th className="py-1 pr-3">Symbol</th><th className="py-1 pr-3">Data</th><th className="py-1 pr-3">Exits</th><th className="py-1 pr-3">Trades</th><th className="py-1 pr-3">Net P&L</th><th className="py-1 pr-3">Win %</th><th className="py-1 pr-3">Max DD</th></tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className="border-t border-border">
                    <td className="py-1 pr-3 text-muted whitespace-nowrap">{r.created_at ? new Date(r.created_at).toLocaleString() : "-"}</td>
                    <td className="py-1 pr-3 text-slate-200">{r.strategy_id}</td>
                    <td className="py-1 pr-3">{r.symbol} · {r.base_timeframe}</td>
                    <td className="py-1 pr-3 text-muted">{r.data_source} · {r.bars} bars · engine v{r.engine_version}</td>
                    <td className="py-1 pr-3 text-muted">{r.exit_rules ? Object.entries(r.exit_rules).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ") : "-"}</td>
                    <td className="py-1 pr-3">{r.total_trades ?? "-"}</td>
                    <td className={`py-1 pr-3 ${(r.net_pnl ?? 0) >= 0 ? "text-accent" : "text-danger"}`}>{r.net_pnl?.toFixed(0) ?? "-"}</td>
                    <td className="py-1 pr-3">{r.win_rate ?? "-"}%</td>
                    <td className="py-1 pr-3">{r.max_drawdown?.toFixed(0) ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
