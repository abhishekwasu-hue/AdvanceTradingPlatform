/**
 * U5: one stage of the funnel - a block that reads as a sentence the trader edits in place. D1 had the indicator block
 * ("RSI (14) on 15m above 60"); D2 adds the other forms - a filter ("Pattern doji"), a category ("Trend is UPTREND"),
 * a rank across the universe ("Rank of PctChange(close, 5) at or below 10") - and the group: ANY of its blocks or
 * NOT all of them, drawn as a bracket with its own label, its blocks nested inside (they have no trail of their own:
 * the funnel counts top-level stages). The advanced fold shows the block in plain words and as ScreenQL.
 *
 * Keyboard: the handle moves the stage with Alt+Up / Alt+Down and keeps the focus as the stage moves; every control is
 * a native button, input or select.
 */
import { ChevronDown, Copy, Filter, GripVertical, Layers, LineChart, ListOrdered, Plus, Tags, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState, type DragEvent, type ReactNode } from "react";
import { cx } from "../components/primitives";
import {
  defaultParams, duplicateStage, indicatorNames, isCategory, isFilter, namesWhere, newBlock, OPS, paramKinds, RANK_FNS, RANK_OPS,
  seriesRef, stageSummary, stageText, type GroupStage, type IndicatorStage, type Op, type Operand, type ParamValue,
  type Registry, type SeriesRef, type Stage, type StageKind, type TrailRow,
} from "./model";
import { NumberInline, SelectInline, TextInline } from "./ParamInline";
import { SurvivorTrail } from "./SurvivorTrail";
import { TimeframeChip } from "./TimeframeChip";

const OP_LABEL: Record<Op, string> = { ">": "above", ">=": "at or above", "<": "below", "<=": "at or below", "crosses above": "crosses above", "crosses below": "crosses below" };
export const KIND_LABEL: Record<StageKind, string> = { indicator: "Indicator", filter: "Filter", category: "Category", rank: "Rank", group: "Group" };
const KIND_ICON: Record<StageKind, typeof LineChart> = { indicator: LineChart, filter: Filter, category: Tags, rank: ListOrdered, group: Layers };

export interface StageRowProps {
  stage: Stage;
  index: number;
  registry: Registry;
  scanTf: string;
  trail: TrailRow | null;          // null inside a group: the funnel counts top-level stages
  total: number | null;
  problem?: string | null;
  problems?: Record<string, string>;
  onChange: (s: Stage) => void;
  onMove: (delta: -1 | 1) => void;
  onDuplicate: () => void;
  onRemove: () => void;
  onShowRemoved?: () => void;
  onDragStart?: (e: DragEvent) => void;
  onDragEnd?: (e: DragEvent) => void;   // a drop outside any stage (or Escape) still ends the drag
  onDragOver?: (e: DragEvent) => void;
  onDrop?: (e: DragEvent) => void;
  dragging?: boolean;
  trailDelayMs?: number;        // the run's fill reaches this stage after the ones above it
  removedList?: string[] | "run" | null; // shown under the stage after "See what this removes" ("run": live counts carry no list)
  nested?: boolean;
}

function SeriesEditor({ value, registry, label, names, onChange }: { value: SeriesRef; registry: Registry; label: string; names?: string[]; onChange: (s: SeriesRef) => void }) {
  const entry = registry[value.fn];
  const kinds = entry ? paramKinds(entry) : [];
  const setParam = (name: string, v: ParamValue) => onChange({ ...value, params: { ...value.params, [name]: v } });
  return (
    <span className="inline-flex items-center gap-0.5 whitespace-nowrap">
      <SelectInline label={`${label} indicator`} mono={false} value={value.fn} options={names ?? indicatorNames(registry)}
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
              : k.kind === "text"
                ? <TextInline value={String(v)} label={`${value.fn} ${k.name}`} onChange={(n) => setParam(k.name, n)} />
                : <SelectInline value={v} label={`${value.fn} ${k.name}`} options={k.options} onChange={(n) => setParam(k.name, n)} />}
          </span>
        );
      })}
      {kinds.length > 0 && <span className="font-mono text-t13 text-fg-muted" aria-hidden>)</span>}
    </span>
  );
}

