/** U5: the timeframe a stage reads, as a chip. "Scan" means the scan's own timeframe (no `@tf`). SCR-FIX D8: the shared
 * picker (src/ui/Combobox.tsx: keyboard, screen readers and a phone sheet with 44 px rows), all nine timeframes. */
import { createContext, useContext } from "react";
import { Combobox } from "../ui/Combobox";
import { TIMEFRAMES, timeframeWords, type Timeframe } from "./model";

/** Day-one fix 4: true on the v2 Find setups page - timeframes read as words ("Daily"), never codes ("1d"). */
export const PlainTimeframes = createContext(false);

export interface TimeframeChipProps { value: Timeframe | null; scanTf: string; label?: string; onChange: (tf: Timeframe | null) => void }

export function TimeframeChip({ value, scanTf, label = "Timeframe", onChange }: TimeframeChipProps) {
  const plain = useContext(PlainTimeframes);
  const options = [
    { value: "", label: `Scan timeframe (${timeframeWords(scanTf, plain)})` },
    ...TIMEFRAMES.map((tf) => ({ value: tf, label: timeframeWords(tf, plain) })),
  ];
  return (
    <Combobox variant="inline" label={label} value={value ?? ""} options={options}
              onChange={(v) => onChange((v || null) as Timeframe | null)}
              triggerText={<span className={plain ? undefined : "font-mono font-tabular"}>{timeframeWords(value ?? scanTf, plain)}</span>}
              // a chosen timeframe is a filled chip; the scan's own one a dashed outline
              triggerClassName={value ? "!border-border !bg-surface-2 !text-t12" : "border-dashed !border-border !text-t12 !text-fg-muted"} />
  );
}
