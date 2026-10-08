import { Activity, CheckCircle2, Compass, ScanSearch, Settings2, ShieldAlert, Sparkles, Sun, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, Disclaimer } from "../components/ui";
import type { AiAction, AiStrategyDraft, Condition, Regime } from "../types";
import { DataSourceBar, useCandleSource } from "../components/DataSource";
import StrategyInterview from "../components/StrategyInterview";
import MarketMemoryCard from "../components/MarketMemoryCard";
import DailyBriefing from "../components/DailyBriefing";
import StrategistPanel from "../components/StrategistPanel";
import ThesisCard from "../components/ThesisCard";
import AiAcknowledgementGate from "../components/AiAcknowledgementGate";
import { INTERVIEW_MR } from "../i18n/interviewSecondary";

const input = "w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm";

function operandLabel(o: Condition["left"]): string {
  return o.type === "value" ? String(o.value) : `${o.indicator}${o.period ? `(${o.period})` : ""}`;
}
const OPS: Record<string, string> = { GT: ">", LT: "<", GTE: ">=", LTE: "<=", CROSSES_ABOVE: "crosses above", CROSSES_BELOW: "crosses below" };

function StatusBadge({ status }: { status: string }) {
  const cls = status === "APPROVED" || status === "EXECUTED" ? "border-emerald-500/40 text-emerald-400" : status === "BACKTESTED" || status === "PROPOSED" ? "border-amber-500/40 text-amber-400"
    : status === "REJECTED" || status === "FAILED" ? "border-rose-500/40 text-rose-400" : "border-border text-muted";
  return <span className={`rounded-md border px-2 py-0.5 text-[11px] font-bold ${cls}`}>{status}</span>;
}

type Tab = "strategist" | "today" | "strategy" | "advanced";
/** P1.1: each tab has its own address - /ai-copilot/study, /today, /interview, /drafts. */
const TABS: { id: Tab; slug: string; en: string; icon: typeof Sun }[] = [
  { id: "strategist", slug: "study", en: "Market study & templates", icon: ScanSearch },
  { id: "today", slug: "today", en: "Today's market", icon: Sun },
  { id: "strategy", slug: "interview", en: "Strategy interview", icon: Compass },
  { id: "advanced", slug: "drafts", en: "Drafts & agent", icon: Settings2 },
];
function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try { const v = localStorage.getItem(key) as T | null; return v && allowed.includes(v) ? v : fallback; } catch { return fallback; }
}
function store(key: string, value: string) { try { localStorage.setItem(key, value); } catch { /* storage unavailable */ } }

/** Phase L: the AI Copilot - generate a strategy draft, backtest it, approve it (only then does
 * it exist as a strategy); read the market regime; decide on the monitoring agent's proposals.
 * Phase AV/AW: market study and rule templates (default tab), today's market briefing, the strategy
 * interview and the draft / agent tools. The coach and the guide have their own page.
 * P0.9: English only (no language toggle); it explains rules and data - decisions are the trader's. */
