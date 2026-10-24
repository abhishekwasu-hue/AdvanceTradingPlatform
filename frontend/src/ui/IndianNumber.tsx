import { useState } from "react";
import { cx } from "../components/primitives/cx";
import {
  formatChange, formatCompactIN, formatINR, formatNumber, formatPct, formatPrice, formatSignedCompactIN, formatSignedINR,
  meaningOf, type Meaning,
} from "./format";

/**
 * UI-H Wave 0 (H4): one way to show a number. Built on src/ui/format.ts (the one formatter; nothing is re-implemented
 * here):
 * - `kind`: "count" (OI, volume; compact Cr / L / K when `compact`), "money" (the money rule: whole rupees, `paise`
 *   only on tickets / fills; `compact` gives "₹2.4 Cr" from one crore up), "price" (decimals from the tick), "change"
 *   (a signed price change), "pct" (percent units, signed);
 * - `signed`: "+" / "−" on counts and money (P&L, OI change);
 * - `tone="meaning"`: up / down colour by sign (profit, rise); never for a plain level such as a price or OI;
 * - when the compact text hides digits, the full value is the hover title and the accessible name, and a tap swaps
 *   the compact text for the full one (phones have no hover).
 * Figures are tabular. A missing value is "–", never 0.
 */
export type NumberKind = "count" | "money" | "price" | "change" | "pct";

export interface IndianNumberProps {
  value: number | null | undefined;
  kind?: NumberKind;
  compact?: boolean;
  signed?: boolean;
  /** Decimals for count / pct (and the scaled figure of a compact count). */
  decimals?: number;
  /** price / change: the tick size that sets the decimals. */
  tick?: number;
  /** money: two decimals (order tickets, fills, closed-trade detail only). */
  paise?: boolean;
  tone?: "none" | "meaning";
  className?: string;
}

export const MEANING_CLASS: Record<Meaning, string> = { up: "text-up", down: "text-down", flat: "" };

/** The full and the shown text of a number (exported for tests). */
export function indianNumberText({ value, kind = "count", compact = false, signed = false, decimals, tick, paise }: IndianNumberProps):
  { full: string; shown: string } {
  switch (kind) {
    case "money": {
      const fmt = signed ? formatSignedINR : formatINR;
      return { full: fmt(value, { paise }), shown: fmt(value, { paise, compact }) };
    }
    case "price": { const t = formatPrice(value, tick); return { full: t, shown: t }; }
    case "change": { const t = formatChange(value, tick); return { full: t, shown: t }; }
    case "pct": { const t = formatPct(value, decimals ?? 2); return { full: t, shown: t }; }
    default: {
      const plain = formatNumber(value, 0);
      const full = signed && typeof value === "number" && value > 0 && /[1-9]/.test(plain) ? `+${plain}` : plain;
      if (!compact) return { full, shown: decimals !== undefined ? formatNumber(value, decimals) : full };
      return { full, shown: signed ? formatSignedCompactIN(value, { decimals }) : formatCompactIN(value, { decimals }) };
    }
  }
}

export function IndianNumber(props: IndianNumberProps) {
  const { value, tone = "none", className, decimals } = props;
  const { full, shown } = indianNumberText(props);
  const [expanded, setExpanded] = useState(false);
  const colour = tone === "meaning" ? MEANING_CLASS[meaningOf(value, props.kind === "money" ? 0 : decimals ?? 2)] : "";
  const base = cx("font-tabular", colour, className);
  if (shown === full) return <span className={base} data-number={full}>{shown}</span>;
  return (
    <button type="button" title={full} aria-label={full} onClick={() => setExpanded((x) => !x)} data-number={full}
      data-expanded={expanded ? "true" : undefined}
      className={cx(base, "rounded-control underline decoration-dotted decoration-1 underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand")}>
      {expanded ? full : shown}
    </button>
  );
}
