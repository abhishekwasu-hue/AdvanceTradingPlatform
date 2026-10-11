/**
 * U5: one stage of the funnel - an indicator block. It reads as a sentence the trader edits in place:
 * "RSI (14) on 15m above 60". The indicator, the operator and every number are inline controls; the advanced fold
 * switches the right side between a number and another indicator and shows the stage's ScreenQL.
 *
 * Keyboard: the handle moves the stage with Alt+Up / Alt+Down; every control is a native button, input or select.
 */
import { ChevronDown, Copy, GripVertical, LineChart, Trash2 } from "lucide-react";
import { useId, useState, type DragEvent } from "react";
import { cx } from "../components/primitives";
import {
  defaultParams, indicatorNames, OPS, paramKinds, seriesRef, stageText, type Op, type Operand, type ParamValue, type Registry,
  type SeriesRef, type Stage, type TrailRow,
} from "./model";
import { NumberInline, SelectInline } from "./ParamInline";
import { SurvivorTrail } from "./SurvivorTrail";
import { TimeframeChip } from "./TimeframeChip";

const OP_LABEL: Record<Op, string> = { ">": "above", ">=": "at or above", "<": "below", "<=": "at or below", "crosses above": "crosses above", "crosses below": "crosses below" };

export interface StageRowProps {
  stage: Stage;
  index: number;
  registry: Registry;
  scanTf: string;
  trail: TrailRow;
  total: number | null;
  problem?: string | null;
  onChange: (s: Stage) => void;
  onMove: (delta: -1 | 1) => void;
  onDuplicate: () => void;
  onRemove: () => void;
  onShowRemoved?: () => void;
  onDragStart?: (e: DragEvent) => void;
  onDragOver?: (e: DragEvent) => void;
  onDrop?: (e: DragEvent) => void;
  dragging?: boolean;
  trailDelayMs?: number;        // the run's fill reaches this stage after the ones above it
  removedList?: string[] | null; // shown under the stage after "See what this removes"
}

function SeriesEditor({ value, registry, label, onChange }: { value: SeriesRef; registry: Registry; label: string; onChange: (s: SeriesRef) => void }) {
  const entry = registry[value.fn];
  const kinds = entry ? paramKinds(entry) : [];
  const setParam = (name: string, v: ParamValue) => onChange({ ...value, params: { ...value.params, [name]: v } });
  return (
    <span className="inline-flex items-center gap-0.5 whitespace-nowrap">
      <SelectInline label={`${label} indicator`} mono={false} value={value.fn} options={indicatorNames(registry)}
                    onChange={(fn) => onChange(seriesRef(registry, String(fn), value.tf))} />
      {kinds.length > 0 && <span className="font-mono text-t13 text-fg-muted" aria-hidden>(</span>}
      {kinds.map((k, i) => {
        const v = value.params[k.name] ?? (entry ? defaultParams(entry)[k.name] : 0);
        return (
          <span key={k.name} className="inline-flex items-center">
            {i > 0 && <span className="font-mono text-t13 text-fg-muted" aria-hidden>,&nbsp;</span>}
            {k.kind === "window" || k.kind === "number"
              ? <NumberInline value={Number(v)} label={`${value.fn} ${k.name}`} integer={k.kind === "window"} min={k.kind === "window" ? 1 : undefined}
                              onChange={(n) => setParam(k.name, n)} />
              : <SelectInline value={v} label={`${value.fn} ${k.name}`} options={k.options} onChange={(n) => setParam(k.name, n)} />}
          </span>
        );
      })}
      {kinds.length > 0 && <span className="font-mono text-t13 text-fg-muted" aria-hidden>)</span>}
    </span>
  );
}

