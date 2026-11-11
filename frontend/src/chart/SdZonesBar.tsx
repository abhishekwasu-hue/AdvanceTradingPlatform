/**
 * M97 PR-4: the "Supply & Demand zones" options under the chart while the layer is on - which timeframes' zones to
 * draw (the chart's own first; a higher timeframe on a lower-timeframe chart, or any other) and "Show violated" (off by
 * default). Numbers first: one row of chips, the explanation behind Details.
 */
import { cx } from "../components/primitives/cx";
import { SD_LABEL, SD_MAX_TIMEFRAMES, SD_TF_SHORT, sdTimeframesFor, toggleSdTimeframe, type SdOptions, type SdTimeframe } from "./sdZones";
import type { TimeframeId } from "./timeframes";

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

export interface SdZonesBarProps { chartTf: TimeframeId; shown: readonly SdTimeframe[]; options: SdOptions; onChange: (o: SdOptions) => void; note: string | null }

export function SdZonesBar({ chartTf, shown, options, onChange, note }: SdZonesBarProps) {
  const offered = sdTimeframesFor(chartTf);
  return (
    <section aria-label={`${SD_LABEL} options`} data-testid="sd-zones-bar" className="border-t border-subtle px-space-3 py-space-2 text-t13">
      <div className="flex flex-wrap items-center gap-x-space-2 gap-y-space-1">
        <h3 className="text-t13 font-semibold text-fg">{SD_LABEL}</h3>
        <div role="group" aria-label="Zones of these timeframes" className="flex flex-wrap gap-space-1">
          {offered.map((t) => {
            const on = shown.includes(t);
            return (
              <button key={t} type="button" aria-pressed={on} data-sd-tf={t}
                onClick={() => onChange({ ...options, timeframes: toggleSdTimeframe(shown, t) })}
                className={cx("min-h-11 min-w-11 rounded-pill border px-space-2 text-t12 font-semibold md:min-h-8 md:min-w-8", focus,
                  on ? "border-brand bg-brand-tint text-brand" : "border-border text-fg-muted hover:text-fg")}>
                {SD_TF_SHORT[t]}
              </button>
            );
          })}
        </div>
        <label className="flex min-h-11 items-center gap-space-1 text-t12 text-fg md:min-h-8">
          <input type="checkbox" checked={options.showViolated} data-sd-violated
            onChange={(e) => onChange({ ...options, showViolated: e.target.checked })} className={cx("h-4 w-4", focus)} />
          Show violated
        </label>
      </div>
      {note && <p role="status" className="mt-space-1 text-t12 text-fg-muted">{note}</p>}
      <details className="mt-space-1 text-t12 text-fg-muted">
        <summary className={cx("cursor-pointer select-none", focus)}>Details</summary>
        <p className="mt-space-1">
          A zone is a base of small candles between two strong candles: demand when the move out of it went up, supply when it went down.
          Proximal is the edge nearest the price, distal the far edge. Fresh: not visited since it formed; Tested: visited (the count);
          Violated: a candle closed beyond the distal edge. Up to {SD_MAX_TIMEFRAMES} timeframes at once; closed candles only. Tap a zone for its numbers.
        </p>
      </details>
    </section>
  );
}
