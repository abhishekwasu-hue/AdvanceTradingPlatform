/**
 * M97 PR-4 / PR-5: the "Supply & Demand zones" options under the chart while the layer is on - the trading style (a
 * preset that only preselects timeframes: investor M, positional W, swing D / 2H, short-term 1H / 30m, intraday 15m /
 * 10m, scalper 5m / 3m), which timeframes' zones to draw (editable: an edit is kept as that style's own), and "Show
 * violated" (off by default). Saved in the user's chart settings on the server. Numbers first: one row of chips, the
 * explanation behind Details.
 */
import { cx } from "../components/primitives/cx";
import { Combobox } from "../ui/Combobox";
import { SD_STYLES, SD_STYLE_LABELS, SD_MAX_TIMEFRAMES, editTimeframes, pickStyle, styleTimeframes, type SdOverlayPrefs, type SdStyle } from "./sdStyles";
import { SD_LABEL, SD_TF_SHORT, sdTimeframesFor, toggleSdTimeframe, type SdTimeframe } from "./sdZones";
import type { TimeframeId } from "./timeframes";

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const OWN = "own";

export interface SdZonesBarProps { chartTf: TimeframeId; shown: readonly SdTimeframe[]; options: SdOverlayPrefs; onChange: (o: SdOverlayPrefs) => void; note: string | null }

/** "Swing · D, 2H" - a style with its timeframes (the user's own edit when he made one). */
export function styleOptionLabel(o: SdOverlayPrefs, style: SdStyle): string {
  return `${SD_STYLE_LABELS[style]} · ${styleTimeframes(o, style).map((t) => SD_TF_SHORT[t]).join(", ")}`;
}

export function SdZonesBar({ chartTf, shown, options, onChange, note }: SdZonesBarProps) {
  const offered = sdTimeframesFor(chartTf);
  const styles = [{ value: OWN, label: "My own timeframes" }, ...SD_STYLES.map((s) => ({ value: s, label: styleOptionLabel(options, s) }))];
  return (
    <section aria-label={`${SD_LABEL} options`} data-testid="sd-zones-bar" className="border-t border-subtle px-space-3 py-space-2 text-t13">
      <div className="flex flex-wrap items-center gap-x-space-2 gap-y-space-1">
        <h3 className="text-t13 font-semibold text-fg">{SD_LABEL}</h3>
        <Combobox label="Trading style" value={options.style ?? OWN} options={styles} dataAttrs={{ "data-sd-style": options.style ?? OWN }}
          onChange={(v) => onChange(pickStyle(options, v === OWN ? null : (v as SdStyle)))}
          triggerText={options.style ? SD_STYLE_LABELS[options.style] : "My own"} className="w-auto" />
        <div role="group" aria-label="Zones of these timeframes" className="flex flex-wrap gap-space-1">
          {offered.map((t) => {
            const on = shown.includes(t);
            return (
              <button key={t} type="button" aria-pressed={on} data-sd-tf={t}
                onClick={() => onChange(editTimeframes(options, toggleSdTimeframe(shown, t)))}
                className={cx("min-h-11 min-w-11 rounded-pill border px-space-2 text-t12 font-semibold md:min-h-8 md:min-w-8", focus,
                  on ? "border-brand bg-brand-tint text-brand" : "border-border text-fg-muted hover:text-fg")}>
                {SD_TF_SHORT[t]}
              </button>
            );
          })}
        </div>
        <label className="flex min-h-11 items-center gap-space-1 text-t12 text-fg md:min-h-8">
          <input type="checkbox" checked={options.show_violated} data-sd-violated
            onChange={(e) => onChange({ ...options, show_violated: e.target.checked })} className={cx("h-4 w-4", focus)} />
          Show violated
        </label>
      </div>
      {note && <p role="status" className="mt-space-1 text-t12 text-fg-muted">{note}</p>}
      <details className="mt-space-1 text-t12 text-fg-muted">
        <summary className={cx("cursor-pointer select-none", focus)}>Details</summary>
        <p className="mt-space-1">
          A zone is a base of small candles between two strong candles: demand when the move out of it went up, supply when it went down.
          Proximal is the edge nearest the price, distal the far edge. Fresh: not visited since it formed; Tested: visited (the count);
          Violated: a candle closed beyond the distal edge. A trading style only picks the timeframes; change them with the chips and
          your choice is kept for that style. Up to {SD_MAX_TIMEFRAMES} timeframes at once; closed candles only. Tap a zone for its numbers.
        </p>
      </details>
    </section>
  );
}
