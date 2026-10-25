import type { ReactNode } from "react";

/**
 * UI-H Wave 0 (H1, H4): one summary card - a short label, ONE big number (an IndianNumber, or a word such as
 * "Neutral") and ONE plain line that says what it means. Optional (?) help (a HelpPopover or TermHint) beside the
 * label; never an inline method paragraph. Labels are sentence case (no uppercase).
 */
export interface SummaryCardProps {
  label: string;
  value: ReactNode;
  line?: ReactNode;
  help?: ReactNode;
  className?: string;
}

export function SummaryCard({ label, value, line, help, className = "" }: SummaryCardProps) {
  return (
    <section aria-label={label} data-summary-card
      className={`min-w-0 rounded-panel border border-subtle bg-surface-1 p-space-3 md:p-space-4 ${className}`}>
      <p className="flex items-center gap-space-1 break-words text-t14 text-fg-muted">{label}{help}</p>
      <p className="mt-space-1 break-words text-t24 font-semibold leading-tight text-fg" data-summary-value>{value}</p>
      {line && <p className="mt-space-1 break-words text-t14 text-fg-muted">{line}</p>}
    </section>
  );
}