function IndicatorBody({ stage, registry, scanTf, onChange }: { stage: IndicatorStage; registry: Registry; scanTf: string; onChange: (s: Stage) => void }) {
  const setRight = (right: Operand) => onChange({ ...stage, right });
  return (
    <>
      <SeriesEditor value={stage.left} registry={registry} label="Left" onChange={(left) => onChange({ ...stage, left })} />
      <TimeframeChip value={stage.left.tf} scanTf={scanTf} label="Timeframe of the left side" onChange={(tf) => onChange({ ...stage, left: { ...stage.left, tf } })} />
      <SelectInline label="Comparison" mono={false} value={stage.op} options={OPS.map((o) => o)} format={(o) => OP_LABEL[o as Op]}
                    onChange={(op) => onChange({ ...stage, op: op as Op })} />
      {stage.right.kind === "value"
        ? <NumberInline value={stage.right.value} label="Compared with" onChange={(value) => setRight({ kind: "value", value })} />
        : <SeriesEditor value={stage.right.ref} registry={registry} label="Right" onChange={(ref) => setRight({ kind: "series", ref })} />}
    </>
  );
}

function Tf({ call, registry, scanTf, label, onChange }: { call: SeriesRef; registry: Registry; scanTf: string; label: string; onChange: (s: SeriesRef) => void }) {
  if (!registry[call.fn]?.timeframed) return null;
  return <TimeframeChip value={call.tf} scanTf={scanTf} label={label} onChange={(tf) => onChange({ ...call, tf })} />;
}

function BlockBody({ stage, registry, scanTf, onChange }: { stage: Stage; registry: Registry; scanTf: string; onChange: (s: Stage) => void }) {
  switch (stage.kind) {
    case "indicator":
      return <IndicatorBody stage={stage} registry={registry} scanTf={scanTf} onChange={onChange} />;
    case "filter": {
      const set = (call: SeriesRef) => onChange({ ...stage, call });
      return (
        <>
          <SeriesEditor value={stage.call} registry={registry} label="Filter" names={namesWhere(registry, isFilter)} onChange={set} />
          <Tf call={stage.call} registry={registry} scanTf={scanTf} label="Timeframe of the filter" onChange={set} />
        </>
      );
    }
    case "category": {
      const set = (call: SeriesRef) => onChange({ ...stage, call, values: call.fn === stage.call.fn ? stage.values : (registry[call.fn]?.values.filter(Boolean).slice(0, 1) ?? []) });
      const choices = registry[stage.call.fn]?.values.filter(Boolean) ?? [];
      return (
        <>
          <SeriesEditor value={stage.call} registry={registry} label="Category" names={namesWhere(registry, isCategory)} onChange={set} />
          <Tf call={stage.call} registry={registry} scanTf={scanTf} label="Timeframe of the category" onChange={set} />
          <SelectInline label="Is or is not" mono={false} value={stage.negate ? "not" : "is"} options={["is", "not"]} format={(v) => (v === "is" ? "is" : "is not")}
                        onChange={(v) => onChange({ ...stage, negate: v === "not" })} />
          {choices.length
            ? <ValuePicker values={stage.values} choices={choices} onChange={(values) => onChange({ ...stage, values })} />
            : <TextInline value={stage.values.join(", ")} label={`${stage.call.fn} values, comma separated`}
                          onChange={(t) => onChange({ ...stage, values: t.split(",").map((x) => x.trim()).filter(Boolean) })} />}
        </>
      );
    }
    case "rank":
      return (
        <>
          <SelectInline label="Rank or percentile" mono={false} value={stage.fn} options={RANK_FNS.map((f) => f)} format={(f) => (f === "Rank" ? "Rank of" : "Percentile rank of")}
                        onChange={(fn) => onChange({ ...stage, fn: fn as typeof stage.fn })} />
          <SeriesEditor value={stage.of} registry={registry} label="Ranked" onChange={(of) => onChange({ ...stage, of })} />
          <SelectInline label="Rank comparison" mono={false} value={stage.op} options={RANK_OPS.map((o) => o)} format={(o) => OP_LABEL[o as Op]}
                        onChange={(op) => onChange({ ...stage, op: op as typeof stage.op })} />
          <NumberInline value={stage.value} label="Rank limit" min={stage.fn === "Rank" ? 1 : 0} onChange={(value) => onChange({ ...stage, value })} />
          <span className="text-t12 text-fg-muted">across the universe</span>
        </>
      );
    case "group":
      return (
        <SelectInline label="Group" mono={false} value={stage.op} options={["ANY", "NOT"]} format={(o) => (o === "ANY" ? "Any of these" : "None of these")}
                      onChange={(op) => onChange({ ...stage, op: op as GroupStage["op"] })} />
      );
  }
}

