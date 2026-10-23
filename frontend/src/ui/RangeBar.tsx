import { cx } from "../components/primitives/cx";
import { formatPrice } from "./format";

/**
 * Chart + colour order, Part 3 (CC-4): the range bar - the 52-week range, the day range, an IV range. A neutral track
 * (`--range-track`) with an amber-yellow marker (`--range`) where the current value sits, and the low / high always as
 * text: a yellow track fails contrast in the light theme, and the numbers must never rest on colour. A range is not a
 * warning, so it does not use `warn`.
 *
 * `position()` is exported for the tests: the marker's place as 0..100 (clamped), or null when the range is unknown.
 */
export function position(low: number | null | undefined, high: number | null | undefined, value: number | null | undefined): number | null {
  if (low == null || high == null || value == null) return null;
  if (![low, high, value].every(Number.isFinite) || high < low) return null;
  if (high === low) return 50;
  return Math.min(100, Math.max(0, ((value - low) / (high - low)) * 100));
}

export interface RangeBarProps {
  /** What the range is, in plain words ("52-week range", "Today's range"). */
  label: string;
  low: number | null | undefined;
  high: number | null | undefined;
  /** The current value; without it the bar shows the range only. */
  value?: number | null;
  format?: (v: number) => string;
  className?: string;
}

export function RangeBar({ label, low, high, value, format = (v) => formatPrice(v), className }: RangeBarProps) {
  const known = low != null && high != null && Number.isFinite(low) && Number.isFinite(high) && high >= low;
  const at = position(low, high, value);
  const text = !known ? `${label}: not available yet`
    : at == null ? `${label}: ${format(low)} to ${format(high)}`
      : `${label}: ${format(value as number)}, between ${format(low)} and ${format(high)}`;
  return (
    <div className={cx("min-w-0", className)} data-range-bar>
      <div className="flex items-baseline justify-between gap-space-2 text-t12">
        <span className="font-medium text-fg-muted">{label}</span>
        {at != null && <span className="font-tabular font-semibold text-fg">{format(value as number)}</span>}
      </div>
      <div role="meter" aria-label={label} aria-valuetext={text}
        aria-valuemin={known ? low : undefined} aria-valuemax={known ? high : undefined}
        aria-valuenow={at != null ? (value as number) : undefined}
        className="relative mt-space-1 h-1.5 rounded-pill bg-range-track">
        {at != null && (
          <span data-range-marker aria-hidden style={{ left: `${at}%` }}
            className="absolute top-1/2 h-3.5 w-1 -translate-x-1/2 -translate-y-1/2 rounded-pill bg-range ring-2 ring-surface-1" />
        )}
      </div>
      <div className="mt-space-1 flex justify-between gap-space-2 font-tabular text-t12 text-fg-muted">
        {known ? <><span>{format(low)}</span><span>{format(high)}</span></> : <span>Not available yet</span>}
      </div>
    </div>
  );
}
