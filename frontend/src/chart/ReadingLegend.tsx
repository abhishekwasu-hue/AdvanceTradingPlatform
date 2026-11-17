import { cx } from "../components/primitives/cx";
import type { AtpReading } from "./atpReading";

const TONE = { up: "bg-up-tint text-up", down: "bg-down-tint text-down", sideways: "bg-surface-2 text-fg" } as const;

/** Owner 15 Nov: the small legend under a chart that shows ATP's reading - the trend per timeframe as coloured chips
 * (green up, red down, neutral sideways), in the answer's own words ("Weekly ▼ Down · weak"). Read-only. */
export function ReadingLegend({ reading }: { reading: AtpReading }) {
  if (reading.trends.length === 0) return null;
  return (
    <section aria-label="ATP reading" data-testid="atp-reading-legend" className="border-t border-subtle px-space-3 py-space-2">
      <ul className="flex flex-wrap items-center gap-x-space-2 gap-y-space-1 text-t12">
        <li className="font-semibold text-fg-muted">ATP reading</li>
        {reading.trends.map((t) => (
          <li key={t.tf} data-reading-trend={t.tf} className={cx("inline-flex min-h-6 items-center rounded-pill px-space-2 font-semibold",
            t.trend ? TONE[t.trend] : "bg-surface-2 text-fg-muted")}>
            {t.label} {t.text}
          </li>
        ))}
      </ul>
    </section>
  );
}
