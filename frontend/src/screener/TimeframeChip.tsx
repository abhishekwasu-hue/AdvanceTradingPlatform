/** U5: the timeframe a stage reads, as a chip. "Scan" means the scan's own timeframe (no `@tf`). Native select inside, so
 * the keyboard and screen readers get the platform's own list. */
import { cx } from "../components/primitives";
import { TIMEFRAMES, type Timeframe } from "./model";

export interface TimeframeChipProps { value: Timeframe | null; scanTf: string; label?: string; onChange: (tf: Timeframe | null) => void }

export function TimeframeChip({ value, scanTf, label = "Timeframe", onChange }: TimeframeChipProps) {
  return (
    <label className={cx("relative inline-flex h-6 items-center rounded-control border px-1.5 text-t12",
      value ? "border-border bg-surface-2 text-fg" : "border-dashed border-border text-fg-muted",
      "focus-within:ring-2 focus-within:ring-brand")}>
      <span className="sr-only">{label}</span>
      <span aria-hidden className="font-mono font-tabular">{value ?? scanTf}</span>
      <select
        className="absolute inset-0 cursor-pointer opacity-0"
        value={value ?? ""}
        onChange={(e) => onChange((e.target.value || null) as Timeframe | null)}
      >
        <option value="">Scan timeframe ({scanTf})</option>
        {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
      </select>
    </label>
  );
}
