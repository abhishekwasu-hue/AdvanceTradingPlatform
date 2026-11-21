import { ArrowLeftRight, Check, Copy, Lock, LockOpen, Trash2, Type, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import type { DrawingStyle } from "../charting/drawings";
import type { DrawingController, ToolView } from "../charting/tools";
import { cx } from "../components/primitives/cx";
import { drawingNote } from "./chartsPage";
import type { PlotInsets } from "./chartCore";
import { readChartPalette } from "./chartTheme";
import { toolLabel } from "./drawingRail";
import { EXTEND_OPTIONS, LINE_STYLES, WIDTHS, drawingSettings, shownColour, swatchColours, textProblem } from "./drawingSelection";

/**
 * Owner 21 Nov ("selected drawing i cant delete or move so make it like tradingview"): the floating bar of the
 * selected drawing, as pro charting apps have it - colour, line (width and style), extension, text, lock, copy, delete,
 * close. It sits over the chart's top, clear of the price axis (from md); on a phone it docks above the chart's bottom
 * controls. Each change is one `edit` (one undo step); delete asks nothing (Undo brings it back). Only the settings the
 * drawing's kind shows are offered (drawingSelection.ts). Nothing here touches an order.
 */
export interface DrawingSelectionBarProps {
  view: ToolView | null;
  controller: DrawingController | null;
  insets: PlotInsets | null;
  /** The chart's root (its `data-chart-theme`), for the theme's swatches. */
  root: Element | null;
  /** The controller's message (locked, not saved) as a short line under the bar; off where the page shows it. */
  showMessage?: boolean;
}

type Panel = "colour" | "line" | "extend" | "text" | null;

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const btn = (on = false) => cx("inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-space-1 rounded-control px-space-1 md:min-h-9 md:min-w-9",
  "disabled:opacity-40 disabled:hover:bg-transparent", focus, on ? "bg-surface-3 text-fg ring-1 ring-brand" : "text-fg-muted hover:bg-surface-2 hover:text-fg");
const DASH: Record<NonNullable<DrawingStyle["line"]>, string> = { solid: "border-solid", dashed: "border-dashed", dotted: "border-dotted" };

/** A short line in the drawing's width and style, in the current text colour (the Line button and its choices). */
function LineSample({ width, line }: { width: number; line: NonNullable<DrawingStyle["line"]> }) {
  return <span aria-hidden className={cx("inline-block w-6 border-t border-current", DASH[line])} style={{ borderTopWidth: width }} />;
}

function Swatch({ colour, size = 16 }: { colour: string; size?: number }) {
  return <span aria-hidden className="inline-block shrink-0 rounded-pill border border-subtle" style={{ width: size, height: size, backgroundColor: colour }} />;
}

export function DrawingSelectionBar({ view, controller, insets, root, showMessage = true }: DrawingSelectionBarProps) {
  const sel = view?.selected && !view.tool ? controller?.selectedDrawing() ?? null : null;
  const [panel, setPanel] = useState<Panel>(null);
  const [draft, setDraft] = useState("");
  const [draftProblem, setDraftProblem] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const triggers = useRef<Partial<Record<NonNullable<Panel>, HTMLButtonElement | null>>>({});
  const custom = useRef<HTMLInputElement>(null);
  const panelId = useId();
  const key = sel?.key ?? null;

  useEffect(() => { setPanel(null); setDraftProblem(null); }, [key]);
  useEffect(() => {
    if (!panel) return undefined;
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setPanel(null); };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [panel]);
  // The custom colour is saved once the picker closes ("change"), not on every move inside it ("input").
  useEffect(() => {
    const input = custom.current;
    if (!input || !key || !controller) return undefined;
    const onChange = () => { void controller.edit(key, { style: { color: input.value.toLowerCase() } }); setPanel(null); };
    input.addEventListener("change", onChange);
    return () => input.removeEventListener("change", onChange);
  }, [panel, key, controller]);

  if (!sel || !controller || !view) return null;
  const { drawing, locked } = sel;
  const can = drawingSettings(drawing.kind);
  const palette = readChartPalette(root);
  const colour = shownColour(drawing, palette);
  const width = Math.min(4, Math.max(1, drawing.style?.width ?? 1));
  const line = drawing.style?.line ?? "solid";
  const extend = drawing.style?.extend ?? "none";
  const name = toolLabel(drawing.kind);
  const message = showMessage ? drawingNote(view.message) : null;

  const edit = (patch: Parameters<DrawingController["edit"]>[1]) => { void controller.edit(sel.key, patch); };
  const toggle = (p: NonNullable<Panel>) => {
    setPanel((cur) => (cur === p ? null : p));
    if (p === "text") { setDraft(drawing.text ?? ""); setDraftProblem(null); }
  };
  const closePanel = () => {
    const p = panel;
    setPanel(null);
    if (p) triggers.current[p]?.focus();
  };
  const saveText = () => {
    const problem = textProblem(draft);
    setDraftProblem(problem);
    if (problem) return;
    if (draft.trim() !== (drawing.text ?? "")) edit({ text: draft.trim() });
    closePanel();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "Escape") return;
    e.stopPropagation();
    if (panel) closePanel(); else controller.select(null);
  };
  const trigger = (p: NonNullable<Panel>, label: string, content: ReactNode) => (
    <button ref={(el) => { triggers.current[p] = el; }} type="button" aria-label={label} title={label} aria-haspopup="dialog" aria-expanded={panel === p}
      aria-controls={panel === p ? panelId : undefined} disabled={locked} onClick={() => toggle(p)} className={btn(panel === p)} data-bar-control={p}>
      {content}
    </button>
  );

  const vars = { "--bar-right": `${insets?.right ?? 0}px`, "--bar-bottom": `${(insets?.bottom ?? 0) + 60}px` } as CSSProperties;
  const choice = (on: boolean) => cx("inline-flex min-h-11 items-center justify-center gap-space-1 rounded-control px-space-2 text-t13 md:min-h-9", focus,
    on ? "bg-surface-3 font-semibold text-fg ring-1 ring-brand" : "text-fg hover:bg-surface-2");

  return (
    <div data-drawing-selection-bar={drawing.kind} style={vars}
      className="pointer-events-none absolute inset-x-0 bottom-[var(--bar-bottom)] z-30 flex justify-center px-space-1 md:bottom-auto md:right-[var(--bar-right)] md:top-space-2">
      <div ref={ref} onKeyDown={onKey} className="pointer-events-auto relative max-w-full">
        <div role="toolbar" aria-label={`${name} settings`} aria-orientation="horizontal"
          className="flex max-w-full items-center gap-0 overflow-x-auto rounded-panel border border-subtle bg-surface-1 p-space-1 md:gap-space-1 text-t13 text-fg shadow-raised">
          {can.colour && trigger("colour", "Colour", <Swatch colour={colour} />)}
          {can.line && trigger("line", "Line width and style", <LineSample width={width} line={line} />)}
          {can.extend && trigger("extend", "Extend the line", <ArrowLeftRight size={16} aria-hidden />)}
          {can.text && trigger("text", "Edit the text", <Type size={16} aria-hidden />)}
          {(can.colour || can.line || can.extend || can.text) && <span aria-hidden className="mx-space-1 hidden h-5 w-px shrink-0 bg-border md:inline-block" />}
          <button type="button" aria-label={locked ? "Unlock" : "Lock"} title={locked ? "Unlock: it can be moved and changed again" : "Lock: it cannot be moved, changed or deleted"}
            aria-pressed={locked} onClick={() => void controller.toggleLock()} className={btn(locked)}>
            {locked ? <Lock size={16} aria-hidden /> : <LockOpen size={16} aria-hidden />}
          </button>
          <button type="button" aria-label="Duplicate" title="Duplicate" onClick={() => void controller.duplicate(sel.key)} className={btn()}>
            <Copy size={16} aria-hidden />
          </button>
          <button type="button" aria-label="Delete" title="Delete (Undo brings it back)" disabled={locked} onClick={() => void controller.deleteSelected()}
            className={cx(btn(), "hover:text-down")}>
            <Trash2 size={16} aria-hidden />
          </button>
          <span aria-hidden className="mx-space-1 hidden h-5 w-px shrink-0 bg-border md:inline-block" />
          <button type="button" aria-label="Close" title="Done (Esc)" onClick={() => controller.select(null)} className={btn()}>
            <X size={16} aria-hidden />
          </button>
        </div>

        {panel && (
          <div id={panelId} role="dialog" aria-label={panel === "colour" ? "Colour" : panel === "line" ? "Line width and style" : panel === "extend" ? "Extend the line" : "Edit the text"}
            className="absolute bottom-full left-1/2 z-30 mb-space-1 w-max max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-panel border border-subtle bg-surface-1 p-space-2 text-t13 text-fg shadow-raised md:bottom-auto md:top-full md:mb-0 md:mt-space-1">
            {panel === "colour" && (
              <div className="flex flex-wrap items-center gap-space-1">
                {swatchColours(palette).map((c) => (
                  <button key={c} type="button" aria-label={`Colour ${c}`} aria-pressed={c === colour} data-swatch={c}
                    onClick={() => { if (c !== colour) edit({ style: { color: c } }); closePanel(); }}
                    className={cx("inline-flex min-h-11 min-w-11 items-center justify-center rounded-control md:min-h-9 md:min-w-9", focus, c === colour ? "ring-2 ring-brand" : "hover:bg-surface-2")}>
                    <Swatch colour={c} size={20} />
                  </button>
                ))}
                <label className={cx("relative inline-flex min-h-11 cursor-pointer items-center gap-space-1 rounded-control px-space-2 text-fg-muted hover:bg-surface-2 hover:text-fg md:min-h-9", "focus-within:ring-2 focus-within:ring-brand")}>
                  <input ref={custom} type="color" aria-label="Custom colour" defaultValue={colour} className="h-5 w-5 cursor-pointer border-0 bg-transparent p-0" />
                  Custom
                </label>
              </div>
            )}
            {panel === "line" && (
              <div className="grid gap-space-2">
                <div role="radiogroup" aria-label="Width" className="flex items-center gap-space-1">
                  {WIDTHS.map((w) => (
                    <button key={w} type="button" role="radio" aria-checked={w === width} aria-label={`Width ${w}`}
                      onClick={() => { if (w !== width) edit({ style: { width: w } }); }} className={choice(w === width)}>
                      <LineSample width={w} line="solid" /><span className="tabular-nums">{w}</span>
                    </button>
                  ))}
                </div>
                <div role="radiogroup" aria-label="Style" className="flex items-center gap-space-1">
                  {LINE_STYLES.map((s) => (
                    <button key={s.id} type="button" role="radio" aria-checked={s.id === line} aria-label={s.label} title={s.label}
                      onClick={() => { if (s.id !== line) edit({ style: { line: s.id } }); }} className={choice(s.id === line)}>
                      <LineSample width={2} line={s.id} />
                    </button>
                  ))}
                </div>
              </div>
            )}
            {panel === "extend" && (
              <div role="radiogroup" aria-label="Extend the line" className="grid gap-space-1">
                {EXTEND_OPTIONS.map((o) => (
                  <button key={o.id} type="button" role="radio" aria-checked={o.id === extend}
                    onClick={() => { if (o.id !== extend) edit({ style: { extend: o.id } }); closePanel(); }} className={cx(choice(o.id === extend), "justify-start")}>
                    {o.label}
                  </button>
                ))}
              </div>
            )}
            {panel === "text" && (
              <form className="flex items-center gap-space-1" onSubmit={(e) => { e.preventDefault(); saveText(); }}>
                <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Label text" maxLength={200}
                  aria-invalid={!!draftProblem} aria-describedby={draftProblem ? `${panelId}-problem` : undefined}
                  className={cx("min-h-11 w-56 rounded-control border border-subtle bg-surface-2 px-space-2 text-t14 text-fg md:min-h-9", focus)} />
                <button type="submit" aria-label="Save the text" title="Save" className={btn()}><Check size={16} aria-hidden /></button>
              </form>
            )}
            {panel === "text" && draftProblem && <p id={`${panelId}-problem`} className="mt-space-1 text-t12 text-warn">{draftProblem}</p>}
          </div>
        )}

        {message && (
          <p role="status" data-drawing-bar-message className="mt-space-1 rounded-control bg-surface-1 px-space-2 py-space-1 text-center text-t12 text-warn shadow-raised">
            {message}
          </p>
        )}
      </div>
    </div>
  );
}