/** The values of a category with a fixed set: toggle chips (one or several). */
function ValuePicker({ values, choices, onChange }: { values: string[]; choices: string[]; onChange: (v: string[]) => void }) {
  return (
    <span role="group" aria-label="Values" className="inline-flex flex-wrap gap-1">
      {choices.map((c) => {
        const on = values.includes(c);
        return (
          <button key={c} type="button" aria-pressed={on}
                  aria-disabled={on && values.length === 1 ? true : undefined}
                  title={on && values.length === 1 ? "A category needs at least one value" : undefined}
                  onClick={() => { if (on && values.length === 1) return; onChange(on ? values.filter((v) => v !== c) : [...values, c]); }}
                  className={cx("rounded-control border px-1.5 font-mono text-t12 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
                    on ? "border-signal/60 bg-surface-2 text-fg" : "border-border text-fg-muted hover:text-fg")}>
            {c}
          </button>
        );
      })}
    </span>
  );
}

function GroupChildren({ group, registry, scanTf, problems, onChange }: { group: GroupStage; registry: Registry; scanTf: string; problems: Record<string, string>; onChange: (s: Stage) => void }) {
  const setChild = (id: string, next: Stage) => onChange({ ...group, children: group.children.map((c) => (c.id === id ? next : c)) });
  const label = group.op === "ANY" ? "Any" : "Not";
  const [adding, setAdding] = useState<StageKind>("indicator");
  return (
    <div className="relative mx-2 mb-2 ml-10 pl-5">
      <div aria-hidden className={cx("absolute bottom-1 left-0 top-1 w-3 rounded-l-control border-y border-l", group.op === "NOT" ? "border-warn/60" : "border-signal/60")} />
      <span aria-hidden className="absolute -left-3 top-1/2 -translate-y-1/2 -rotate-90 text-t12 font-medium text-fg-muted">{label}</span>
      <ol aria-label={group.op === "ANY" ? "Blocks - any one may pass" : "Blocks - the stage passes when they do not all pass"} className="rounded-control border border-border bg-surface">
        {group.children.map((c, i) => (
          <StageRow key={c.id} nested stage={c} index={i} registry={registry} scanTf={scanTf} trail={null} total={null}
                    problem={problems[c.id] ?? null} problems={problems}
                    onChange={(next) => setChild(c.id, next)}
                    onMove={(d) => {
                      const j = i + d;
                      if (j < 0 || j >= group.children.length) return;
                      const out = group.children.slice();
                      [out[i], out[j]] = [out[j], out[i]];
                      onChange({ ...group, children: out });
                    }}
                    onDuplicate={() => onChange({ ...group, children: duplicateStage(group.children, c.id) })}
                    onRemove={() => onChange({ ...group, children: group.children.filter((x) => x.id !== c.id) })} />
        ))}
      </ol>
      <div className="mt-1 flex items-center gap-1">
        <SelectInline label="Kind of block to add" mono={false} value={adding} options={(["indicator", "filter", "category", "rank"] as StageKind[])}
                      format={(k) => KIND_LABEL[k as StageKind]} onChange={(k) => setAdding(k as StageKind)} />
        <button type="button" onClick={() => onChange({ ...group, children: [...group.children, newBlock(registry, adding)] })}
                className="inline-flex items-center gap-1 rounded-control px-1.5 py-0.5 text-t12 text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          <Plus size={12} aria-hidden /> Add to this group
        </button>
      </div>
    </div>
  );
}

function IconButton({ label, title, onClick, children, danger }: { label: string; title: string; onClick: () => void; children: ReactNode; danger?: boolean }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={title}
            className={cx("rounded-control p-1 text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand", danger ? "hover:text-down" : "hover:text-fg")}>
      {children}
    </button>
  );
}

