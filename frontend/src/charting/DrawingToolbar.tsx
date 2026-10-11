/**
 * CH2c-2 (ADR-0023): the drawing tools on a chart - a hook that binds a `DrawingController` to the chart engine for
 * one symbol, and the toolbar that drives it. Drawings are saved per user and symbol (CH1), so every timeframe shows
 * the same ones. Nothing here touches an order.
 */
import { Lock, Redo2, Trash2, Undo2, Unlock } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { drawingsApi, type DrawingKind } from "./drawings";
import type { LightweightEngine } from "./lightweight";
import { DrawingController, type ToolView } from "./tools";
import { bindTools } from "./toolsBinding";

export const TOOLBAR_KINDS: { kind: DrawingKind; label: string; title: string }[] = [
  { kind: "trendline", label: "Line", title: "Trend line: two clicks" },
  { kind: "ray", label: "Ray", title: "Ray: two clicks, extends past the second" },
  { kind: "hline", label: "H-line", title: "Horizontal line: one click" },
  { kind: "vline", label: "V-line", title: "Vertical line: one click" },
  { kind: "rectangle", label: "Box", title: "Rectangle / zone: two corners" },
  { kind: "channel", label: "Channel", title: "Parallel channel: two clicks for the line, a third for the width" },
  { kind: "fib_retracement", label: "Fib", title: "Fibonacci retracement: the move's start, then its end" },
  { kind: "fib_extension", label: "Fib ext", title: "Fibonacci extension: the move (two clicks), then where it projects from" },
  { kind: "measure", label: "Measure", title: "Measure: change and % between two points" },
  { kind: "long_position", label: "Long", title: "Long position: entry, then stop, then target (a planning drawing only)" },
  { kind: "short_position", label: "Short", title: "Short position: entry, then stop, then target (a planning drawing only)" },
  { kind: "text", label: "Text", title: "Text: one click" },
];

/** Binds the drawing tools to a chart while `enabled`; returns the controller and its latest view. */
export function useDrawingTools(engine: LightweightEngine | null, element: HTMLElement | null, symbol: string | undefined,
                                exchange: string, enabled: boolean) {
  const [view, setView] = useState<ToolView | null>(null);
  const ctlRef = useRef<DrawingController | null>(null);
  useEffect(() => {
    if (!enabled || !engine || !element || !symbol) return undefined;
    const ctl = new DrawingController(drawingsApi, symbol, exchange);
    ctlRef.current = ctl;
    const unbind = bindTools(engine, ctl, element, window);
    const unsubscribe = ctl.subscribe(setView);
    let alive = true;
    ctl.load().catch((error: unknown) => {
      if (alive) setView({ ...ctl.view(), message: `Drawings could not be loaded: ${error instanceof Error ? error.message : String(error)}` });
    });
    return () => {
      alive = false;
      unsubscribe();
      unbind();
      ctlRef.current = null;
      setView(null);
    };
  }, [engine, element, symbol, exchange, enabled]);
  return { view, controller: ctlRef.current };
}

export function DrawingToolbar({ view, controller }: { view: ToolView; controller: DrawingController }) {
  const selected = view.selected ? view.drawings.find((d) => d.key === view.selected) : undefined;
  const pick = (kind: DrawingKind) => {
    if (view.tool === kind) { controller.setTool(null); return; }
    if (kind === "text") {
      const text = window.prompt("Text for the label (no < or >)", "");
      if (text == null) return;
      controller.setTool(kind, text);
      return;
    }
    controller.setTool(kind);
  };
  const btn = "rounded px-1.5 py-0.5 text-[10px] font-semibold";
  return (
    <div className="mb-1 flex flex-wrap items-center gap-1 text-[10px]" role="toolbar" aria-label="Drawing tools">
      {TOOLBAR_KINDS.map((t) => (
        <button key={t.kind} type="button" title={t.title} aria-pressed={view.tool === t.kind} onClick={() => pick(t.kind)}
                className={`${btn} ${view.tool === t.kind ? "bg-brand/15 text-brand ring-1 ring-brand/40" : "text-fg-muted hover:text-fg"}`}>
          {t.label}
        </button>
      ))}
      <span className="mx-1 h-3 w-px bg-border" />
      <button type="button" title="Undo (Ctrl+Z)" disabled={!view.canUndo} onClick={() => void controller.undo()} className={`${btn} text-fg-muted hover:text-fg disabled:opacity-40`}><Undo2 size={11} /></button>
      <button type="button" title="Redo (Ctrl+Shift+Z)" disabled={!view.canRedo} onClick={() => void controller.redo()} className={`${btn} text-fg-muted hover:text-fg disabled:opacity-40`}><Redo2 size={11} /></button>
      <button type="button" title={selected?.locked ? "Unlock the selected drawing" : "Lock the selected drawing"} disabled={!selected}
              onClick={() => void controller.toggleLock()} className={`${btn} text-fg-muted hover:text-fg disabled:opacity-40`}>
        {selected?.locked ? <Unlock size={11} /> : <Lock size={11} />}
      </button>
      <button type="button" title="Delete the selected drawing (Delete)" disabled={!selected || selected.locked}
              onClick={() => void controller.deleteSelected()} className={`${btn} text-fg-muted hover:text-down disabled:opacity-40`}><Trash2 size={11} /></button>
      {view.tool && <span className="text-fg-muted">Click on the chart to place it · Esc to cancel</span>}
      {view.message && <span className="max-w-[24rem] truncate text-warn" title={view.message}>{view.message}</span>}
    </div>
  );
}
