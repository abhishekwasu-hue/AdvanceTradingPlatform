/**
 * U5: the condition canvas drawn as a funnel - the page's one memorable element. The universe enters at the top as a
 * count, every stage narrows the stream, and the survivor trail on the right edge shows how many symbols are still in
 * after each stage. The stages sit inside one ALL bracket (a continuous left edge); ANY / NOT groups come with
 * GroupBracket. When a stage removes the last symbols its trail turns warn and offers to show what it removes.
 */
import { Plus } from "lucide-react";
import { useState, type DragEvent } from "react";
import { Button, cx } from "../components/primitives";
import { duplicateStage, moveStage, newStage, trail, type FunnelResult, type Registry, type Stage } from "./model";
import { StageRow } from "./StageRow";
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
  ranTexts: string[] | null;         // each enabled stage's ScreenQL at that run (counts only show while unchanged)
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
  const rows = trail(stages, funnel, props.ranTexts, registry);
  const total = funnel?.with_data ?? null;
  const enabledCount = stages.filter((s) => s.enabled).length;
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
            <Button size="sm" variant="primary" onClick={() => onChange([newStage(registry)])}><Plus size={14} /> Add a condition</Button>
          </div>
        </div>
      </section>
    );
  }

  let enabledIndex = -1;
  return (
    <section aria-label="Conditions" className="rounded-panel border border-border bg-surface-1 p-4" data-run={props.runKey}>
      <FunnelHead universe={props.universe} withData={funnel?.with_data ?? null} />
      <div className="relative mt-3 pl-6">
        {/* the ALL bracket: one continuous left edge with its label - the group, not a nested grey box */}
        <div aria-hidden className="absolute bottom-2 left-1 top-2 w-3 rounded-l-control border-y border-l border-fg-muted/50" />
        <span className="absolute -left-1 top-1/2 -translate-y-1/2 -rotate-90 text-t12 font-medium text-fg-muted" aria-hidden>All</span>
        <ol aria-label="Stages - every one must pass" className="rounded-control border border-border bg-surface">
          {stages.map((s, i) => {
            if (s.enabled) enabledIndex += 1;
            const delay = s.enabled ? enabledIndex * step : 0;
            return (
              <StageRow
                  key={s.id} trailDelayMs={delay}
                  stage={s} index={i} registry={registry} scanTf={scanTf} trail={rows[i]} total={total}
                  problem={props.problems[s.id] ?? null}
                  onChange={(next) => update(s.id, next)}
                  onMove={(d) => onChange(moveStage(stages, s.id, d))}
                  onDuplicate={() => onChange(duplicateStage(stages, s.id))}
                  onRemove={() => onChange(stages.filter((x) => x.id !== s.id))}
                  onShowRemoved={() => { setShowing(showing === s.id ? null : s.id); props.onShowRemoved?.(s.id); }}
                  removedList={showing === s.id && rows[i].survivors != null ? funnel?.stages[enabledIndex]?.removed ?? [] : null}
                  onDragStart={(e) => { setDragId(s.id); e.dataTransfer.effectAllowed = "move"; }}
                  onDragOver={(e) => { if (dragId) e.preventDefault(); }}
                  onDrop={drop(s.id)}
                  dragging={dragId === s.id}
                />
            );
          })}
        </ol>
      </div>
      <div className="mt-3 flex items-center justify-between gap-3 pl-6">
        <Button size="sm" onClick={() => onChange([...stages, newStage(registry)])}><Plus size={14} /> Add a condition</Button>
        <MatchedFoot matched={props.matched} kills={rows.some((r) => r.kills)} />
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

function MatchedFoot({ matched, kills }: { matched: number | null; kills: boolean }) {
  return (
    <p className="text-t12 text-fg-muted" aria-live="polite">
      Matched{" "}
      <span className={cx("font-mono font-tabular text-t18 font-semibold", matched == null ? "text-fg-muted" : kills || matched === 0 ? "text-warn" : "text-signal")}>
        {matched ?? "–"}
      </span>
    </p>
  );
}