export default function AiCopilotPage() {
  const { user } = useAuth();
  const [prompt, setPrompt] = useState("");
  const [drafts, setDrafts] = useState<AiStrategyDraft[]>([]);
  const [selected, setSelected] = useState<AiStrategyDraft | null>(null);
  const [actions, setActions] = useState<AiAction[]>([]);
  const [regime, setRegime] = useState<Regime | null>(null);
  const [symbol, setSymbol] = useState("NIFTY 50");   // Phase AK: a real default; sample mode still synthesises candles for any symbol
  const [busy, setBusy] = useState(false);
  const [acceptRisk, setAcceptRisk] = useState(false);
  const [showChecks, setShowChecks] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // Phase AP: the interview runs before any strategy is proposed for a vague request.
  const [interviewKey, setInterviewKey] = useState(0);
  const [interviewPrompt, setInterviewPrompt] = useState("");
  // The address decides the tab; a bare /ai-copilot opens the tab used last (remembered in this browser).
  const params = useParams<{ tab?: string }>();
  const navigate = useNavigate();
  const fromUrl = TABS.find((t) => t.slug === params.tab)?.id;
  const tab: Tab = fromUrl ?? stored<Tab>("atp_copilot_tab", TABS.map((t) => t.id), "strategist");
  const slugOf = (t: Tab) => TABS.find((x) => x.id === t)?.slug ?? "study";
  useEffect(() => {
    if (!fromUrl) navigate(`/ai-copilot/${slugOf(tab)}`, { replace: true });
    else store("atp_copilot_tab", fromUrl);
  }, [fromUrl]); // eslint-disable-line react-hooks/exhaustive-deps
  const setTab = (t: Tab) => { store("atp_copilot_tab", t); navigate(`/ai-copilot/${slugOf(t)}`); };
  const startInterview = (text: string) => {
    setTab("strategy");
    setInterviewPrompt(text); setInterviewKey((k) => k + 1);
    window.setTimeout(() => document.getElementById("strategy-interview")?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };

  function refresh() {
    api.aiDrafts().then(setDrafts).catch((e) => setError(String(e)));
    api.aiActions().then(setActions).catch(() => {});
  }
  useEffect(() => { if (user) refresh(); }, [user]);

  async function run(label: string | null, fn: () => Promise<unknown>) {
    setBusy(true); setError(null); setMessage(null);
    try { await fn(); if (label) setMessage(label); refresh(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  // Phase AD: sample candles or the tenant's broker candles for the draft backtest and the regime read.
  const source = useCandleSource(30);
  const candlesFor = async (timeframe: string) => {
    const r = await source.fetch([symbol], timeframe, { count: 600, startPriceFor: () => 100, seedFor: () => 11 });
    const c = r.candles[symbol.trim().toUpperCase()];
    if (!c?.length) throw new Error(`No candles for ${symbol}`);
    return c;
  };
  const dataLabel = source.mode === "broker" ? "broker candles" : "sample data";

  if (!user) return <Card><p className="text-sm text-muted">Log in to use the AI Copilot.</p></Card>;
  const open = actions.filter((a) => a.status === "PROPOSED");
  const decided = actions.filter((a) => a.status !== "PROPOSED").slice(0, 10);

  // P0.8-D: nothing AI-written is shown until this user accepted the current acknowledgement (shared gate).
  return (
    <AiAcknowledgementGate>
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1 min-w-[260px]">
          <h1 className="text-xl font-extrabold text-purple-400 flex items-center gap-2"><Sparkles size={18} /> AI Copilot</h1>
          <p className="text-sm text-purple-200">Explains rules and data - market reads, templates, backtests and risk settings. Decisions are yours; nothing trades without your approval.</p>
        </div>
      </div>

      <DataSourceBar source={source} />

      {open.length > 0 && (
        <Card title={`Proposals waiting for your decision (${open.length})`}>
          {open.map((a) => (
            <div key={a.id} className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 mb-2">
              <div className="flex items-center gap-2 text-sm font-bold"><ShieldAlert size={14} className="text-amber-400" /> {a.action.replace(/_/g, " ")} <span className="text-[11px] text-muted font-normal">· rule {a.rule} · deployment #{a.deployment_id ?? "-"}{a.trade_id ? ` · position #${a.trade_id}` : ""}</span></div>
              <div className="text-xs text-slate-300 whitespace-pre-wrap mt-1">{a.reason}</div>
              <div className="text-[11px] text-muted mt-1">evidence: {JSON.stringify(a.evidence)} · expires {a.expires_at ? new Date(a.expires_at).toLocaleString() : "-"}</div>
              <div className="flex gap-2 mt-2">
                <button disabled={busy} onClick={() => run("Approved and executed.", () => api.aiApproveAction(a.id))} className="rounded bg-emerald-600 hover:bg-emerald-500 text-white font-semibold px-3 py-1 text-xs"><CheckCircle2 size={12} className="inline mr-1" />Approve & execute</button>
                <button disabled={busy} onClick={() => { const note = window.prompt("Why reject? (optional)") ?? ""; void run("Rejected.", () => api.aiRejectAction(a.id, note || undefined)); }} className="rounded border border-rose-500/40 text-rose-400 px-3 py-1 text-xs"><XCircle size={12} className="inline mr-1" />Reject</button>
              </div>
            </div>
          ))}
        </Card>
      )}

      <div className="flex flex-wrap gap-1 border-b border-border">
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.id;
          return (
            <button key={t.id} onClick={() => setTab(t.id)}
                    className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-semibold ${active ? "border-purple-400 text-purple-100" : "border-transparent text-muted hover:text-slate-200"}`}>
              <Icon size={14} />{t.en}
              {t.id === "strategy" && drafts.some((d) => d.status === "BACKTESTED") && <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />}
            </button>
          );
        })}
      </div>

      {tab === "today" && (
        <div className="space-y-4">
          <DailyBriefing />
          <MarketMemoryCard />
          <ThesisCard />
        </div>
      )}

      {tab === "strategist" && <StrategistPanel source={source} />}

      {tab === "strategy" && (
        <div className="space-y-4">
      <div id="strategy-interview">
        <Card title="Strategy interview">
          {interviewKey === 0 ? (
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm text-slate-200 flex-1 min-w-[260px]">
                A few questions first - capital, risk, style, time and goal. Then the market data is read (trend, structure, support/resistance) and three templates are shown with their rules, backtest and risk settings. You choose the template; this is not a recommendation.
                <span className="block text-[11px] text-muted mt-1" lang="mr">{INTERVIEW_MR.start}</span>
              </p>
              <button onClick={() => startInterview("")} className="rounded bg-purple-600 hover:bg-purple-500 text-white font-bold px-4 py-2 text-sm"><Compass size={14} className="inline mr-1" />Start</button>
            </div>
          ) : (
            <>
              <StrategyInterview source={source} startPrompt={interviewPrompt} startKey={interviewKey} onDraft={(d) => { setSelected(d); refresh(); }} />
              <button onClick={() => startInterview("")} className="mt-2 text-xs text-sky-300 hover:underline">Start over</button>
            </>
          )}
        </Card>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Card title="Generate a strategy draft">
          <p className="text-xs text-muted mb-2">Describe entries in plain language. The draft targets the same rule schema as the Strategy Builder; approve only after a backtest you have read.</p>
          <textarea className={input} rows={4} placeholder="e.g. Buy pullbacks in a 5-minute uptrend: EMA20 above EMA50, RSI(14) crossing back above 40; 1.5 ATR stop, 1:2 target. Or simply: give me a trading strategy" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          <button disabled={busy || prompt.trim().length < 10} onClick={() => run(null, async () => {
            // Phase AP: "give me a strategy" with no rules in it starts the interview instead of guessing.
            const s = await api.aiInterviewStart(prompt);
            if (s.needs_interview) { setMessage("A few questions first - see the strategy interview."); startInterview(prompt); return; }
            setMessage("Draft generated - review it on the right.");
            const d = await api.aiGenerate(prompt, { language: "en", regime: regime?.kind ?? null, symbol: symbol.trim() ? symbol : null }); setSelected(d); })} className="mt-2 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-xs disabled:opacity-50">{busy ? "Working…" : "Generate draft"}</button>

          <div className="mt-4">
            <div className="text-[11px] font-bold uppercase tracking-wider text-muted mb-1">Recent drafts</div>
            {drafts.length === 0 ? <div className="text-xs text-muted">None yet.</div> : (
              <table className="w-full text-xs"><tbody>
                {drafts.slice(0, 12).map((d) => (
                  <tr key={d.id} className={`border-t border-border/60 cursor-pointer hover:bg-panel2/60 ${selected?.id === d.id ? "bg-panel2/60" : ""}`} onClick={() => api.aiDraft(d.id).then(setSelected)}>
                    <td className="py-1 text-muted">#{d.id}</td>
                    <td className="py-1 truncate max-w-[260px]" title={d.prompt}>{d.config?.name ?? d.prompt.slice(0, 40)}</td>
                    <td className="py-1 text-muted">{d.provider}</td>
                    <td className="py-1 text-right"><StatusBadge status={d.status} /></td>
                  </tr>
                ))}
              </tbody></table>
            )}
          </div>
        </Card>

        <Card title={selected ? `Draft #${selected.id} - ${selected.status}` : "Draft review"}>
          {!selected ? <div className="text-xs text-muted">Generate or pick a draft to review its rules, backtest it and approve it.</div> : (
            <div className="space-y-2 text-xs">
              <div className="text-muted">via {selected.lineage.provider} / {selected.lineage.model} · {selected.created_at ? new Date(selected.created_at).toLocaleString() : ""}</div>
              {selected.explanation && <div className="text-slate-300 whitespace-pre-wrap">{selected.explanation}</div>}
              {selected.warnings.length > 0 && <ul className="list-disc pl-4 text-amber-300">{selected.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
              {selected.config && (
                <div className="rounded-lg border border-border bg-panel2/40 p-2 space-y-1">
                  <div className="font-bold">{selected.config.name} · {selected.config.timeframe} · stop {selected.config.stop_loss_atr_mult}×ATR({selected.config.atr_period}) · targets {selected.config.target_rr.join("R / ")}R</div>
                  {(["long_conditions", "short_conditions"] as const).map((side) => selected.config![side].length > 0 && (
                    <div key={side}><span className="text-muted">{side === "long_conditions" ? "LONG when" : "SHORT when"}</span> {selected.config![side].map((c, i) => <span key={i} className="inline-block rounded border border-border px-1.5 py-0.5 mr-1 mb-1">{operandLabel(c.left)} {OPS[c.operator] ?? c.operator} {operandLabel(c.right)}</span>)}</div>
                  ))}
                </div>
              )}
              {selected.deployment && (
                <div className="rounded-lg border border-border bg-panel2/40 p-2 space-y-0.5">
                  <div className="font-bold">Suggested Autopilot settings <span className="text-muted font-normal">· next step: {selected.deployment.next_step.replace("_", " ")}{selected.prompt_version ? ` · prompt ${selected.prompt_version}` : ""}</span></div>
                  <div className="text-slate-300">{selected.deployment_text}</div>
                  <div className="text-muted">{selected.deployment.instrument_kind}{selected.deployment.option_strategy !== "SINGLE" ? ` · ${selected.deployment.option_strategy}` : selected.deployment.option_position ? ` · ${selected.deployment.option_position}` : ""}{selected.deployment.expiry_rule ? ` · ${selected.deployment.expiry_rule} expiry` : ""}{selected.deployment.strike_rule ? ` · ${selected.deployment.strike_rule}${selected.deployment.strike_offset || ""}` : ""}{selected.deployment.target_credit_pct != null ? ` · target ${selected.deployment.target_credit_pct}% / stop ${selected.deployment.stop_credit_pct ?? "default"}% of credit` : ""}</div>
                  <div className="text-[11px] text-muted">Copy these into the Autopilot form when you deploy the approved strategy; the engine's sizing and guardian rules apply on top.</div>
                </div>
              )}
              {selected.compliance && (
                <div className={`rounded-lg border p-2 space-y-1 ${selected.compliance.ok ? "border-border bg-panel2/40" : "border-rose-500/50 bg-rose-500/5"}`}>
                  <div className="flex items-center justify-between">
                    <span className="font-bold">Risk Guardian checklist · {selected.compliance.passed.length} passed{selected.compliance.failed.length ? ` · ${selected.compliance.failed.length} failed` : ""}{selected.compliance.warnings.length ? ` · ${selected.compliance.warnings.length} warning(s)` : ""}</span>
                    <button onClick={() => setShowChecks(!showChecks)} className="text-sky-400 hover:underline">{showChecks ? "hide" : "show all"}</button>
                  </div>
                  {selected.compliance.fixes.length > 0 && <div className="text-amber-300">Auto-fixed: {selected.compliance.fixes.join("; ")}</div>}
                  {selected.compliance.checks.filter((c) => showChecks || c.status === "FAIL" || c.status === "WARN").map((c) => (
                    <div key={c.rule} className="flex gap-2">
                      <span className={`font-mono w-8 shrink-0 ${c.status === "PASS" ? "text-accent" : c.status === "FAIL" ? "text-danger" : c.status === "WARN" ? "text-amber-400" : "text-muted"}`}>{c.rule}</span>
                      <span className={c.status === "N/A" ? "text-muted" : "text-slate-300"}>{c.detail}</span>
                    </div>
                  ))}
                  {selected.compliance.evidence && (
                    <div className={selected.compliance.evidence.strength === "weak" ? "text-amber-300" : "text-slate-300"}>Backtest evidence ({selected.compliance.evidence.strength}): {selected.compliance.evidence.summary}</div>
                  )}
                  <div className="rounded border border-amber-500/40 bg-amber-500/5 p-2 text-amber-200">
                    <div className="font-bold">You must accept before approving</div>
                    <div>{selected.compliance.user_must_accept.max_loss_per_trade_text}</div>
                    <div className="mt-1">{selected.compliance.user_must_accept.worst_case_text}</div>
                    {selected.status === "BACKTESTED" && (
                      <label className="mt-1 flex items-center gap-2 text-slate-200"><input type="checkbox" checked={acceptRisk} onChange={(e) => setAcceptRisk(e.target.checked)} /> I accept this maximum loss and worst case.</label>
                    )}
                  </div>
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <input className="rounded bg-panel2 border border-border px-2 py-1 text-xs w-28" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
                {(selected.status === "DRAFT" || selected.status === "BACKTESTED") && (
                  <button disabled={busy} onClick={() => run("Backtest recorded on the draft.", async () => { const r = await api.aiBacktestDraft(selected.id, symbol, selected.config?.timeframe ?? "1min", await candlesFor(selected.config?.timeframe ?? "1min")); setSelected(r.draft);
                    // P0.9: figures from sample candles are never shown as performance.
                    setMessage(source.mode === "sample" ? `Backtest recorded on SAMPLE data (${r.result.total_trades} trades) - its figures are not real performance; switch Data to broker candles for a real read.`
                      : `Backtest: ${r.result.total_trades} trades, win rate ${(r.result.win_rate * (r.result.win_rate <= 1 ? 100 : 1)).toFixed(0)}%, net P&L ${r.result.net_pnl.toFixed(0)} (${dataLabel}).`); })} className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1 text-xs">Backtest on {dataLabel}</button>
                )}
                {selected.status === "BACKTESTED" && (
                  <button disabled={busy || !acceptRisk || (selected.compliance ? !selected.compliance.ok : false)} title={!acceptRisk ? "Tick the acceptance first" : undefined} onClick={() => run("Approved - it is now one of your strategies. Paper-trade it before LIVE.", async () => { const r = await api.aiApproveDraft(selected.id, undefined, true); setSelected(r.draft); setAcceptRisk(false); })} className="rounded bg-emerald-600 hover:bg-emerald-500 text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">Approve as strategy</button>
                )}
                {selected.status !== "APPROVED" && selected.status !== "REJECTED" && (
                  <button disabled={busy} onClick={() => run("Rejected.", async () => setSelected(await api.aiRejectDraft(selected.id)))} className="text-xs text-danger hover:underline">Reject</button>
                )}
                {selected.strategy_id && <span className="text-emerald-400">saved as {selected.strategy_id}</span>}
                {selected.backtest_run_id && <span className="text-muted">backtest run #{selected.backtest_run_id}</span>}
              </div>
              <div className="text-[11px] text-muted">{selected.disclaimer}</div>
            </div>
          )}
        </Card>
      </div>

        </div>
      )}

      {tab === "advanced" && (
        <div className="space-y-4">
      <div className="grid lg:grid-cols-2 gap-4">
        <Card title={`Market regime (${dataLabel})`}>
          <p className="text-xs text-muted mb-2">The same classifier the Autopilot uses for a deployment's regime filter: ADX for trend strength, EMA20/50 for direction, ATR against its median for volatility.</p>
          <button disabled={busy} onClick={() => run(null, async () => setRegime(await api.aiRegime(await candlesFor("5min"))))} className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1 text-xs"><Activity size={12} className="inline mr-1" />Classify {symbol} ({dataLabel})</button>
          {regime && (
            <div className="mt-2 text-xs">
              <div className="font-bold text-sm">{regime.kind.replace("_", " ")} <span className="text-muted font-normal">confidence {regime.confidence}</span></div>
              <ul className="list-disc pl-4 text-slate-300 mt-1">{regime.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
          )}
        </Card>

        <Card title="Decided proposals">
          {decided.length === 0 ? <div className="text-xs text-muted">Nothing decided yet. The monitoring agent watches active deployments for losing streaks, daily drawdown, error streaks, stale positions in adverse regimes and win-rate drift.</div> : (
            <table className="w-full text-xs"><tbody>
              {decided.map((a) => (
                <tr key={a.id} className="border-t border-border/60">
                  <td className="py-1 text-muted">{a.created_at ? new Date(a.created_at).toLocaleString() : ""}</td>
                  <td className="py-1">{a.action.replace(/_/g, " ")} <span className="text-muted">({a.rule})</span></td>
                  <td className="py-1"><StatusBadge status={a.status} /></td>
                  <td className="py-1 text-muted">{a.result ?? a.decision_note ?? ""}</td>
                </tr>
              ))}
            </tbody></table>
          )}
        </Card>
      </div>

        </div>
      )}

      <Disclaimer kind="ai" />
      {error && <div className="text-sm text-danger">{error}</div>}
      {message && <div className="text-sm text-accent">{message}</div>}
    </div>
    </AiAcknowledgementGate>
  );
}
