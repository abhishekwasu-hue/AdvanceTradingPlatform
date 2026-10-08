import type { ReactNode } from "react";
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
const TONE: Record<Tone, string> = {
  neutral: "border-border bg-surface-2 text-fg-muted",
  up: "border-up/40 bg-up/10 text-up",
  down: "border-down/40 bg-down/10 text-down",
  warn: "border-warn/40 bg-warn/10 text-warn",
  info: "border-info/40 bg-info/10 text-info",
  brand: "border-brand/40 bg-brand/10 text-brand",
};

/** A small label. Tone carries meaning only: up / down for direction or P&L, warn for attention. */
export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return <span className={cx("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-semibold", TONE[tone], className)}>{children}</span>;
}

/** A number coloured by its sign (profit / loss), tabular digits, explicit + for gains. Colour-blind safe via tokens. */
export function Signed({ value, format = (v) => v.toFixed(2), className }: { value: number | null | undefined; format?: (v: number) => string; className?: string }) {
  if (value == null || !Number.isFinite(value)) return <span className={cx("font-tabular text-fg-muted", className)}>-</span>;
  const tone = value > 0 ? "text-up" : value < 0 ? "text-down" : "text-fg-muted";
  return <span className={cx("font-tabular", tone, className)}>{value > 0 ? "+" : ""}{format(value)}</span>;
}
