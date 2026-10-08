import { CheckCircle2, FileText, FlaskConical, Pencil, Sparkles, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { api } from "../../../api/client";
import { Badge, Button, EmptyState, type Tone } from "../../../components/primitives";
import type { CandleSourceState } from "../../../components/DataSource";
import { fieldClass } from "../../../components/primitives";
import type { AiStrategyDraft, Condition } from "../../../types";
import { friendlyError, useAiTask } from "../../aiTask";
import AiProgress from "../../components/AiProgress";
import CostChip from "../../components/CostChip";
import { useCostEstimate } from "../../cost";
import { useCopilotT } from "../../i18n";
import { copilotPath } from "../../tabs";
import { Panel } from "../shared";

/**
 * AI drafts: a plain-language idea becomes a rule set (the same schema as the Strategy Builder), shown in words; the
 * trader backtests it, may edit the idea and generate again, and approves only after reading the backtest and
 * accepting the maximum loss. A vague request ("give me a strategy") goes to the Idea Builder interview instead.
 */
const STATUS_TONE: Record<string, Tone> = { APPROVED: "up", BACKTESTED: "info", DRAFT: "neutral", REJECTED: "down", FAILED: "down" };
function operand(o: Condition["left"]): string {
  return o.type === "value" ? String(o.value) : `${o.indicator}${o.period ? `(${o.period})` : ""}`;
}
/** A condition in words: "EMA(20) crosses above EMA(50)". */
export function conditionText(c: Condition, t: (key: string, o?: Record<string, unknown>) => string): string {
  return `${operand(c.left)} ${t(`lab.op.${c.operator}`, { defaultValue: c.operator })} ${operand(c.right)}`;
}

export default function DraftsPanel({ source, symbol }: { source: CandleSourceState; symbol: string }) {
  const t = useCopilotT();
  const navigate = useNavigate();
  const location = useLocation();
  // The Idea Builder's "ask the AI for a custom rule set" hands its prompt over.
  const [prompt, setPrompt] = useState(() => (location.state as { prompt?: string } | null)?.prompt ?? "");
  const [drafts, setDrafts] = useState<AiStrategyDraft[]>([]);
  const [selected, setSelected] = useState<AiStrategyDraft | null>(null);
  const [acceptRisk, setAcceptRisk] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const task = useAiTask(t);
  const estimate = useCostEstimate("strategy_generation");

  const refresh = () => { api.aiDrafts().then(setDrafts).catch((e) => setError(friendlyError(e, t))); };
  useEffect(refresh, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function generate() {
    setMessage(null);
    const d = await task.run([t("lab.drafts.step.read"), t("lab.drafts.step.write"), t("lab.drafts.step.check")], async (signal, advance) => {
      const s = await api.aiInterviewStart(prompt);
      if (s.needs_interview) return "interview" as const;
      advance(1);
      return api.aiGenerate(prompt, { language: "en", symbol: symbol.trim() || null }, signal);
    });
    if (d === "interview") { navigate(copilotPath("idea-builder"), { state: { prompt } }); return; }
    if (d) { setSelected(d); setAcceptRisk(false); refresh(); }
  }

  async function backtest(d: AiStrategyDraft) {
    setMessage(null);
    const tf = d.config?.timeframe ?? "1min";
    const r = await task.run([t("lab.drafts.step.candles"), t("lab.drafts.step.backtest")], async (signal, advance) => {
      const data = await source.fetch([symbol], tf, { count: 600, startPriceFor: () => 100, seedFor: () => 11 });
      const candles = data.candles[symbol.trim().toUpperCase()];
      if (!candles?.length) throw new Error(t("lab.drafts.noCandles", { symbol }));
      advance(1);
      return api.aiBacktestDraft(d.id, symbol, tf, candles, source.mode === "broker" ? `broker:${source.broker}` : "sample", signal);
    });
    if (!r) return;
    setSelected(r.draft); refresh();
    // Figures from sample candles are never shown as performance.
    setMessage(source.mode === "sample" ? t("lab.drafts.sampleResult", { n: r.result.total_trades })
      : t("lab.drafts.result", { n: r.result.total_trades, win: Math.round(r.result.win_rate * (r.result.win_rate <= 1 ? 100 : 1)), pnl: Math.round(r.result.net_pnl) }));
  }

  async function decide(fn: () => Promise<AiStrategyDraft>, done: string) {
    setError(null); setMessage(null);
    try { setSelected(await fn()); setAcceptRisk(false); setMessage(done); refresh(); } catch (e) { setError(friendlyError(e, t)); }
  }

  return (
    <Panel title={t("lab.drafts.title")} icon={<FileText size={15} />} testId="lab-drafts">
      <p className="mb-2 text-xs text-fg-muted">{t("lab.drafts.intro")}</p>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <label htmlFor="draft-prompt" className="block text-xs font-medium text-fg-muted">{t("lab.drafts.label")}</label>
          <textarea id="draft-prompt" className={`${fieldClass} min-h-[96px] py-2`} rows={4} placeholder={t("lab.drafts.placeholder")} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" size="sm" icon={<Sparkles size={13} />} disabled={task.busy || prompt.trim().length < 10} onClick={() => void generate()}>{t("lab.drafts.generate")}</Button>
            {estimate && <CostChip tokens={estimate.tokens} inr={estimate.inr} estimate />}
          </div>
          <AiProgress state={task.state} onCancel={task.cancel} />
          <div className="pt-2">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-fg-muted">{t("lab.drafts.recent")}</div>
            {drafts.length === 0 ? <p className="text-xs text-fg-muted">{t("lab.drafts.none")}</p> : (
              <ul className="divide-y divide-border/60 text-xs">
                {drafts.slice(0, 10).map((d) => (
                  <li key={d.id}>
                    <button onClick={() => api.aiDraft(d.id).then((x) => { setSelected(x); setAcceptRisk(false); }).catch((e) => setError(friendlyError(e, t)))}
                            aria-pressed={selected?.id === d.id}
                            className={`flex w-full items-center gap-2 px-1 py-1.5 text-left hover:bg-surface-2 ${selected?.id === d.id ? "bg-ai/10" : ""}`}>
                      <span className="text-fg-muted">#{d.id}</span>
                      <span className="flex-1 truncate text-fg" title={d.prompt}>{d.config?.name ?? d.prompt.slice(0, 50)}</span>
                      <Badge tone={STATUS_TONE[d.status] ?? "neutral"}>{t(`lab.drafts.status.${d.status}`)}</Badge>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div>
          {!selected ? (
            <EmptyState icon={<FileText size={22} />} title={t("lab.drafts.emptyTitle")} body={t("lab.drafts.emptyBody")} />
          ) : (
            <div className="space-y-3 text-sm" data-testid="draft-review">
              <div className="flex flex-wrap items-center gap-2">
                <b className="text-fg">#{selected.id} {selected.config?.name ?? ""}</b>
                <Badge tone={STATUS_TONE[selected.status] ?? "neutral"}>{t(`lab.drafts.status.${selected.status}`)}</Badge>
                <span className="text-xs text-fg-muted">{selected.lineage.provider} / {selected.lineage.model}</span>
              </div>
              {selected.explanation && <p className="whitespace-pre-wrap text-fg-muted">{selected.explanation}</p>}
              {selected.warnings.length > 0 && <ul className="list-disc pl-4 text-xs text-warn">{selected.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
              {selected.config && (
                <div className="rounded-xl border border-ai/25 bg-ai/5 p-3" data-testid="rules-in-words">
                  <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-ai">{t("lab.candidate.rulesInWords")}</div>
                  {(["long_conditions", "short_conditions"] as const).map((side) => selected.config![side].length > 0 && (
                    <div key={side} className="mb-1">
                      <div className="text-xs font-semibold text-fg">{t(side === "long_conditions" ? "lab.candidate.when.long" : "lab.candidate.when.short")}</div>
                      <ul className="list-disc pl-4 text-fg">{selected.config![side].map((c, i) => <li key={i}>{conditionText(c, t)}</li>)}</ul>
                    </div>
                  ))}
                  <div className="mt-1 text-xs text-fg-muted">{t("lab.drafts.exits", { atr: selected.config.stop_loss_atr_mult, period: selected.config.atr_period, targets: selected.config.target_rr.join("R / ") })}</div>
                </div>
              )}
              {selected.compliance && (
                <div className={`rounded-xl border p-3 text-xs ${selected.compliance.ok ? "border-border" : "border-down/50 bg-down/5"}`}>
                  <div className="font-semibold text-fg">{t("lab.drafts.checklist", { passed: selected.compliance.passed.length, failed: selected.compliance.failed.length })}</div>
                  {selected.compliance.checks.filter((c) => c.status === "FAIL" || c.status === "WARN").map((c) => (
                    <div key={c.rule} className="mt-1 flex gap-2"><span className={`w-8 shrink-0 font-mono ${c.status === "FAIL" ? "text-down" : "text-warn"}`}>{c.rule}</span><span className="text-fg">{c.detail}</span></div>
                  ))}
                  {selected.compliance.evidence && <p className="mt-1 text-fg-muted">{t("lab.drafts.evidence", { strength: selected.compliance.evidence.strength, summary: selected.compliance.evidence.summary })}</p>}
                  <div className="mt-2 rounded-lg border border-warn/40 bg-warn/5 p-2 text-fg">
                    <div className="font-semibold">{t("lab.drafts.mustAccept")}</div>
                    <div>{selected.compliance.user_must_accept.max_loss_per_trade_text}</div>
                    <div className="mt-1">{selected.compliance.user_must_accept.worst_case_text}</div>
                    {selected.status === "BACKTESTED" && (
                      <label className="mt-1 flex items-center gap-2"><input type="checkbox" className="h-4 w-4 accent-[rgb(var(--ai))]" checked={acceptRisk} onChange={(e) => setAcceptRisk(e.target.checked)} />{t("lab.drafts.acceptBox")}</label>
                    )}
                  </div>
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2">
                {(selected.status === "DRAFT" || selected.status === "BACKTESTED") && (
                  <Button size="sm" icon={<FlaskConical size={13} />} disabled={task.busy} onClick={() => void backtest(selected)}>
                    {selected.status === "BACKTESTED" ? t("lab.drafts.retest") : t("lab.drafts.backtest")} ({source.mode === "broker" ? t("lab.drafts.onBroker") : t("lab.drafts.onSample")})
                  </Button>
                )}
                <Button size="sm" variant="ghost" icon={<Pencil size={13} />} onClick={() => { setPrompt(selected.prompt); document.getElementById("draft-prompt")?.focus(); }}>{t("lab.drafts.edit")}</Button>
                {selected.status === "BACKTESTED" && (
                  <Button size="sm" variant="primary" icon={<CheckCircle2 size={13} />} disabled={!acceptRisk || (selected.compliance ? !selected.compliance.ok : false)}
                          title={!acceptRisk ? t("lab.drafts.tickFirst") : undefined}
                          onClick={() => void decide(async () => (await api.aiApproveDraft(selected.id, undefined, true)).draft, t("lab.drafts.approved"))}>{t("lab.drafts.approve")}</Button>
                )}
                {selected.status !== "APPROVED" && selected.status !== "REJECTED" && (
                  <Button size="sm" variant="danger" icon={<XCircle size={13} />} onClick={() => void decide(() => api.aiRejectDraft(selected.id), t("lab.drafts.rejected"))}>{t("lab.drafts.reject")}</Button>
                )}
              </div>
              {selected.strategy_id && <p className="text-xs text-up">{t("lab.drafts.savedAs", { id: selected.strategy_id })}</p>}
              <p className="text-[11px] text-fg-muted">{selected.disclaimer}</p>
            </div>
          )}
          {message && <p className="mt-2 rounded-lg border border-ai/30 bg-ai/5 px-3 py-2 text-xs text-fg" role="status">{message}</p>}
          {error && <p className="mt-2 text-xs text-down" role="alert">{error}</p>}
        </div>
      </div>
    </Panel>
  );
}
