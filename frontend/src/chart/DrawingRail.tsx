import { Ellipsis, Eye, EyeOff, Minus, MoveUpRight, PenLine, RectangleHorizontal, Ruler, Slash, Trash2, Type, type LucideIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { DrawingKind } from "../charting/drawings";
import type { useDrawingTools } from "../charting/DrawingToolbar";
import { TOOLBAR_KINDS } from "../charting/DrawingToolbar";
import { cx } from "../components/primitives/cx";
import { ConfirmBox } from "./ConfirmBox";
import { MORE_TOOLS, RAIL_TOOLS, deleteAllText, isMoreTool } from "./drawingRail";

/**
 * UI2-4c (Charts page plan §3, CHP-8): the drawing tools of the v2 Chart tab, on the drawing controller ChartV2 hands
 * over through `onTools` (saved per user and symbol; each new drawing records its timeframe).
 * - `DrawingRail` (tablet and desktop): a vertical icon rail left of the chart - the six tools, "More" (the other
 *   kinds), Hide all, Delete all (asks first, naming what goes).
 * - `DrawingMenu` (phone): one "Draw" button opening the same tools as a grid of 44 px buttons.
 * Undo and redo stay in the top bar. Hide all is a view setting: while on, no drawing is drawn and the tools rest.
 */
type Tools = ReturnType<typeof useDrawingTools>;
export interface DrawingToolsProps { tools: Tools | null; symbol: string; hidden: boolean; onHidden: (hidden: boolean) => void }

const ICONS: Partial<Record<DrawingKind, LucideIcon>> = {
  trendline: Slash, hline: Minus, ray: MoveUpRight, rectangle: RectangleHorizontal, text: Type, measure: Ruler,
};
const TITLES = Object.fromEntries(TOOLBAR_KINDS.map((t) => [t.kind, t.title])) as Record<DrawingKind, string>;
const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const toolBtn = (on: boolean) => cx("inline-flex items-center justify-center rounded-control disabled:opacity-40 disabled:hover:bg-transparent", focus,
  on ? "bg-surface-3 text-fg ring-1 ring-brand" : "text-fg-muted hover:bg-surface-2 hover:text-fg");

/** The shared actions: pick a tool (again: put it down), hide or show, and the Delete all question. */
function useDrawingActions({ tools, symbol, hidden }: DrawingToolsProps) {
  const view = tools?.view ?? null;
  const ctl = tools?.controller ?? null;
  const [asking, setAsking] = useState(false);
  const ready = !!view && !!ctl && !hidden;
  const drawings = view?.drawings ?? [];
  const deletable = drawings.some((d) => !d.locked);
  useEffect(() => { if (!ready) setAsking(false); }, [ready]);
  const pick = (kind: DrawingKind) => {
    if (!ctl) return;
    if (view?.tool === kind) { ctl.setTool(null); return; }
    if (kind === "text") {
      const text = window.prompt("Text for the label (no < or >)", "");
      if (text == null) return;
      ctl.setTool(kind, text);
      return;
    }
    ctl.setTool(kind);
  };
  const question = deleteAllText(symbol, drawings);
  const confirm = () => { setAsking(false); void ctl?.deleteAll(); };
  return { view, ready, deletable, pick, asking, setAsking, question, confirm };
}

/** "More": the other drawing kinds, as a small menu next to its button. */
function MoreTools({ active, disabled, onPick }: { active: DrawingKind | null; disabled: boolean; onPick: (kind: DrawingKind) => void }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open]);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  const on = isMoreTool(active);
  return (
    <div ref={ref} className="relative">
      <button ref={trigger} type="button" aria-label="More drawing tools" title="More drawing tools" aria-haspopup="dialog" aria-expanded={open}
        aria-controls={open ? id : undefined} disabled={disabled} onClick={() => setOpen((o) => !o)} className={cx("h-9 w-9", toolBtn(on))}>
        <Ellipsis size={16} aria-hidden />
      </button>
      {open && (
        <div id={id} role="dialog" aria-label="More drawing tools"
          className="absolute left-full top-0 z-30 ml-space-2 w-52 rounded-panel border border-subtle bg-surface-1 p-space-1 text-t14 text-fg shadow-raised">
          {MORE_TOOLS.map((t) => (
            <button key={t.kind} type="button" aria-pressed={active === t.kind} title={TITLES[t.kind]}
              onClick={() => { onPick(t.kind); setOpen(false); }}
              className={cx("flex min-h-9 w-full items-center rounded-control px-space-2 text-left", focus,
                active === t.kind ? "bg-surface-3 font-semibold" : "hover:bg-surface-2")}>
              {t.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** The left rail (from the md breakpoint up). */
export function DrawingRail(props: DrawingToolsProps) {
  const { view, ready, deletable, pick, asking, setAsking, question, confirm } = useDrawingActions(props);
  const { hidden, onHidden } = props;
  return (
    <div role="group" aria-label="Drawing tools" data-drawing-rail
      className="relative hidden shrink-0 flex-col items-center gap-space-1 border-r border-subtle p-space-1 md:flex">
      {RAIL_TOOLS.map((t) => {
        const Icon = ICONS[t.kind] ?? PenLine;
        return (
          <button key={t.kind} type="button" aria-label={t.label} title={TITLES[t.kind]} aria-pressed={view?.tool === t.kind}
            disabled={!ready} onClick={() => pick(t.kind)} className={cx("h-9 w-9", toolBtn(view?.tool === t.kind))}>
            <Icon size={16} aria-hidden />
          </button>
        );
      })}
      <MoreTools active={view?.tool ?? null} disabled={!ready} onPick={pick} />
      <span aria-hidden className="my-space-1 h-px w-6 bg-border" />
      <button type="button" aria-label="Hide all drawings" title={hidden ? "Show the drawings again" : "Hide all drawings"} aria-pressed={hidden}
        onClick={() => onHidden(!hidden)} className={cx("h-9 w-9", toolBtn(hidden))}>
        {hidden ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}
      </button>
      <button type="button" aria-label="Delete all drawings" title="Delete all drawings on this symbol" disabled={!ready || !deletable}
        onClick={() => setAsking(true)} className={cx("h-9 w-9", toolBtn(false))}>
        <Trash2 size={16} aria-hidden />
      </button>
      {asking && (
        <ConfirmBox {...question} confirm="Delete all" onConfirm={confirm} onCancel={() => setAsking(false)}
          className="absolute bottom-space-1 left-full z-30 ml-space-2 w-72" />
      )}
    </div>
  );
}

/** The phone's compact menu: one "Draw" button, the tools as a grid of 44 px buttons. Picking a tool closes it, so
 * the next tap on the chart places the drawing. */
export function DrawingMenu(props: DrawingToolsProps) {
  const { view, ready, deletable, pick, asking, setAsking, question, confirm } = useDrawingActions(props);
  const { hidden, onHidden } = props;
  const [open, setOpen] = useState(false);
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !asking && !e.defaultPrevented) { setOpen(false); trigger.current?.focus(); } };
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open, asking]);
  useEffect(() => { if (!open) setAsking(false); }, [open, setAsking]);
  const choose = (kind: DrawingKind) => { pick(kind); setOpen(false); };
  const cell = (on: boolean) => cx("flex min-h-11 flex-col items-center justify-center gap-space-1 px-space-1 py-space-1 text-t12", toolBtn(on));
  const active = !!view?.tool;
  return (
    // Not positioned itself: the menu opens under the whole top bar (which is), across the screen's width.
    <div ref={ref} className="inline-flex md:hidden">
      <button ref={trigger} type="button" aria-label="Drawing tools" title="Drawing tools" aria-haspopup="dialog" aria-expanded={open}
        aria-controls={open ? id : undefined} onClick={() => setOpen((o) => !o)}
        className={cx("min-h-11 min-w-11", toolBtn(active))}>
        <PenLine size={16} aria-hidden />
      </button>
      {open && (
        <div id={id} role="dialog" aria-label="Drawing tools"
          className="absolute inset-x-space-2 top-full z-30 mt-space-1 rounded-panel border border-subtle bg-surface-1 p-space-2 text-t14 text-fg shadow-raised">
          {asking ? (
            <ConfirmBox {...question} confirm="Delete all" onConfirm={() => { confirm(); setOpen(false); }} onCancel={() => setAsking(false)} flat />
          ) : (
            <>
              <div className="grid grid-cols-3 gap-space-1">
                {RAIL_TOOLS.map((t) => {
                  const Icon = ICONS[t.kind] ?? PenLine;
                  return (
                    <button key={t.kind} type="button" aria-pressed={view?.tool === t.kind} disabled={!ready} onClick={() => choose(t.kind)} className={cell(view?.tool === t.kind)}>
                      <Icon size={16} aria-hidden />{t.label}
                    </button>
                  );
                })}
              </div>
              <p className="mt-space-2 px-space-1 text-t12 font-medium text-fg-muted">More</p>
              <div className="grid grid-cols-2 gap-space-1">
                {MORE_TOOLS.map((t) => (
                  <button key={t.kind} type="button" aria-pressed={view?.tool === t.kind} disabled={!ready} onClick={() => choose(t.kind)}
                    className={cx("min-h-11 px-space-2 text-left text-t13", toolBtn(view?.tool === t.kind))}>
                    {t.label}
                  </button>
                ))}
              </div>
              <div className="mt-space-2 grid grid-cols-2 gap-space-1 border-t border-subtle pt-space-2">
                <button type="button" aria-pressed={hidden} onClick={() => onHidden(!hidden)} className={cx("min-h-11 gap-space-1 px-space-2 text-t13", toolBtn(hidden))}>
                  {hidden ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}Hide all
                </button>
                <button type="button" disabled={!ready || !deletable} onClick={() => setAsking(true)} className={cx("min-h-11 gap-space-1 px-space-2 text-t13", toolBtn(false))}>
                  <Trash2 size={16} aria-hidden />Delete all
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