export function StageRow(props: StageRowProps) {
  const { stage, index, registry, scanTf, trail, total, problem, onChange } = props;
  const [open, setOpen] = useState(false);
  const foldId = useId();
  const summaryId = useId();
  const handle = useRef<HTMLButtonElement>(null);
  const moved = useRef(false);
  // moving a stage re-inserts its row, which drops the focus in some browsers: put it back on the handle (a move at
  // either end changes nothing, so the flag is cleared on the next frame either way)
  useEffect(() => {
    if (moved.current) { moved.current = false; handle.current?.focus(); }
  }, [index]);
  const move = (d: -1 | 1) => {
    moved.current = true;
    props.onMove(d);
    window.requestAnimationFrame(() => { moved.current = false; });
  };
  const Icon = KIND_ICON[stage.kind];
  const what = props.nested ? `Block ${index + 1}` : `Stage ${index + 1}`;
  return (
    <li
      data-testid={props.nested ? "group-block" : "stage-row"}
      data-stage-id={stage.id}
      data-kind={stage.kind}
      style={{ ["--trail-delay" as string]: `${props.trailDelayMs ?? 0}ms` }}
      onDragOver={props.onDragOver}
      onDrop={props.onDrop}
      className={cx("relative border-b border-border last:border-b-0", props.nested ? "group/block" : "group/stage", !stage.enabled && "opacity-60", props.dragging && "opacity-40")}
    >
      <div className="flex min-h-stage flex-wrap items-center gap-x-2 gap-y-1 px-2 py-1">
        <button
          ref={handle}
          type="button"
          draggable={!props.nested}
          onDragStart={props.onDragStart}
          onDragEnd={props.onDragEnd}
          onKeyDown={(e) => {
            if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); move(-1); }
            if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); move(1); }
          }}
          aria-label={`${what}. ${props.nested ? "Press" : "Drag, or press"} Alt and an arrow key, to move it`}
          aria-describedby={summaryId}
          className={cx("rounded-control p-1 text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
            props.nested ? "cursor-default" : "cursor-grab active:cursor-grabbing")}
        >
          <GripVertical size={14} aria-hidden />
        </button>
        <Icon size={14} className="shrink-0 text-fg-muted" aria-label={`${KIND_LABEL[stage.kind]} block`} />
        <div className="flex min-w-[14rem] flex-1 flex-wrap items-center gap-x-1.5 gap-y-1 text-t13 text-fg">
          <BlockBody stage={stage} registry={registry} scanTf={scanTf} onChange={onChange} />
        </div>
        <div className={cx("ml-auto flex items-center gap-0.5 opacity-100 sm:opacity-60",
          props.nested ? "sm:group-focus-within/block:opacity-100 sm:group-hover/block:opacity-100" : "sm:group-focus-within/stage:opacity-100 sm:group-hover/stage:opacity-100")}>
          <label className="inline-flex cursor-pointer items-center rounded-control p-1 focus-within:ring-2 focus-within:ring-brand" title={stage.enabled ? "Disable this block" : "Enable this block"}>
            <input type="checkbox" className="peer sr-only" checked={stage.enabled} onChange={(e) => onChange({ ...stage, enabled: e.target.checked })}
                   aria-label={`${what} on`} />
            <span aria-hidden className="relative h-4 w-7 rounded-full bg-surface-3 transition-colors peer-checked:bg-signal/70">
              <span className={cx("absolute top-0.5 h-3 w-3 rounded-full bg-fg transition-transform", stage.enabled ? "translate-x-3.5" : "translate-x-0.5")} />
            </span>
          </label>
          <IconButton label={`Duplicate ${what.toLowerCase()}`} title="Duplicate" onClick={props.onDuplicate}><Copy size={14} /></IconButton>
          <IconButton label={`Remove ${what.toLowerCase()}`} title="Remove" onClick={props.onRemove} danger><Trash2 size={14} /></IconButton>
          <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls={foldId} aria-label={`Advanced options for ${what.toLowerCase()}`}
                  className="rounded-control p-1 text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
            <ChevronDown size={14} className={cx("transition-transform", open && "rotate-180")} />
          </button>
        </div>
        {trail && <SurvivorTrail survivors={trail.survivors} removed={trail.removed} total={total} kills={trail.kills} onShowRemoved={props.onShowRemoved} />}
      </div>
      <span id={summaryId} className="sr-only">{stageSummary(registry, stage)}{stage.enabled ? "" : " (disabled)"}</span>
      {problem && <p role="alert" className="px-10 pb-2 text-t12 text-warn">{problem}</p>}
      {stage.kind === "group" && (
        <GroupChildren group={stage} registry={registry} scanTf={scanTf} problems={props.problems ?? {}} onChange={onChange} />
      )}
      {props.removedList && (
        <p className="mx-10 mb-2 rounded-control bg-surface-inset px-3 py-2 text-t12 text-fg-muted">
          {props.removedList === "run" ? "Live counts carry no symbol list - Run the scan to see what this stage removes." : (
            <>This stage removed{" "}
              {props.removedList.length ? <span className="font-mono text-fg">{props.removedList.join(", ")}</span> : "nothing"}.</>
          )}
        </p>
      )}
      {open && (
        <div id={foldId} className="mx-10 mb-2 flex flex-wrap items-center gap-3 rounded-control bg-surface-inset px-3 py-2 text-t12 text-fg-muted">
          {stage.kind === "indicator" && <CompareWith stage={stage} registry={registry} scanTf={scanTf} onChange={onChange} />}
          <span className="basis-full text-fg">Reads: {stageSummary(registry, stage)}</span>
          <code className="truncate font-mono text-t12 text-fg-muted" title="This block in ScreenQL">{stageText(registry, stage)}</code>
        </div>
      )}
    </li>
  );
}

function CompareWith({ stage, registry, scanTf, onChange }: { stage: IndicatorStage; registry: Registry; scanTf: string; onChange: (s: Stage) => void }) {
  const setRight = (right: Operand) => onChange({ ...stage, right });
  return (
    <>
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
    </>
  );
}
