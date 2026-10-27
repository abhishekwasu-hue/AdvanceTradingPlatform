import { cx } from "../components/primitives/cx";
import { formatNumber } from "./format";

/**
 * UX-11 U3 (owner, 27 Oct): a count towards a goal - "12 of 30" and a thin bar (Experience level, the 20-trade figures).
 * The bar is a progressbar for screen readers; the words say the same, so it never relies on the bar alone.
 */
export function progressPct(done: number, of: number): number {
  if (!Number.isFinite(done) || !Number.isFinite(of) || of <= 0) return 0;
  return Math.min(100, Math.max(0, (done / of) * 100));
}

export function ProgressStat({ label, done, of, unit, className }: {
  label: string; done: number; of: number; unit?: string; className?: string;
}) {
  const text = `${formatNumber(Math.max(0, done), 0)} of ${formatNumber(of, 0)}${unit ? ` ${unit}` : ""}`;
  return (
    <div className={cx("min-w-0", className)} data-progress={label}>
      <div className="flex items-baseline justify-between gap-space-3">
        <span className="text-t13 text-fg-muted">{label}</span>
        <span className="font-tabular text-t14 font-medium text-fg">{text}</span>
      </div>
      <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={of} aria-valuenow={Math.min(Math.max(0, done), of)}
        aria-valuetext={text} className="mt-space-1 h-1 overflow-hidden rounded-pill bg-surface-2">
        <div className="h-full rounded-pill bg-brand" style={{ width: `${progressPct(done, of)}%` }} />
      </div>
    </div>
  );
}
