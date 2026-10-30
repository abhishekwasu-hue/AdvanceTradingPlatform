import { ChevronRight } from "lucide-react";
import { useId, type ReactNode } from "react";
import { Link } from "react-router";
import { chipClass } from "../ui/Chip";
import { Skeleton } from "../components/primitives";

/**
 * UI2-2 (UI v2 proposal §8 "Cards and numbers"): the one v2 card - surface-1, a 1 px subtle outline, the panel radius,
 * the near-flat card shadow, 16 px padding on a phone and 20 px on a desktop - and the pieces the dashboard's blocks
 * share: the sentence-case card title with its count, the "View all" link, the friendly empty state with one action,
 * and the plain-language "could not load" line.
 */

export const CARD = "rounded-panel border border-subtle bg-surface-1 shadow-card";
export const LINK_PRIMARY = "inline-flex h-11 shrink-0 items-center justify-center gap-space-2 rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand transition-colors duration-fast hover:bg-brand-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface md:h-9";
export const LINK_SECONDARY = "inline-flex h-11 shrink-0 items-center justify-center gap-space-2 rounded-control border border-border bg-surface-1 px-space-4 text-t14 font-semibold text-fg transition-colors duration-fast hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface md:h-9";

export function CardV2({ title, count, more, children, className = "", testId, tour, badge }: {
  title: ReactNode;
  /** CT-1 C8: the screen's one mode badge (PAPER), beside the title and outside the heading's name. */
  badge?: ReactNode;
  count?: number;
  more?: { label: string; to: string };
  children: ReactNode;
  className?: string;
  testId?: string;
  /** MO2 part C: the `data-tour` id a page tour points at. */
  tour?: string;
}) {
  const titleId = useId();
  return (
    <section aria-labelledby={titleId} data-block={testId} data-tour={tour} className={`${CARD} p-space-4 md:p-space-5 ${className}`}>
      <div className="flex min-h-11 flex-wrap items-center justify-between gap-x-space-3 md:min-h-0">
        <h2 id={titleId} className="flex items-center gap-space-2 text-t15 font-semibold text-fg">
          {title}
          {count !== undefined && count > 0 && (
            <span className="inline-flex h-6 min-w-6 items-center justify-center rounded-pill bg-surface-2 px-space-2 text-t12 font-semibold text-fg-muted">{count}</span>
          )}
        </h2>
        {badge && <span className="mr-auto flex items-center">{badge}</span>}
        {more && (
          <Link to={more.to} className="inline-flex h-11 items-center gap-space-1 rounded-control text-t14 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-auto">
            {more.label} <ChevronRight size={16} aria-hidden />
          </Link>
        )}
      </div>
      <div className="mt-space-3">{children}</div>
    </section>
  );
}

/** A friendly empty state with at most one action (§5 "Empty states"; UX-11 U4: one line + one action - a link or a
 * button). Shared as `EmptyState` in src/ui/numbersFirst.ts. */
export type EmptyAction = { label: string; to: string } | { label: string; onClick: () => void };
export function EmptyStateV2({ text, action }: { text: string; action?: EmptyAction }) {
  return (
    <div className="flex flex-col items-start gap-space-3 py-space-2 sm:flex-row sm:items-center sm:justify-between" data-empty>
      <p className="text-t14 text-fg-muted">{text}</p>
      {action && ("to" in action
        ? <Link to={action.to} className={LINK_SECONDARY}>{action.label}</Link>
        : <button type="button" onClick={action.onClick} className={LINK_SECONDARY}>{action.label}</button>)}
    </div>
  );
}

/** A block whose data could not be read: what happened and what happens next, never the server's own words. */
export function LoadFailedV2({ what }: { what: string }) {
  return <p className="py-space-2 text-t14 text-fg-muted">{`We could not load ${what}. We will try again in a moment.`}</p>;
}

/** Placeholder rows while a block's first read is under way (no figures, so nothing made up is shown). */
export function LoadingRowsV2({ rows = 2 }: { rows?: number }) {
  return (
    <div aria-busy="true" className="space-y-space-2">
      <span className="sr-only">Loading</span>
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} className="h-10" />)}
    </div>
  );
}

/** A small rounded label: Paper (outline), Live (filled with the text colour, §8), Waiting (brand), Taken (neutral). */
// Chart + colour order Part 3: the one `Chip` (src/ui/Chip.tsx) under the old name - the same look as before.
export function ChipV2({ children, kind = "outline" }: { children: ReactNode; kind?: "outline" | "solid" | "brand" | "warn" }) {
  const look = kind === "solid" ? { variant: "solid" as const }
    : kind === "brand" ? { tone: "brand" as const }
      : kind === "warn" ? { tone: "warn" as const }
        : { variant: "outline" as const };
  return <span className={chipClass(look)}>{children}</span>;
}

/** The colour class of a direction: up, down, or the plain text colour. */
export function toneClass(tone: "neutral" | "up" | "down" | "warn"): string {
  return tone === "up" ? "text-up" : tone === "down" ? "text-down" : tone === "warn" ? "text-warn" : "text-fg";
}
