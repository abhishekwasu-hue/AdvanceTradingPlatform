import { CheckCircle2, Code2, Crosshair, Rocket, Save, Target } from "lucide-react";
import { useState } from "react";
import { api } from "../../../api/client";
import { Badge, Button, type Tone } from "../../../components/primitives";
import SampleStamp from "../../../components/SampleStamp";
import type { StrategyCandidate } from "../../../types";
import { friendlyError } from "../../aiTask";
import TiltCard from "../../components/TiltCard";
import { useCopilotT } from "../../i18n";
import { money, num } from "../shared";
import EquityCurve from "./EquityCurve";

/**
 * One tested rule template: its rules ALWAYS in words (white-box; the DSL is an extra view, never a replacement),
 * exits and risk, the backtest on tuning vs unseen sessions with its equity curve, and the two human steps - accept
 * the maximum loss and save it, then start it in PAPER. Nothing here is ranked or recommended.
 */
const VERDICT_TONE: Record<StrategyCandidate["verdict"], Tone> = {
  robust: "up", overfit: "warn", weak: "down", insufficient: "neutral", sample: "warn", thin: "neutral", untested: "neutral",
};

function MetricsRow({ label, m }: { label: string; m: StrategyCandidate["in_sample"] }) {
  const tone = m.expectancy_r > 0 ? "text-up" : m.expectancy_r < 0 ? "text-down" : "text-fg";
  return (
    <tr className="border-t border-border/50">
      <td className="py-1 text-fg-muted">{label}</td>
      <td className="text-right font-tabular text-fg">{m.trades}</td>
      <td className="text-right font-tabular text-fg">{m.trades ? `${m.win_rate}%` : "-"}</td>
      <td className={`text-right font-tabular ${tone}`}>{m.trades ? `${m.expectancy_r > 0 ? "+" : ""}${m.expectancy_r}R` : "-"}</td>
      <td className="text-right font-tabular text-fg">{m.profit_factor ?? "-"}</td>
    </tr>
  );
}

