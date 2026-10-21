import { Clock } from "lucide-react";
import { useEffect, useState } from "react";
import { liveChipState, liveChipText, type LiveQuote } from "./liveChip";

/**
 * UI2-4e: "Live" / "Price delayed · 4 min" on a v2 chart (rules in liveChip.ts). Neutral: the chart's own text, axis
 * and background tokens (it sits inside the chart's `data-chart-theme` root), words and an icon, never colour alone.
 * Its own one-second clock keeps the age current without drawing the chart again.
 */
export function LiveChip({ live, marketOpen }: { live: LiveQuote | null | undefined; marketOpen: boolean | null | undefined }) {
  const [receivedAt, setReceivedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const at = Date.now();
    setReceivedAt(live ? at : null);
    setNow(at);
    if (!live) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, [live]);
  const state = liveChipState(live, receivedAt, marketOpen, now);
  if (!state) return null;
  const { text, title } = liveChipText(state);
  return (
    <span data-live-chip={state.kind} title={title}
      className="inline-flex items-center gap-space-1 whitespace-nowrap rounded-pill border px-space-2 py-0.5 text-t12"
      style={{ color: "rgb(var(--chart-text))", backgroundColor: "rgb(var(--chart-bg))", borderColor: "rgb(var(--chart-axis))" }}>
      {state.kind === "live"
        ? <span aria-hidden className="inline-block h-1.5 w-1.5 rounded-pill" style={{ backgroundColor: "rgb(var(--chart-text))" }} />
        : <Clock size={12} aria-hidden />}
      {text}
    </span>
  );
}
