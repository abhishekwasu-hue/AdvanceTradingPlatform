import { Check, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { toneClass } from "../dashboard/CardV2";
import type { Tone } from "../dashboard/model";
import type { GlossaryKey } from "../ui/glossary";
import { TermHint } from "../ui/TermHint";

/**
 * UI2-9a (UI v2 proposal §8, §9): the pieces My positions, My portfolio and Risk limits share - the page title with its
 * one-line question, the tile grid (1 px gaps, no card inside a card), the plain notes (neutral with a check for "done",
 * amber with an icon for a real warning) and the v2 field and button classes. Semantic tokens only.
 */

/** A v2 button that is not the page's one primary action (UI-11: destructive actions are this neutral button too). */
export const BUTTON_SECONDARY = "inline-flex h-11 shrink-0 items-center justify-center gap-space-2 rounded-control border border-border bg-surface-1 px-space-4 text-t14 font-semibold text-fg transition-colors duration-fast hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-50 md:h-9";
/** The page's one primary action. */
export const BUTTON_PRIMARY = "inline-flex h-11 shrink-0 items-center justify-center gap-space-2 rounded-control bg-brand px-space-5 text-t14 font-semibold text-on-brand transition-colors duration-fast hover:bg-brand-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-50 md:h-9";
/** A text field or select: 44 px on a phone, 36 px with a mouse. */
export const FIELD = "h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand disabled:opacity-50 md:h-9";
export const LABEL = "mb-space-1 flex items-center gap-space-1 text-t12 font-medium text-fg-muted";

/** The page title and its question. On a phone the top bar already shows the title, so the heading is for readers only. */
export function PageTitleV2({ title, subtitle, action }: { title: string; subtitle: string; action?: ReactNode }) {
  return (
    <header className="flex flex-col gap-space-3 md:flex-row md:items-end md:justify-between">
      <div className="min-w-0">
        <h1 className="sr-only md:not-sr-only md:text-t24 md:font-semibold md:tracking-tight md:text-fg">{title}</h1>
        <p className="text-t14 text-fg-muted md:mt-space-1">{subtitle}</p>
      </div>
      {action}
    </header>
  );
}

/** A grid of figures with 1 px dividers (§8: "Groups inside a card use dividers or a tile grid with 1 px gaps"). */
export function TilesV2({ children, columns = 4 }: { children: ReactNode; columns?: 2 | 3 | 4 }) {
  const cols = columns === 4 ? "grid-cols-2 lg:grid-cols-4" : columns === 3 ? "grid-cols-1 sm:grid-cols-3" : "grid-cols-2";
  return <div className={`grid ${cols} gap-px overflow-hidden rounded-control border border-subtle bg-border-subtle`}>{children}</div>;
}

export function TileV2({ label, hint, value, tone = "neutral", sub, children, testId }: {
  label: string; hint?: GlossaryKey; value: string; tone?: Tone; sub?: string; children?: ReactNode; testId?: string;
}) {
  return (
    <div className="min-w-0 bg-surface-1 p-space-3 md:p-space-4" data-tile={testId}>
      <div className="flex items-center gap-space-1 text-t13 text-fg-muted">{label}{hint && <TermHint term={hint} />}</div>
      <div className={`mt-space-1 font-tabular text-t20 font-semibold ${toneClass(tone)}`}>{value}</div>
      {sub && <div className="mt-px text-t12 text-fg-muted">{sub}</div>}
      {children}
    </div>
  );
}

/** A thin bar for a share (0-100): neutral, amber from `warnAt`. */
export function ShareBarV2({ pct, warn = false }: { pct: number; warn?: boolean }) {
  const width = Math.min(100, Math.max(0, Number.isFinite(pct) ? pct : 0));
  return (
    <div aria-hidden className="mt-space-2 h-1 overflow-hidden rounded-pill bg-surface-3">
      <div className={`h-1 rounded-pill ${warn ? "bg-warn" : "bg-fg-muted"}`} style={{ width: `${width}%` }} />
    </div>
  );
}

/** A note on the page: "done" (neutral with a check), "info" (neutral) or "warn" (amber with an icon and a reason). */
export function NoteV2({ kind = "info", children, testId }: { kind?: "done" | "info" | "warn"; children: ReactNode; testId?: string }) {
  if (kind === "warn") {
    return (
      <p role="alert" data-note={testId} className="flex items-start gap-space-2 rounded-panel border border-warn/40 bg-warn-tint px-space-4 py-space-3 text-t14 text-fg">
        <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" />
        <span className="min-w-0">{children}</span>
      </p>
    );
  }
  return (
    <p role="status" data-note={testId} className="flex items-start gap-space-2 rounded-panel border border-subtle bg-surface-1 px-space-4 py-space-3 text-t14 text-fg">
      {kind === "done" && <Check size={16} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />}
      <span className="min-w-0">{children}</span>
    </p>
  );
}

/** A small rounded status label with an icon for a warning, so colour is never the only signal (§8 Accessibility). A
 * neutral one carries a check ("done", "normal") unless `check` is false (a plain description such as a size cut). */
export function StateChipV2({ tone, check = true, children }: { tone: Tone; check?: boolean; children: ReactNode }) {
  return tone === "warn" ? (
    <span className="inline-flex h-6 shrink-0 items-center gap-space-1 rounded-pill bg-warn-tint px-space-2 text-t12 font-semibold text-warn">
      <TriangleAlert size={12} aria-hidden />{children}
    </span>
  ) : (
    <span className="inline-flex h-6 shrink-0 items-center gap-space-1 rounded-pill border border-border px-space-2 text-t12 font-medium text-fg-muted">
      {check && <Check size={12} aria-hidden />}{children}
    </span>
  );
}
