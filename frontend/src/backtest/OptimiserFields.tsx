import { Details } from "../ui/Details";
import { useId } from "react";
import { BUTTON_SECONDARY, FIELD, LABEL } from "../trading/PartsV2";
import { Combobox } from "../ui/Combobox";
import { METRIC_LABEL } from "../ui/strategyLabels";
import { MAX_COMBINATIONS, checkGrid, newRow, type GridRow, type Optimisable } from "./optimiserGrid";

/**
 * SB-UX SU7: the optimiser's search as fields (per setting: From / To / Step), never a JSON box. The rows start empty
 * (DEF-0); the button stays off until every row is valid, and the problems are said in words under the rows.
 */
const METRICS = Object.entries(METRIC_LABEL).map(([value, label]) => ({ value, label }));

function RowFields({ row, params, used, onChange, onRemove, index }: {
  row: GridRow; params: Optimisable[]; used: string[]; onChange: (r: GridRow) => void; onRemove: () => void; index: number;
}) {
  const ids = { key: useId(), from: useId(), to: useId(), step: useId() };
  const p = params.find((x) => x.key === row.key);
  const options = params.map((x) => ({ value: x.key, label: x.label, hint: `now ${x.current}`, disabled: x.key !== row.key && used.includes(x.key) }));
  return (
    <div className="grid grid-cols-3 gap-space-2 rounded-control border border-border p-space-2 md:grid-cols-[minmax(12rem,2fr)_1fr_1fr_1fr_auto] md:items-end" data-optimiser-row={index}>
      <div className="col-span-3 md:col-span-1">
        <label htmlFor={ids.key} className={LABEL}>Setting</label>
        <Combobox id={ids.key} label="Setting" value={row.key} options={options} onChange={(key) => onChange({ ...row, key })} />
      </div>
      <div>
        <label htmlFor={ids.from} className={LABEL}>From</label>
        <input id={ids.from} inputMode="decimal" className={FIELD} value={row.from} placeholder={p ? String(p.current) : ""} onChange={(e) => onChange({ ...row, from: e.target.value })} />
      </div>
      <div>
        <label htmlFor={ids.to} className={LABEL}>To</label>
        <input id={ids.to} inputMode="decimal" className={FIELD} value={row.to} onChange={(e) => onChange({ ...row, to: e.target.value })} />
      </div>
      <div>
        <label htmlFor={ids.step} className={LABEL}>Step</label>
        <input id={ids.step} inputMode="decimal" className={FIELD} value={row.step} onChange={(e) => onChange({ ...row, step: e.target.value })} />
      </div>
      <button type="button" onClick={onRemove}
        className="col-span-3 inline-flex min-h-11 items-center justify-start text-t14 font-medium text-down hover:underline md:col-span-1 md:min-h-9">
        Remove
      </button>
    </div>
  );
}

export function OptimiserFields({ params, rows, onRows, metric, onMetric, onRun, busy, ready }: {
  params: Optimisable[];
  rows: GridRow[];
  onRows: (rows: GridRow[]) => void;
  metric: string;
  onMetric: (m: string) => void;
  onRun: (grid: Record<string, number[]>) => void;
  busy: boolean;
  /** A backtest has run (the search uses its bars). */
  ready: boolean;
}) {
  const metricId = useId();
  const check = checkGrid(rows, params);
  const next = newRow(params, rows);
  if (params.length === 0) {
    return <p className="text-t13 text-fg-muted" data-optimiser-fields data-optimiser-empty>This strategy has no number settings to vary.</p>;
  }
  return (
    <div className="space-y-space-3" data-optimiser-fields>
      <div className="space-y-space-2">
        {rows.map((row, i) => (
          <RowFields key={i} index={i} row={row} params={params} used={rows.map((r) => r.key)}
            onChange={(r) => onRows(rows.map((x, j) => (j === i ? r : x)))} onRemove={() => onRows(rows.filter((_, j) => j !== i))} />
        ))}
      </div>
      {next && (
        <button type="button" className={BUTTON_SECONDARY} onClick={() => onRows([...rows, next])}>+ Add a setting to vary</button>
      )}
      <div className="flex flex-wrap items-end gap-space-3">
        <div className="w-full sm:w-56">
          <label htmlFor={metricId} className={LABEL}>Rank by</label>
          <Combobox id={metricId} label="Rank by" value={metric} options={METRICS} onChange={onMetric} />
        </div>
        <button type="button" className={BUTTON_SECONDARY} disabled={busy || !ready || check.grid === null}
          onClick={() => check.grid && onRun(check.grid)}>
          {busy ? "Searching…" : "Optimise"}
        </button>
        {check.grid && <span className="text-t13 text-fg-muted" data-optimiser-count>{check.combinations} of {MAX_COMBINATIONS} combinations</span>}
      </div>
      {rows.length > 0 && check.problems.length > 0 && (
        <ul className="space-y-space-1 text-t13 text-warn" role="status" data-optimiser-problems>
          {check.problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
      {!ready && <p className="text-t13 text-fg-muted">Run the backtest first: the search uses the same bars.</p>}
      <Details id="backtest.optimiser" className="mt-0"><p>The first 70% of the bars pick the settings; the last 30% check them.</p></Details>
    </div>
  );
}
