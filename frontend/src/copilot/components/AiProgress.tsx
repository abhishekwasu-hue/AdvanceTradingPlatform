import { AlertCircle, Check, Loader2, X } from "lucide-react";
import { Button } from "../../components/primitives";
import type { AiTaskState } from "../aiTask";
import { useCopilotT } from "../i18n";

/** The progress of an AI call: its named steps (done / now / next), a Cancel button, and the friendly error or
 * "cancelled" afterwards. Nothing is shown while idle or after success - the result itself is the success state. */
export default function AiProgress({ state, onCancel, onRetry }: { state: AiTaskState; onCancel: () => void; onRetry?: () => void }) {
  const t = useCopilotT();
  if (state.phase === "running") {
    return (
      <div className="rounded-xl border border-ai/30 bg-ai/5 p-3" role="status" aria-live="polite">
        <ol className="space-y-1 text-sm">
          {state.steps.map((s, i) => (
            <li key={s} className={`flex items-center gap-2 ${i < state.step ? "text-fg-muted" : i === state.step ? "text-fg" : "text-fg-muted/60"}`}>
              {i < state.step ? <Check size={14} className="text-ai" /> : i === state.step ? <Loader2 size={14} className="animate-spin text-ai" /> : <span className="inline-block h-3.5 w-3.5 rounded-full border border-border" />}
              {s}
            </li>
          ))}
        </ol>
        <div className="mt-2 flex items-center gap-2">
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-surface-3">
            <div className="copilot-shimmer h-1 rounded-full bg-ai/70 transition-all" style={{ width: `${((state.step + 1) / Math.max(1, state.steps.length)) * 100}%` }} />
          </div>
          <Button size="sm" variant="ghost" icon={<X size={13} />} onClick={onCancel}>{t("states.cancel")}</Button>
        </div>
      </div>
    );
  }
  if (state.phase === "error" && state.error) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-down/40 bg-down/5 p-3 text-sm text-fg" role="alert">
        <AlertCircle size={15} className="shrink-0 text-down" /><span className="flex-1">{state.error}</span>
        {onRetry && <Button size="sm" variant="secondary" onClick={onRetry}>{t("states.retry")}</Button>}
      </div>
    );
  }
  if (state.phase === "cancelled") return <div className="text-xs text-fg-muted" role="status">{t("states.cancelled")}</div>;
  return null;
}
