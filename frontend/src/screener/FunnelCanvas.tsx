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
import starterRows from "./starters.json";
import "./screener.css";

export interface StarterTemplate { id: string; name: string; summary: string; build: (reg: Registry) => Stage[] }

/** How each starter builds its stages, by id. Names and summaries live in starters.json, which the server reads too:
 * U9 TX1 files every starter id in the screen taxonomy, and CI fails when one is not filed. */
export const STARTER_BUILDS: Record<string, (reg: Registry) => Stage[]> = {
  "starter.momentum": (r) => [newStage(r, "RSI", { kind: "value", value: 60 }), newStage(r, "close", { kind: "series", ref: { fn: "EMA", params: { x: "close", n: 20 }, tf: null } })],
  "starter.oversold": (r) => [newStage(r, "RSI", { kind: "value", value: 30 }, "<=")],
  "starter.daily_trend": (r) => { const s = newStage(r, "close", { kind: "series", ref: { fn: "EMA", params: { x: "close", n: 50 }, tf: "1d" } }); s.left.tf = "1d"; return [s]; },
};

/** Starting points, worded neutrally (a research tool, never a recommendation). A row without a build is left out. */
export const STARTERS: StarterTemplate[] = starterRows.starters
  .filter((t) => t.id in STARTER_BUILDS)
  .map((t) => ({ ...t, build: STARTER_BUILDS[t.id] }));

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
  /** U2 (flag `screener_builder_u2`): offer the formula block. */
  formula?: boolean;
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
            <KindPicker value={kind} onChange={setKind} formula={!!props.formula} />
          </div>
        </div>
      </section>
    );
  }

  let enabledIndex = -1;
  return (
    <section aria-label="Conditions" className="rounded-panel border border-border bg-surface-1 p-4" data-run={props.runKey}>
      <FunnelHead universe={props.universe} withData={total} />
      {/* SCR-FIX D4: the ALL bracket's label reads across, in words, above the stages (it was turned on its side) */}
      <p className="mt-3 pl-6 text-t12 font-medium text-fg-muted" aria-hidden data-testid="all-label">All of these must pass</p>
      <div className="relative mt-1 pl-6">
        {/* the ALL bracket: one continuous left edge - the group, not a nested grey box */}
        <div aria-hidden className="absolute bottom-2 left-1 top-2 w-3 rounded-l-control border-y border-l border-fg-muted/50" />
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
                  removedList={showing === s.id && rows[i].survivors != null ? funnel?.stages[enabledIndex]?.removed ?? "run" : null}
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
      {/* SCR-FIX D3: the footer wraps on a phone, so the Matched count stays inside the card */}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-2 pl-6">
        <span className="inline-flex min-w-0 flex-wrap items-center gap-1">
          <Button size="sm" onClick={() => onChange([...stages, newBlock(registry, kind)])}><Plus size={14} /> Add a condition</Button>
          <KindPicker value={kind} onChange={setKind} formula={!!props.formula} />
        </span>
        <MatchedFoot matched={props.matched} of={total} kills={rows.some((r) => r.kills)} live={!!props.live} />
      </div>
    </section>
  );
}

function FunnelHead({ universe, withData = null }: { universe: number; withData?: number | null }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
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
    // one announcement per Run; live previews update the number quietly (they come after every pause in editing)
    <p className="text-t12 text-fg-muted" aria-live={live ? "off" : "polite"} aria-atomic="true">
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

function KindPicker({ value, onChange, formula }: { value: StageKind; onChange: (k: StageKind) => void; formula: boolean }) {
  const kinds: StageKind[] = formula ? [...KINDS, "formula"] : KINDS;
  return <SelectInline label="Kind of condition to add" mono={false} value={value} options={kinds} format={(k) => KIND_LABEL[k as StageKind]} onChange={(k) => onChange(k as StageKind)} />;
}
