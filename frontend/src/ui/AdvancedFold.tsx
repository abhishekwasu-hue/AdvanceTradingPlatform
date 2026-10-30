import { ChevronDown } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import { cx } from "../components/primitives/cx";

/**
 * SB-UX SU7 (owner order 30 Oct): the ONE closed "Advanced" fold of the strategy pages. The tools a beginner does not
 * need on day one (higher timeframe per operand, ATR length, premium model / IV, the chain CSV, sample bars, the
 * optimiser and the robustness checks) live here - moved, never deleted. Closed on every visit; a native <details>,
 * so the keyboard and screen readers work as is; the summary is a 44 px target on a phone.
 * `on` false (a page shared with the flag-off app) renders the children in place, exactly as before.
 */
export function AdvancedFold({ id, title = "Advanced", on = true, openWhen = false, children, className }: {
  id: string;
  /** Opens the fold by itself while this holds (a value the step needs is inside); the trader can close it again. */
  openWhen?: boolean;
  title?: string;
  on?: boolean;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDetailsElement>(null);
  // Opens only (never closes on its own), so typing the missing value inside keeps the fold and the focus where they are.
  useEffect(() => { if (openWhen && ref.current) ref.current.open = true; }, [openWhen]);
  if (!on) return <>{children}</>;
  return (
    <details ref={ref} data-advanced={id} className={cx("group rounded-control border border-border", className)}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-space-2 px-space-3 text-t14 font-medium text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9 [&::-webkit-details-marker]:hidden">
        {title}
        <ChevronDown size={16} aria-hidden className="shrink-0 text-fg-muted transition-transform group-open:rotate-180" />
      </summary>
      <div className="space-y-space-3 border-t border-border p-space-3">{children}</div>
    </details>
  );
}
