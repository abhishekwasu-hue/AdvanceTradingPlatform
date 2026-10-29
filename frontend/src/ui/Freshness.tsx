import { asOfText } from "./StatusBadge";
import { Chip } from "./Chip";

/**
 * UI-28 U3.8 (owner, 28 Oct): the ONE "how fresh is this number" mark on a number card - "as of 14:32" in muted text,
 * or an amber "Delayed · as of 14:32" chip when the source says the figures are stale (TradingView / Kite time stamps).
 * The time is IST: a time on today's date, else the date and time (asOfText). Nothing without a time.
 */
export function freshnessText(at: string | number | Date | null | undefined, stale = false, now?: Date): string {
  const when = asOfText(at, now);
  if (!when) return "";
  return stale ? `Delayed · ${when}` : when;
}

export function Freshness({ at, stale = false, now, className = "" }: {
  at: string | number | Date | null | undefined; stale?: boolean | null; now?: Date; className?: string;
}) {
  const text = freshnessText(at, !!stale, now);
  if (!text) return null;
  return stale
    ? <span data-freshness="delayed" className={className}><Chip tone="warn" variant="meaning" icon="!" name="delayed">{text}</Chip></span>
    : <span data-freshness="fresh" className={`text-t12 text-fg-muted ${className}`}>{text}</span>;
}
