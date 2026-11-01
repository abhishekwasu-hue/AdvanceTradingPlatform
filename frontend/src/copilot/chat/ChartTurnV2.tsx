import { TriangleAlert } from "lucide-react";
import { useState } from "react";
import { InPlaceLink } from "../../inplace/InPlaceLink";
import AlertDialogV2, { type AlertTarget } from "../../chartAnalyze/AlertDialogV2";
import { ENGINE_ADMIN_TITLE, ENGINE_USER_LINE, ENGINE_USER_TITLE, engineAudience } from "../../chartAnalyze/engineWords";
import HybridResultV2 from "../../chartAnalyze/HybridResultV2";
import ResultViewV2 from "../../chartAnalyze/ResultViewV2";
import { useCopilotT } from "../i18n";
import type { ChartTurnState } from "./model";

/**
 * M59 CR-2: one attached chart in the Copilot thread - working, the result (the annotated chart and the reading,
 * ResultViewV2 without its page-only buttons; the area alerts stay), a failure in plain words, or the engine-missing
 * state (CR-3: the user's plain line, the admin's owner action). Its own chunk: the chart drawing loads with it.
 */
/** The engine is not installed (CR-3): the user's plain line, or the admin's owner action with the steps. */
function EngineMissing({ engine }: { engine: Extract<ChartTurnState, { status: "unavailable" }>["engine"] }) {
  const admin = engineAudience(engine) === "admin";
  return (
    <div className="flex items-start gap-space-3" role="status" data-chart-turn="unavailable" data-audience={admin ? "admin" : "user"}>
      <TriangleAlert size={20} aria-hidden className="mt-0.5 shrink-0 text-warn" />
      <div className="min-w-0">
        <p className="text-t15 font-semibold text-fg">{admin ? ENGINE_ADMIN_TITLE : ENGINE_USER_TITLE}</p>
        <p className="mt-space-1 text-t14 text-fg-muted">{ENGINE_USER_LINE}</p>
        {admin && engine.reason && <p className="mt-space-1 text-t14 text-fg-muted">Why: {engine.reason}.</p>}
        {admin && engine.owner_steps && <p className="mt-space-1 break-words text-t14 text-fg-muted">{engine.owner_steps}</p>}
      </div>
    </div>
  );
}

/** One attached chart in the thread: working, the result (annotated chart + reading), a failure, or no engine. */
export function ChartTurnV2({ chart }: { chart: ChartTurnState }) {
  const t = useCopilotT();
  const [alertTarget, setAlertTarget] = useState<AlertTarget | null>(null);
  if (chart.status === "working") {
    return <p aria-busy="true" className="text-t14 text-fg-muted" data-chart-turn="working">{chart.step === "adding" ? t("chat.chartAdding") : t("chat.chartAnalysing")}</p>;
  }
  if (chart.status === "unavailable") return <EngineMissing engine={chart.engine} />;
  if (chart.status === "failed") {
    return (
      <div role="alert" data-chart-turn="failed">
        <p className="text-t14 font-semibold text-fg">{t("chat.chartFailed")}</p>
        <p className="mt-space-1 text-t14 text-fg">{chart.message}</p>
      </div>
    );
  }
  const { item, imageSrc } = chart;
  return (
    <div className="space-y-space-3" data-chart-turn="done" data-item={item.id}>
      {item.hybrid && <HybridResultV2 result={item.hybrid} userImageSrc={imageSrc} readingSrc={chart.readingSrc ?? null} actions={false} />}
      {item.result && (
        <ResultViewV2 result={item.result} itemLabel={item.label} itemDisclaimer={item.disclaimer} imageSrc={imageSrc} actions={false} onAlert={setAlertTarget} />
      )}
      <InPlaceLink to="/chart-analyze" className="inline-flex min-h-11 items-center text-t14 font-semibold text-brand hover:underline md:min-h-0" data-chart-open-page>
        {t("chat.chartOpenPage")}
      </InPlaceLink>
      <AlertDialogV2 target={alertTarget} onClose={() => setAlertTarget(null)} onCreated={() => setAlertTarget(null)} />
    </div>
  );
}

export default ChartTurnV2;
