import type { ReactNode } from "react";
import { useColourV2 } from "./colourV2";
import { checkTone, toneSurfaceClass, type Toned } from "./tone";

/**
 * UI-H Wave 0 (H1, H4): one summary card - a short label, ONE big number (an IndianNumber, or a word such as
 * "Neutral") and ONE plain line that says what it means. Optional (?) help (a HelpPopover or TermHint) beside the
 * label; never an inline method paragraph. Labels are sentence case (no uppercase).
 *
 * UI-H CA4: an optional `tone` (profit / loss / risk / info) gives the card a soft tinted background while colour_v2 is
 * on - always with its `cue` (a sign in the value, an icon or a word), which shows in both colour modes.
 */
export type SummaryCardProps = {
  label: string;
  value: ReactNode;
  line?: ReactNode;
  help?: ReactNode;
  className?: string;
} & Toned;

export function SummaryCard({ label, value, line, help, className = "", tone, cue }: SummaryCardProps) {
  const toned = checkTone(tone, cue, value);
  const colour = useColourV2();
  const surface = toned && tone && colour ? toneSurfaceClass(tone) : "bg-surface-1";
  const Icon = cue && "icon" in cue ? cue.icon : null;
  return (
    <section aria-label={label} data-summary-card data-tone={tone}
      className={`min-w-0 rounded-panel border border-subtle ${surface} p-space-3 md:p-space-4 ${className}`}>
      <p className="flex items-center gap-space-1 break-words text-t14 text-fg-muted">
        {label}{help}
        {cue && "word" in cue && <span className="ml-auto text-t13 font-semibold text-fg">{cue.word}</span>}
      </p>
      <p className="mt-space-1 flex items-center gap-space-2 break-words text-t24 font-semibold leading-tight text-fg" data-summary-value>
        {Icon && <Icon size={20} aria-hidden className="shrink-0" />}
        {value}
      </p>
      {line && <p className="mt-space-1 break-words text-t14 text-fg-muted">{line}</p>}
    </section>
  );
}
