/**
 * U5: the condition canvas drawn as a funnel - the page's one memorable element. The universe enters at the top as a
 * count, every stage narrows the stream, and the survivor trail on the right edge shows how many symbols are still in
 * after each stage. The stages sit inside one ALL bracket (a continuous left edge); a group stage draws its own ANY /
 * NOT bracket inside it (D2). When a stage removes the last symbols its trail turns warn and offers to show what it
 * removes. "Add a condition" adds a block of the kind picked beside it.
 */
import { Plus } from "lucide-react";
import { useEffect, useState, type DragEvent } from "react";
import { Button, cx } from "../components/primitives";
import { duplicateStage, isLive, moveStage, newBlock, newStage, trail, type FunnelResult, type Registry, type Stage, type StageKind } from "./model";
import { SelectInline } from "./ParamInline";
import { KIND_LABEL, StageRow } from "./StageRow";
import "./screener.css";

export interface StarterTemplate { name: string; summary: string; build: (reg: Registry) => Stage[] }

/** Starting points, worded neutrally (a research tool, never a recommendation). */
export const STARTERS: StarterTemplate[] = [
  { name: "Momentum", summary: "RSI(14) above 60 and close above EMA(close, 20)",
    build: (r) => [newStage(r, "RSI", { kind: "value", value: 60 }), newStage(r, "close", { kind: "series", ref: { fn: "EMA", params: { x: "close", n: 20 }, tf: null } })] },
  { name: "Oversold", summary: "RSI(14) at or below 30",
    build: (r) => [newStage(r, "RSI", { kind: "value", value: 30 }, "<=")] },
  { name: "Trend on the daily", summary: "close above EMA(close, 50) on 1d",
    build: (r) => { const s = newStage(r, "close", { kind: "series", ref: { fn: "EMA", params: { x: "close", n: 50 }, tf: "1d" } }); s.left.tf = "1d"; return [s]; } },
];

export interface FunnelCanvasProps {
  stages: Stage[];
  registry: Registry;
  scanTf: string;
  universe: number;                  // symbols the scan will read
  funnel: FunnelResult | null;       // the last run
  fresh: boolean;                    // the scan is still the one that ran (stages, timeframe, symbols): counts show only then
  live?: boolean;                    // the counts come from a live preview (not a stored run)
  matched: number | null;
  problems: Record<string, string>;  // stage id -> what to fix
  runKey?: number;                   // bumps on every run: replays the fill
  onChange: (stages: Stage[]) => void;
  onShowRemoved?: (stageId: string) => void;
}

