import { ArrowLeft, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { friendlyError } from "../api/errors";
import { Button, Skeleton } from "../components/primitives";
import { chartAnalyzeApi, errorCode, isFeatureOff } from "./api";
import ResultViewV2 from "./ResultViewV2";
import type { SharedAnalysis } from "./types";

/**
 * Analyze a chart (PR-A4): what a share link opens - `/chart-analyze?shared=<link>` - for a logged-in member of the
 * owner's organisation. The structure-only copy (no name, no price, no date, no picture; backend share.py), shown with
 * the result screen in its `shared` form: no buttons, the drawing over a plain background, the disclaimer at the end.
 */

const card = "rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5";

export const LINK_PROBLEM: Record<string, string> = {
  link_gone: "This link does not open. It was stopped, replaced by a newer link, or the analysis was deleted.",
  link_expired: "This link has expired. Ask the person who shared it for a new one.",
  off: "Shared analyses are not available for your organisation.",
};

type State = { status: "loading" } | { status: "ready"; data: SharedAnalysis } | { status: "error"; message: string };

export default function SharedAnalysisV2({ token, onBack }: { token: string; onBack: () => void }) {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    chartAnalyzeApi.shared(token).then(
      (data) => { if (live) setState({ status: "ready", data }); },
      (e: unknown) => {
        if (!live) return;
        const code = isFeatureOff(e) ? "off" : errorCode(e);
        setState({ status: "error", message: LINK_PROBLEM[code] ?? friendlyError(e) });
      },
    );
    return () => { live = false; };
  }, [token]);

  return (
    <div className="mx-auto max-w-[1240px] space-y-space-4" data-testid="shared-analysis-page">
      <div className="flex flex-col gap-space-2 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-t20 font-semibold text-fg md:text-t24">A shared chart reading</h1>
        <Button className="h-11 md:h-9" variant="ghost" icon={<ArrowLeft size={16} aria-hidden />} onClick={onBack}>Your analyses</Button>
      </div>
      {state.status === "loading" && <Skeleton className="h-40" panel label="Loading" />}
      {state.status === "error" && (
        <div role="alert" className={`${card} flex items-start gap-space-3 text-t14 text-fg`} data-testid="shared-problem">
          <TriangleAlert size={18} aria-hidden className="mt-0.5 shrink-0 text-warn" />
          <p>{state.message}</p>
        </div>
      )}
      {state.status === "ready" && (
        <ResultViewV2 shared result={{ ...state.data.result, shared: true }} itemDisclaimer={state.data.disclaimer} imageSrc={null} />
      )}
    </div>
  );
}
