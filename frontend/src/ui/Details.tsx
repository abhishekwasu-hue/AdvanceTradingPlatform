import { Info } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cx } from "../components/primitives/cx";

/**
 * UX-11 U1 (owner, 27 Oct): the ONE "ⓘ Details" disclosure. Every explanation, method note, "how it is computed" and
 * source line of a card lives here - moved, never deleted. Closed by default; the open state is remembered per card on
 * this browser (`id`); a native <details> / <summary>, so the keyboard (Enter / Space) and screen readers work as is.
 * The text-budget guard (src/ui/textBudget.ts) does not count what is inside `[data-details]`.
 */
export const DETAILS_KEY = "atp_details_open:";

function readOpen(id: string): boolean {
  try { return localStorage.getItem(DETAILS_KEY + id) === "1"; } catch { return false; }
}
function writeOpen(id: string, open: boolean): void {
  try { if (open) localStorage.setItem(DETAILS_KEY + id, "1"); else localStorage.removeItem(DETAILS_KEY + id); } catch { /* intentional: storage unavailable */ }
}

export function Details({ id, label = "Details", children, className }: {
  /** A stable name per card ("positions.greeks"): the remembered open state. */
  id: string;
  label?: string;
  children: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(() => readOpen(id));
  return (
    <details data-details={id} open={open || undefined} className={cx("group mt-space-3", className)}
      onToggle={(e) => { const next = e.currentTarget.open; if (next !== open) { setOpen(next); writeOpen(id, next); } }}>
      <summary className="inline-flex min-h-11 cursor-pointer list-none items-center gap-space-1 rounded-control text-t13 font-medium text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-0 [&::-webkit-details-marker]:hidden">
        <Info size={14} aria-hidden />
        {label}
      </summary>
      <div className="mt-space-2 space-y-space-2 text-t13 text-fg-muted">{children}</div>
    </details>
  );
}

/** UX-11 on a card shared with today's pages: with `on` (the v2 pages) its explanation goes into Details; without, it
 * renders exactly as before. The text is moved, never removed. */
export function TextOnDemand({ id, on, children }: { id: string; on: boolean; children: ReactNode }) {
  return on ? <Details id={id}>{children}</Details> : <>{children}</>;
}
