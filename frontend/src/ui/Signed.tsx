import { cx } from "../components/primitives/cx";
import { EMPTY, meaningOf } from "./format";
import { indianNumberText, type NumberKind } from "./IndianNumber";

/**
 * UX-11 U2 (owner, 27 Oct): the ONE way a value with a direction is shown on the v2 pages - P&L, change, % change,
 * won / lost, drawdown, OI change, equity vs start. Positive: up colour + "▲" + "+"; negative: down colour + "▼" + "−";
 * zero (as shown) and missing: muted, no arrow. Never colour alone (WCAG 1.4.1): the sign and the arrow say it too; the
 * arrow is decoration for screen readers, the signed text is read. Tabular figures, Indian grouping, the up / down
 * tokens (colour-blind palette of colour_v2). Text from src/ui/format.ts via indianNumberText - nothing re-implemented.
 * UX-11b C6: the ONE Signed. components/primitives `Signed` (the P1.2 pages: a caller's `format`, no arrow) now renders
 * through this component's `format` path, unchanged in what it shows.
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
  /** The P1.2 pages' own text (e.g. `v => v.toFixed(2)`): the sign follows what is shown, "+" for a gain, "-" when
   * missing. With `format` the arrow is off unless asked for. */
  format?: (v: number) => string;
}

/** The `format` path (the P1.2 Signed): a value that formats to zero ("0", "0.00", "-0") is flat and unsigned. */
export function formattedParts(value: number | null | undefined, format: (v: number) => string): { text: string; dir: "up" | "down" | "flat" } {
  if (value == null || !Number.isFinite(value)) return { text: "-", dir: "flat" };
  const text = format(value);
  const shown = Number(text.replace(/[^0-9.-]/g, ""));
  const sign = !Number.isFinite(shown) || shown === 0 ? 0 : Math.sign(value);
  return { text: sign > 0 ? `+${text}` : sign === 0 ? text.replace(/^-/, "") : text, dir: sign > 0 ? "up" : sign < 0 ? "down" : "flat" };
}

export function signedParts({ value, kind = "money", compact = false, paise, decimals, tick }: SignedProps):
  { text: string; full: string; dir: "up" | "down" | "flat" } {
  let { shown, full } = indianNumberText({ value, kind, compact, paise, decimals, tick, signed: true });
  // A count with fixed decimals (a figure shown without ₹, e.g. "+972.50"): indianNumberText leaves its sign out.
  if (kind === "count" && !compact && decimals !== undefined && typeof value === "number" && value > 0 && /[1-9]/.test(shown) && !shown.startsWith("+")) {
    shown = `+${shown}`;
    full = shown;
  }
  if (shown === EMPTY) return { text: EMPTY, full: EMPTY, dir: "flat" };
  // The direction follows what is shown: a value that rounds to zero is flat (no sign, no arrow).
  const places = kind === "money" ? (paise ? 2 : 0) : kind === "count" ? (decimals ?? 0) : (decimals ?? 2);
  const dir = /[1-9]/.test(shown) ? meaningOf(value, places) : "flat";
  return { text: shown, full, dir };
}

const TONE = { up: "text-up", down: "text-down", flat: "text-fg-muted" } as const;
const ARROW = { up: "▲", down: "▼", flat: "" } as const;

export function Signed(props: SignedProps) {
  const { format, className } = props;
  if (format) {
    const { text, dir } = formattedParts(props.value, format);
    if (!props.arrow) return <span className={cx("font-tabular", TONE[dir], className)}>{text}</span>;
    return (
      <span className={cx("inline-flex items-baseline gap-px whitespace-nowrap font-tabular", TONE[dir], className)} data-signed={dir}>
        {ARROW[dir] && <span aria-hidden className="text-t12">{ARROW[dir]}</span>}<span>{text}</span>
      </span>
    );
  }
  const { arrow = true } = props;
  const { text, full, dir } = signedParts(props);
  return (
    <span className={cx("inline-flex items-baseline gap-px whitespace-nowrap font-tabular", TONE[dir], className)}
      data-signed={dir} title={full !== text ? full : undefined}>
      {arrow && ARROW[dir] && <span aria-hidden className="text-t12">{ARROW[dir]}</span>}
      <span>{text}</span>
    </span>
  );
}
