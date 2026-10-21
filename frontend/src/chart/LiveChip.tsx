import { Clock } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { usePageVisible } from "../utils/usePageVisible";
import { liveChipState, liveChipText, type LiveQuote } from "./liveChip";

/**
 * UI2-4e: "Live" / "Price delayed · 4 min" on a v2 chart (rules in liveChip.ts). Neutral: the chart's own text, axis
 * and background tokens (it sits inside the chart's `data-chart-theme` root), words and an icon, never colour alone.
 * Its own one-second clock keeps the age current without drawing the chart again; it stops while the tab is hidden.
 */
export function LiveChip({ live, marketOpen }: { live: LiveQuote | null | undefined; marketOpen: boolean | null | undefined }) {
  const [receivedAt, setReceivedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const visible = usePageVisible();
  const hiddenAt = useRef<number | null>(null);
  useEffect(() => {
    const at = Date.now();
    setReceivedAt(live ? at : null);
    setNow(at);
  }, [live]);
  // The local clock stops while the tab is hidden (nothing is polled then): the hidden time is left out, so a tab
  // shown again does not read "Price delayed" before its next poll.
  useEffect(() => {
    const at = Date.now();
    if (!visible) { hiddenAt.current = at; return undefined; }
    if (hiddenAt.current !== null) {
      const gap = at - hiddenAt.current;
      hiddenAt.current = null;
      setReceivedAt((r) => (r === null ? r : r + gap));
    }
    setNow(at);
    if (!live) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, [live, visible]);
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
