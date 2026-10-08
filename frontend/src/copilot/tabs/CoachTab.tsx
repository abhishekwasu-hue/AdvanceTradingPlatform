import { Layers, Target, Trophy } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { Badge, type Tone } from "../../components/primitives";
import TradeCoach from "../../components/TradeCoach";
import type { ThesisHistory, ThesisWeeklyReport } from "../../types";
import { friendlyError } from "../aiTask";
import TiltCard from "../components/TiltCard";
import { useCopilotT } from "../i18n";
import { Panel, Stagger, StaggerItem } from "./shared";

/**
 * Coach & Scorecard: the trader's own behaviour (the trade coach: flags, per-trade review, breakdowns) and the
 * Copilot's own record - the weekly thesis scoreboard (hit rate of its market readings against the next session)
 * and what the shadow overlay would have done (recorded, never applied). The Copilot is graded too.
 */
const OUTCOME_TONE: Record<string, Tone> = { hit: "up", miss: "down", flat: "neutral" };
/** The server scores a thesis +1 (the next session moved its way), -1 (the other way) or 0 (neither). */
export function resultOf(score: number): "hit" | "miss" | "flat" {
  return score > 0 ? "hit" : score < 0 ? "miss" : "flat";
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <TiltCard className="h-full">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">{label}</div>
      <div className="mt-1 font-tabular text-2xl font-bold text-fg">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-fg-muted">{hint}</div>}
    </TiltCard>
  );
}

export default function CoachTab() {
  const t = useCopilotT();
  const [report, setReport] = useState<ThesisWeeklyReport | null>(null);
  const [history, setHistory] = useState<ThesisHistory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.aiThesisReport().then(setReport).catch((e) => setError(friendlyError(e, t)));
    api.aiThesisHistory(undefined, 30).then(setHistory).catch(() => setHistory(null));
  }, [t]);

  const sb = history?.scoreboard;
  const hitRate = report?.hit_rate ?? sb?.hit_rate ?? null;
  const items = history?.items ?? [];
  const reduced = items.filter((i) => i.shadow_multiplier < 1);

  return (
    <div className="space-y-4" data-testid="tab-panel-coach">
      <Panel title={t("coach.scoreTitle")} icon={<Trophy size={15} />}>
        <p className="mb-3 text-xs text-fg-muted">{t("coach.scoreIntro")}</p>
        {error && <p className="mb-2 text-sm text-down" role="alert">{error}</p>}
        <Stagger className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StaggerItem><Stat label={t("coach.hitRate")} value={hitRate != null ? `${Math.round(hitRate * 100)}%` : "-"} hint={t("coach.hitRateHint")} /></StaggerItem>
          <StaggerItem><Stat label={t("coach.scored")} value={String(report?.scored ?? sb?.scored ?? 0)} hint={t("coach.scoredHint", { hits: report?.hits ?? sb?.hits ?? 0, misses: report?.misses ?? sb?.misses ?? 0, flat: report?.flat ?? sb?.flat ?? 0 })} /></StaggerItem>
          <StaggerItem><Stat label={t("coach.avgShadow")} value={report?.avg_shadow != null ? `×${report.avg_shadow.toFixed(2)}` : "-"} hint={t("coach.avgShadowHint")} /></StaggerItem>
          <StaggerItem><Stat label={t("coach.pending")} value={String(report?.pending ?? 0)} hint={t("coach.pendingHint")} /></StaggerItem>
        </Stagger>
        {report?.lines && report.lines.length > 0 && <ul className="mt-3 list-disc space-y-0.5 pl-5 text-xs text-fg-muted">{report.lines.map((l) => <li key={l}>{l}</li>)}</ul>}
      </Panel>

      <Panel title={t("coach.shadowTitle")} icon={<Layers size={15} />} testId="coach-shadow">
        <p className="mb-2 text-xs text-fg-muted">{t("coach.shadowIntro", { reduced: reduced.length, total: items.length })}</p>
        {items.length === 0 ? <p className="text-sm text-fg-muted">{t("coach.shadowEmpty")}</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="text-left text-fg-muted">
                <th className="py-1 font-medium">{t("coach.col.day")}</th><th className="font-medium">{t("coach.col.symbol")}</th><th className="font-medium">{t("coach.col.read")}</th>
                <th className="text-right font-medium">{t("coach.col.shadow")}</th><th className="pl-6 font-medium">{t("coach.col.outcome")}</th>
              </tr></thead>
              <tbody>
                {items.slice(0, 15).map((i) => (
                  <tr key={i.id} className="border-t border-border/60">
                    <td className="py-1.5 text-fg-muted">{i.day}</td>
                    <td className="text-fg">{i.symbol}</td>
                    <td className={i.direction === "BULLISH" ? "text-up" : i.direction === "BEARISH" ? "text-down" : "text-fg"}>{t(`thesis.dir.${i.direction}`, { defaultValue: i.direction })}</td>
                    <td className="text-right font-tabular text-fg">×{i.shadow_multiplier.toFixed(2)}</td>
                    <td className="pl-6">
                      {i.score == null ? <span className="text-fg-muted">{t("coach.outcome.pending")}</span> : (
                        <><Badge tone={OUTCOME_TONE[resultOf(i.score)]}>{t(`coach.outcome.${resultOf(i.score)}`)}</Badge>
                          {i.outcome && <span className="ml-1 text-fg-muted">{t("coach.nextSession", { move: t(`coach.move.${i.outcome}`, { defaultValue: i.outcome }) })}</span>}</>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-2 text-[11px] text-warn"><Target size={11} className="mr-1 inline" />{t("coach.shadowNote")}</p>
      </Panel>

      <TradeCoach />
    </div>
  );
}
