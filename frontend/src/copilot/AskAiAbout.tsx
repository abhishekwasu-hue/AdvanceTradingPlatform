import { Sparkles } from "lucide-react";
import { InPlaceLink } from "../inplace/InPlaceLink";
import { useFeatures } from "../ui/useFeatures";
import { askAiPath, type AskContext } from "./askContext";

/**
 * M72 PR-3 (CP-3): the "Ask AI" action on a row (a position, a trade, a signal, a screener result, a journal entry). It
 * opens the Copilot beside the page (in place, the page stays) with that row as context. Shown while the Copilot is on.
 */
export function AskAiAbout({ ctx, label = "Ask AI" }: { ctx: AskContext; label?: string }) {
  const on = useFeatures().on("ai_copilot");
  if (!on) return null;
  return (
    <InPlaceLink to={askAiPath(ctx)} data-ask-ai={ctx.object ? ctx.object.kind : "symbol"} aria-label={`${label} about ${ctx.symbol ?? "this"}`}
      onClick={(e) => e.stopPropagation()}
      className="inline-flex min-h-11 items-center gap-space-1 rounded-pill border border-border px-space-3 text-t13 font-medium text-fg transition-colors duration-fast hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
      <Sparkles size={14} aria-hidden className="text-brand" />{label}
    </InPlaceLink>
  );
}

export default AskAiAbout;