export function FunnelCanvas(props: FunnelCanvasProps) {
  const { stages, registry, scanTf, funnel, onChange } = props;
  const [dragId, setDragId] = useState<string | null>(null);
  const [showing, setShowing] = useState<string | null>(null);
  const [kind, setKind] = useState<StageKind>("indicator");
  const rows = trail(stages, funnel, props.fresh);
  useEffect(() => { setShowing(null); }, [props.runKey]);              // a new run closes the old "what this removes" list
  const total = props.fresh ? funnel?.with_data ?? null : null;      // "with data" belongs to the run's universe only
  const enabledCount = stages.filter(isLive).length;
  const step = enabledCount ? Math.min(60, 300 / enabledCount) : 0;     // the fill reaches the last stage within 600 ms

  const update = (id: string, s: Stage) => onChange(stages.map((x) => (x.id === id ? s : x)));
  const drop = (targetId: string) => (e: DragEvent) => {
    e.preventDefault();
    if (!dragId || dragId === targetId) return;
    const from = stages.findIndex((s) => s.id === dragId);
    const to = stages.findIndex((s) => s.id === targetId);
    const out = stages.slice();
    const [moved] = out.splice(from, 1);
    out.splice(to, 0, moved);
    setDragId(null);
    onChange(out);
  };

  if (stages.length === 0) {
    return (
      <section aria-label="Conditions" className="rounded-panel border border-border bg-surface-1 p-4">
        <FunnelHead universe={props.universe} />
        <div className="mt-4 rounded-control border border-dashed border-border px-4 py-8 text-center">
          <p className="text-t15 text-fg">Start with a template or add your first condition.</p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {STARTERS.map((t) => (
              <Button key={t.name} size="sm" onClick={() => onChange(t.build(registry))} title={t.summary}>{t.name}</Button>
            ))}
            <Button size="sm" variant="primary" onClick={() => onChange([newBlock(registry, kind)])}><Plus size={14} /> Add a condition</Button>
            <KindPicker value={kind} onChange={setKind} />
          </div>
        </div>
      </section>
    );
  }

  let enabledIndex = -1;
  return (
    <section aria-label="Conditions" className="rounded-panel border border-border bg-surface-1 p-4" data-run={props.runKey}>
      <FunnelHead universe={props.universe} withData={total} />
      <div className="relative mt-3 pl-6">
        {/* the ALL bracket: one continuous left edge with its label - the group, not a nested grey box */}
        <div aria-hidden className="absolute bottom-2 left-1 top-2 w-3 rounded-l-control border-y border-l border-fg-muted/50" />
        <span className="absolute -left-1 top-1/2 -translate-y-1/2 -rotate-90 text-t12 font-medium text-fg-muted" aria-hidden>All</span>
        <ol aria-label="Stages - every one must pass" className="rounded-control border border-border bg-surface">
          {stages.map((s, i) => {
            if (isLive(s)) enabledIndex += 1;                                   // an empty group is not a stage
            const delay = isLive(s) ? enabledIndex * step : 0;
            return (
              <StageRow
                  key={s.id} trailDelayMs={delay}
                  stage={s} index={i} registry={registry} scanTf={scanTf} trail={rows[i]} total={total}
                  problem={props.problems[s.id] ?? null} problems={props.problems}
                  onChange={(next) => update(s.id, next)}
                  onMove={(d) => onChange(moveStage(stages, s.id, d))}
                  onDuplicate={() => onChange(duplicateStage(stages, s.id))}
                  onRemove={() => onChange(stages.filter((x) => x.id !== s.id))}
                  onShowRemoved={() => { setShowing(showing === s.id ? null : s.id); props.onShowRemoved?.(s.id); }}
                  removedList={showing === s.id && rows[i].survivors != null ? funnel?.stages[enabledIndex]?.removed ?? [] : null}
                  onDragStart={(e) => { setDragId(s.id); e.dataTransfer.effectAllowed = "move"; }}
                  onDragEnd={() => setDragId(null)}
                  onDragOver={(e) => { if (dragId) e.preventDefault(); }}
                  onDrop={drop(s.id)}
                  dragging={dragId === s.id}
                />
            );
          })}
        </ol>
      </div>
      <div className="mt-3 flex items-center justify-between gap-3 pl-6">
        <span className="inline-flex items-center gap-1">
          <Button size="sm" onClick={() => onChange([...stages, newBlock(registry, kind)])}><Plus size={14} /> Add a condition</Button>
          <KindPicker value={kind} onChange={setKind} />
        </span>
        <MatchedFoot matched={props.matched} of={total} kills={rows.some((r) => r.kills)} live={!!props.live} />
      </div>
    </section>
  );
}

function FunnelHead({ universe, withData = null }: { universe: number; withData?: number | null }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <h2 className="text-t15 font-semibold text-fg">Conditions</h2>
      <p className="text-t12 text-fg-muted">
        Universe <span className="font-mono font-tabular text-t15 font-semibold text-fg">{universe}</span>
        {withData != null && withData !== universe && <span> · <span className="font-mono font-tabular">{withData}</span> with data</span>}
      </p>
    </div>
  );
}

/** The canvas's one live region: a single summary per run ("Matched 4 of 48"), not a count per stage. */
function MatchedFoot({ matched, of, kills, live }: { matched: number | null; of: number | null; kills: boolean; live: boolean }) {
  return (
    <p className="text-t12 text-fg-muted" aria-live="polite" aria-atomic="true">
      Matched{" "}
      <span className={cx("font-mono font-tabular text-t18 font-semibold", matched == null ? "text-fg-muted" : kills || matched === 0 ? "text-warn" : "text-signal")}>
        {matched ?? "–"}
      </span>
      {matched != null && of != null && <span> of <span className="font-mono font-tabular">{of}</span></span>}
      {matched == null && <span className="sr-only">not counted yet</span>}
      {matched != null && live && <span className="ml-1.5 rounded-full border border-border px-1.5 text-t12" title="Counted as you edit; Run stores a scan">live</span>}
    </p>
  );
}

const KINDS: StageKind[] = ["indicator", "filter", "category", "rank", "group"];

function KindPicker({ value, onChange }: { value: StageKind; onChange: (k: StageKind) => void }) {
  return <SelectInline label="Kind of condition to add" mono={false} value={value} options={KINDS} format={(k) => KIND_LABEL[k as StageKind]} onChange={(k) => onChange(k as StageKind)} />;
}