export function StageRow(props: StageRowProps) {
  const { stage, index, registry, scanTf, trail, total, problem, onChange } = props;
  const [open, setOpen] = useState(false);
  const foldId = useId();
  const setRight = (right: Operand) => onChange({ ...stage, right });
  return (
    <li
      data-testid="stage-row"
      style={{ ["--trail-delay" as string]: `${props.trailDelayMs ?? 0}ms` }}
      onDragOver={props.onDragOver}
      onDrop={props.onDrop}
      className={cx("group relative border-b border-border last:border-b-0", !stage.enabled && "opacity-60", props.dragging && "opacity-40")}
    >
      <div className="flex min-h-stage flex-wrap items-center gap-x-2 gap-y-1 px-2 py-1">
        <button
          type="button"
          draggable
          onDragStart={props.onDragStart}
          onKeyDown={(e) => {
            if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); props.onMove(-1); }
            if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); props.onMove(1); }
          }}
          aria-label={`Stage ${index + 1}. Drag, or press Alt and an arrow key, to move it`}
          className="cursor-grab rounded-control p-1 text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand active:cursor-grabbing"
        >
          <GripVertical size={14} aria-hidden />
        </button>
        <LineChart size={14} className="shrink-0 text-fg-muted" aria-label="Indicator condition" />
        <div className="flex min-w-[14rem] flex-1 flex-wrap items-center gap-x-1.5 gap-y-1 text-t13 text-fg">
          <SeriesEditor value={stage.left} registry={registry} label="Left" onChange={(left) => onChange({ ...stage, left })} />
          <TimeframeChip value={stage.left.tf} scanTf={scanTf} label="Timeframe of the left side" onChange={(tf) => onChange({ ...stage, left: { ...stage.left, tf } })} />
          <SelectInline label="Comparison" mono={false} value={stage.op} options={OPS.map((o) => o)} format={(o) => OP_LABEL[o as Op]}
                        onChange={(op) => onChange({ ...stage, op: op as Op })} />
          {stage.right.kind === "value"
            ? <NumberInline value={stage.right.value} label="Compared with" onChange={(value) => setRight({ kind: "value", value })} />
            : <SeriesEditor value={stage.right.ref} registry={registry} label="Right"
                            onChange={(ref) => setRight({ kind: "series", ref })} />}
        </div>
        <div className="ml-auto flex items-center gap-0.5 opacity-100 sm:opacity-60 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
          <label className="inline-flex cursor-pointer items-center rounded-control p-1 focus-within:ring-2 focus-within:ring-brand" title={stage.enabled ? "Disable this stage" : "Enable this stage"}>
            <input type="checkbox" className="peer sr-only" checked={stage.enabled} onChange={(e) => onChange({ ...stage, enabled: e.target.checked })}
                   aria-label={`Stage ${index + 1} on`} />
            <span aria-hidden className="relative h-4 w-7 rounded-full bg-surface-3 transition-colors peer-checked:bg-signal/70">
              <span className={cx("absolute top-0.5 h-3 w-3 rounded-full bg-fg transition-transform", stage.enabled ? "translate-x-3.5" : "translate-x-0.5")} />
            </span>
          </label>
          <button type="button" onClick={props.onDuplicate} aria-label={`Duplicate stage ${index + 1}`} title="Duplicate"
                  className="rounded-control p-1 text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"><Copy size={14} /></button>
          <button type="button" onClick={props.onRemove} aria-label={`Remove stage ${index + 1}`} title="Remove"
                  className="rounded-control p-1 text-fg-muted hover:text-down focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"><Trash2 size={14} /></button>
          <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls={foldId} aria-label={`Advanced options for stage ${index + 1}`}
                  className="rounded-control p-1 text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
            <ChevronDown size={14} className={cx("transition-transform", open && "rotate-180")} />
          </button>
        </div>
        <SurvivorTrail survivors={trail.survivors} removed={trail.removed} total={total} kills={trail.kills} onShowRemoved={props.onShowRemoved} />
      </div>
      {problem && <p role="alert" className="px-10 pb-2 text-t12 text-warn">{problem}</p>}
      {props.removedList && (
        <p className="mx-10 mb-2 rounded-control bg-surface-inset px-3 py-2 text-t12 text-fg-muted">
          This stage removed{" "}
          {props.removedList.length ? <span className="font-mono text-fg">{props.removedList.join(", ")}</span> : "nothing"}.
        </p>
      )}
      {open && (
        <div id={foldId} className="mx-10 mb-2 flex flex-wrap items-center gap-3 rounded-control bg-surface-inset px-3 py-2 text-t12 text-fg-muted">
          <span>Compare with</span>
          <div role="radiogroup" aria-label="Compare with" className="inline-flex rounded-control border border-border">
            {(["value", "series"] as const).map((k) => (
              <button key={k} type="button" role="radio" aria-checked={stage.right.kind === k}
                      onClick={() => setRight(k === "value" ? { kind: "value", value: stage.right.kind === "value" ? stage.right.value : 0 } : { kind: "series", ref: seriesRef(registry, "close") })}
                      className={cx("px-2 py-0.5 first:rounded-l-control last:rounded-r-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
                        stage.right.kind === k ? "bg-surface-2 text-fg" : "hover:text-fg")}>
                {k === "value" ? "A number" : "Another indicator"}
              </button>
            ))}
          </div>
          {stage.right.kind === "series" && (
            <TimeframeChip value={stage.right.ref.tf} scanTf={scanTf} label="Timeframe of the right side"
                           onChange={(tf) => stage.right.kind === "series" && setRight({ kind: "series", ref: { ...stage.right.ref, tf } })} />
          )}
          <code className="ml-auto truncate font-mono text-t12 text-fg-muted" title="This stage in ScreenQL">{stageText(registry, stage)}</code>
        </div>
      )}
    </li>
  );
}
