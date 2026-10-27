import type { ReactNode } from "react";
import { cx } from "../components/primitives/cx";

/**
 * Chart + colour order, Part 3 (docs/design/CHART_COLOUR_REVIEW.md, PR sequence item 8): the one chip. It merges the
 * v2 `ChipV2` (dashboard/CardV2.tsx) and the P1.2 `Badge` (components/primitives/Feedback.tsx); both keep their exports
 * and render exactly what they rendered before (pinned in Chip.test.tsx), so no page changes until it adopts `Chip`.
 *
 * Colour by meaning only (CC-1):
 * - neutral: plain facts, Paper (outline), a successful state (with a check icon - never green);
 * - brand: a selected filter, "Waiting"; up / down: direction and P&L only;
 * - warn: needs action; info: tips and information; ai: AI entry points only (Copilot, Vision, Connect AI).
 * Variants: `tint` (a soft background), `outline` (a border, no fill), `solid` (the one emphatic chip, Live: always the
 * text colour on the card, UI v2 §8 - never a hue).
 */
export type ChipTone = "neutral" | "brand" | "up" | "down" | "warn" | "info" | "ai";
export type ChipVariant = "tint" | "outline" | "solid";
/** `pill` is the v2 chip; `badge` is the P1.2 Badge's squarer look, kept for the pages that use it. */
export type ChipShape = "pill" | "badge";

const PILL_TINT: Record<ChipTone, string> = {
  neutral: "bg-surface-2 text-fg-muted",
  brand: "bg-brand/10 text-brand",
  up: "bg-up/10 text-up",
  down: "bg-down/10 text-down",
  warn: "bg-warn-tint text-warn",
  info: "bg-info-tint text-info",
  ai: "bg-ai-tint text-ai",
};
const PILL_OUTLINE: Record<ChipTone, string> = {
  neutral: "border border-border text-fg-muted",
  brand: "border border-brand/60 text-brand",
  up: "border border-up/40 text-up",
  down: "border border-down/40 text-down",
  warn: "border border-warn/40 text-warn",
  info: "border border-info/40 text-info",
  ai: "border border-ai/40 text-ai",
};
const SOLID = "bg-fg text-surface-1";
// The P1.2 Badge tones, unchanged (plus ai).
const BADGE: Record<ChipTone, string> = {
  neutral: "border-border bg-surface-2 text-fg-muted",
  up: "border-up/40 bg-up/10 text-up",
  down: "border-down/40 bg-down/10 text-down",
  warn: "border-warn/40 bg-warn/10 text-warn",
  info: "border-info/40 bg-info/10 text-info",
  brand: "border-brand/40 bg-brand/10 text-brand",
  ai: "border-ai/40 bg-ai/10 text-ai",
};
const PILL_BASE = "inline-flex h-5 shrink-0 items-center rounded-pill px-space-2 text-t12 font-medium";
const BADGE_BASE = "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-semibold";

/** The chip's class string (exported for the pieces that style a link or a button as a chip). */
export function chipClass({ tone = "neutral", variant = "tint", shape = "pill", icon = false, className }: {
  tone?: ChipTone; variant?: ChipVariant; shape?: ChipShape; icon?: boolean; className?: string;
} = {}): string {
  if (shape === "badge") return cx(BADGE_BASE, BADGE[tone], className);
  const colour = variant === "solid" ? SOLID : variant === "outline" ? PILL_OUTLINE[tone] : PILL_TINT[tone];
  return cx(PILL_BASE, icon && "gap-space-1", colour, className);
}

export interface ChipProps {
  tone?: ChipTone;
  variant?: ChipVariant;
  shape?: ChipShape;
  /** A small icon before the words (aria-hidden): the meaning never rests on colour alone. */
  icon?: ReactNode;
  /** The icon's own colour, when it differs from the chip's words (colour v2: the green check of a neutral chip). */
  iconClassName?: string;
  className?: string;
  children: ReactNode;
  /** Passed through as `data-chip`, for tests and the e2e checks. */
  name?: string;
}

export function Chip({ tone = "neutral", variant = "tint", shape = "pill", icon, iconClassName, className, children, name }: ChipProps) {
  return (
    <span data-chip={name} className={chipClass({ tone, variant, shape, icon: icon != null, className })}>
      {icon != null && <span aria-hidden className={cx("inline-flex shrink-0", iconClassName)}>{icon}</span>}
      {children}
    </span>
  );
}

/**
 * A filter chip (Signals, Option chain, Idea Library filters): selected = the brand outline with a light tint, every
 * other chip outlined. A real toggle button (`aria-pressed`), 44 px tall on a phone, 32 px with a mouse.
 */
export function FilterChip({ selected, onClick, children, className, disabled }: {
  selected: boolean; onClick: () => void; children: ReactNode; className?: string; disabled?: boolean;
}) {
  return (
    <button type="button" aria-pressed={selected} onClick={onClick} disabled={disabled}
      className={cx("inline-flex h-11 shrink-0 items-center gap-space-1 rounded-pill border px-space-3 text-t14 font-medium transition-colors duration-fast md:h-8",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:cursor-not-allowed disabled:opacity-50",
        selected ? "border-brand bg-brand/10 text-brand" : "border-border text-fg-muted hover:bg-surface-2 hover:text-fg", className)}>
      {children}
    </button>
  );
}
