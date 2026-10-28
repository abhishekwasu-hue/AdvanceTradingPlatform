import type { ReactNode } from "react";
import { chipClass } from "../../ui/Chip";
import { Signed as SignedOne } from "../../ui/Signed";
import { cx } from "./cx";

/** A placeholder block while data loads (`aria-busy` on the region that contains it). */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx("animate-pulse rounded-md bg-surface-2", className ?? "h-4 w-full")} />;
}

/** "Nothing here yet" with the one action that fills it. */
export function EmptyState({ icon, title, body, action }: { icon?: ReactNode; title: ReactNode; body?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-6 py-10 text-center">
      {icon && <div className="text-fg-muted">{icon}</div>}
      <p className="text-sm font-semibold text-fg">{title}</p>
      {body && <p className="max-w-md text-sm text-fg-muted">{body}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export type Tone = "neutral" | "up" | "down" | "warn" | "info" | "brand";

/** A small label. Tone carries meaning only: up / down for direction or P&L, warn for attention.
 * Chart + colour order Part 3: the one `Chip` (src/ui/Chip.tsx) in its `badge` shape - the same look as before. */
export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return <span className={chipClass({ tone, shape: "badge", className })}>{children}</span>;
}

/** A number coloured by its sign (profit / loss), tabular digits, explicit + for gains. Colour-blind safe via tokens.
 * UX-11b C6: one Signed - this renders src/ui/Signed's `format` path (the same text and colour as before). */
export function Signed({ value, format = (v) => v.toFixed(2), className }: { value: number | null | undefined; format?: (v: number) => string; className?: string }) {
  return <SignedOne value={value} format={format} className={className} />;
}

/** UX-7: a list's placeholder rows while it loads (a label and a value per row), instead of a "Loading…" line. */
export function SkeletonRows({ rows = 5, label }: { rows?: number; label: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} data-skeleton className="space-y-space-2 px-space-3 py-space-3">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center justify-between gap-space-3">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-16" />
        </div>
      ))}
    </div>
  );
}
