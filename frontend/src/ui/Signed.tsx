import { cx } from "../components/primitives/cx";
import { EMPTY, meaningOf } from "./format";
import { indianNumberText, type NumberKind } from "./IndianNumber";

/**
 * UX-11 U2 (owner, 27 Oct): the ONE way a value with a direction is shown on the v2 pages - P&L, change, % change,
 * won / lost, drawdown, OI change, equity vs start. Positive: up colour + "▲" + "+"; negative: down colour + "▼" + "−";
 * zero (as shown) and missing: muted, no arrow. Never colour alone (WCAG 1.4.1): the sign and the arrow say it too; the
 * arrow is decoration for screen readers, the signed text is read. Tabular figures, Indian grouping, the up / down
 * tokens (colour-blind palette of colour_v2). Text from src/ui/format.ts via indianNumberText - nothing re-implemented.
 * (Today's pages keep components/primitives Signed until each page moves to v2.)
 */
export interface SignedProps {
  value: number | null | undefined;
  /** "money" (₹, whole rupees), "count" (OI, qty), "pct" (percent units), "change" (a price change, tick decimals). */
  kind?: Exclude<NumberKind, "price">;
  compact?: boolean;
  paise?: boolean;
  decimals?: number;
  tick?: number;
  /** Leave the arrow out where space is tight (a table cell); sign and colour stay. */
  arrow?: boolean;
  className?: string;
}

export function signedParts({ value, kind = "money", compact = false, paise, decimals, tick }: SignedProps):
  { text: string; full: string; dir: "up" | "down" | "flat" } {
  const { shown, full } = indianNumberText({ value, kind, compact, paise, decimals, tick, signed: true });
  if (shown === EMPTY) return { text: EMPTY, full: EMPTY, dir: "flat" };
  // The direction follows what is shown: a value that rounds to zero is flat (no sign, no arrow).
  const places = kind === "money" ? (paise ? 2 : 0) : kind === "count" ? (decimals ?? 0) : (decimals ?? 2);
  const dir = /[1-9]/.test(shown) ? meaningOf(value, places) : "flat";
  return { text: shown, full, dir };
}

const TONE = { up: "text-up", down: "text-down", flat: "text-fg-muted" } as const;
const ARROW = { up: "▲", down: "▼", flat: "" } as const;

export function Signed(props: SignedProps) {
  const { arrow = true, className } = props;
  const { text, full, dir } = signedParts(props);
  return (
    <span className={cx("inline-flex items-baseline gap-px whitespace-nowrap font-tabular", TONE[dir], className)}
      data-signed={dir} title={full !== text ? full : undefined}>
      {arrow && ARROW[dir] && <span aria-hidden className="text-t12">{ARROW[dir]}</span>}
      <span>{text}</span>
    </span>
  );
}