export default function CandidateCard({ c, symbol, sample }: { c: StrategyCandidate; symbol: string; sample: boolean }) {
  const t = useCopilotT();
  const [showDsl, setShowDsl] = useState(false);
  const [saved, setSaved] = useState<{ strategy_id: string; name: string; deployment: Parameters<typeof api.createDeployment>[0] } | null>(null);
  const [deployed, setDeployed] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [acceptRisk, setAcceptRisk] = useState(false);
  const oos = new Set(c.oos_sessions);
  const splitAt = c.trades.findIndex((tr) => oos.has(tr.date));

  async function adopt() {
    if (c.candidate_id == null) { setError(t("lab.candidate.notOnServer")); return; }
    setBusy(true); setError(null);
    try { setSaved(await api.aiStrategistAdopt(c.candidate_id, `${c.name} · ${symbol}`, acceptRisk)); } catch (e) { setError(friendlyError(e, t)); } finally { setBusy(false); }
  }
  async function deploy() {
    if (!saved) return;
    setBusy(true); setError(null);
    try { setDeployed((await api.createDeployment(saved.deployment)).id); } catch (e) { setError(friendlyError(e, t)); } finally { setBusy(false); }
  }

  const sides = (["long", "short"] as const).filter((s) => c.rules[s].length > 0);
  return (
    <TiltCard as="article" tilt={false} className="space-y-3" data-testid="strategy-card">
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold text-fg">{c.name}</h3>
        <Badge tone="neutral">{c.direction_text ?? t(`lab.direction.${c.direction}`)}</Badge>
        <span className="text-xs text-fg-muted">{c.timeframe_text ?? c.timeframe}{c.source === "ai" ? ` · ${t("lab.candidate.fromAi")}` : ""}</span>
        <Badge className="ml-auto" tone={VERDICT_TONE[c.verdict] ?? "neutral"}>{t(`lab.verdict.${c.verdict}`)}</Badge>
      </header>
      <p className="text-sm text-fg-muted">{c.why}</p>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-2 text-sm">
          {/* Rules in words: always visible on every strategy card. */}
          <div className="rounded-xl border border-ai/25 bg-ai/5 p-3" data-testid="rules-in-words">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-ai">{t("lab.candidate.rulesInWords")}</div>
            {sides.map((side) => (
              <div key={side} className="mb-1.5 last:mb-0">
                <div className="text-xs font-semibold text-fg">{t(`lab.candidate.when.${side}`)}</div>
                <ul className="list-disc pl-4 text-fg">
                  {(c.rules_text?.[side] ?? c.rules[side]).map((r, i) => <li key={`${side}-${i}`}>{r}</li>)}
                </ul>
              </div>
            ))}
          </div>
          <button onClick={() => setShowDsl((v) => !v)} aria-expanded={showDsl} className="inline-flex items-center gap-1 text-xs text-brand hover:underline">
            <Code2 size={12} />{showDsl ? t("lab.candidate.hideDsl") : t("lab.candidate.showDsl")}
          </button>
          {showDsl && (
            <div className="flex flex-wrap gap-1" data-testid="rules-dsl">
              {sides.flatMap((side) => c.rules[side].map((r) => <code key={`${side}-${r}`} className="rounded border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-fg">{side === "long" ? "L" : "S"}: {r}</code>))}
            </div>
          )}
          <div className="text-fg-muted"><Target size={13} className="mr-1 inline text-ai" />{c.exits}</div>
          {c.triggers.length > 0 && (
            <div className="text-fg-muted"><Crosshair size={13} className="mr-1 inline text-ai-2" />{t("lab.candidate.triggers")}: {c.triggers.map((tr) => `${tr.name} ${num(tr.price)}`).join(" · ")}</div>
          )}
          <div className="text-fg-muted">
            {t("lab.candidate.risk")}: <b className="text-fg">{money(c.risk_amount)}</b>
            {c.stop_points ? <> · {t("lab.candidate.stop", { pts: num(c.stop_points) })}{c.quantity_hint ? <> · {t("lab.candidate.qty", { qty: c.quantity_hint })}</> : null}</> : null}
          </div>
        </div>

        <div className="relative">
          <div className={sample ? "select-none blur-sm" : ""} aria-hidden={sample}>
            <TiltCard depth glow="none" className="!p-3">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-fg-muted">{t("lab.candidate.backtest")}</div>
              <table className="w-full text-xs">
                <thead><tr className="text-fg-muted">
                  <th className="text-left font-normal">{t("lab.candidate.check")}</th><th className="text-right font-normal">{t("lab.candidate.trades")}</th>
                  <th className="text-right font-normal">{t("lab.candidate.win")}</th><th className="text-right font-normal">{t("lab.candidate.expectancy")}</th>
                  <th className="text-right font-normal">{t("lab.candidate.pf")}</th>
                </tr></thead>
                <tbody>
                  <MetricsRow label={t("lab.candidate.inSample")} m={c.in_sample} />
                  <MetricsRow label={t("lab.candidate.outSample")} m={c.out_of_sample} />
                </tbody>
              </table>
              {c.trades.length > 1 && <EquityCurve trades={c.trades} splitAt={splitAt >= 0 ? splitAt : null} label={t("lab.candidate.curveAria", { n: c.trades.length })} />}
              <p className="mt-1 text-[11px] text-fg-muted">{c.verdict_text}</p>
            </TiltCard>
          </div>
          {sample && <SampleStamp />}
        </div>
      </div>

      <footer className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-3">
        {!saved ? (
          <>
            <label className="flex items-center gap-1.5 text-xs text-fg">
              <input type="checkbox" className="h-4 w-4 accent-[rgb(var(--ai))]" checked={acceptRisk} onChange={(e) => setAcceptRisk(e.target.checked)} />
              {t("lab.candidate.accept", { amount: money(c.risk_amount) })}
            </label>
            <Button size="sm" variant="primary" icon={<Save size={13} />} loading={busy} disabled={!acceptRisk} onClick={() => void adopt()}>{t("lab.candidate.save")}</Button>
          </>
        ) : deployed == null ? (
          <>
            <span className="text-xs text-up"><CheckCircle2 size={13} className="mr-1 inline" />{t("lab.candidate.saved", { id: saved.strategy_id })}</span>
            <Button size="sm" variant="primary" icon={<Rocket size={13} />} loading={busy} onClick={() => void deploy()}>{t("lab.candidate.deployPaper")}</Button>
          </>
        ) : <span className="text-xs text-up"><CheckCircle2 size={13} className="mr-1 inline" />{t("lab.candidate.deployed", { id: deployed })}</span>}
        {error && <span className="text-xs text-down" role="alert">{error}</span>}
      </footer>
    </TiltCard>
  );
}
