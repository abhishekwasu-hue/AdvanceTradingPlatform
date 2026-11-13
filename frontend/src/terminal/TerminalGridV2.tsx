import { GripVertical, Minimize2, Move, X } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { cx } from "../components/primitives/cx";
import {
  COLUMNS, PANELS, ROWS, gutterLabel, gutters, maximiseView, movePanel, placeLimit, readingOrder, resetEdge, resizeAcrossEdge, resizeFromCorner,
  usedRows, type Gutter, type PanelId, type PanelPlace, type PresetId, type SizeLimit,
} from "./layoutModel";

/**
 * Trading Terminal T1 (TT-17): ATP's own 12-column panel grid - no grid library. A panel moves by its title bar (drag) and
 * resizes by its corner (drag); with the keyboard, its move button takes the arrow keys (move) and Shift + the arrow keys
 * (resize). A move or resize that would cover another panel or leave the grid is refused (the layout model's rule), so
 * the panels never overlap. Close is the panel's own ×; the toolbar opens panels again. Desktop only: the phone shows the
 * panels as tabs (PhoneTabsV2).
 *
 * M58 (T4): the line between two adjacent panels (a gutter: vertical between columns, horizontal between rows) is the
 * main way to resize - dragging it grows one side and shrinks the other down to its smallest size (layoutModel
 * resizeAcrossEdge); it takes the arrow keys too, and a double-click puts the ready layout's sizes back. A size or move
 * that cannot be applied says why at once (T4-2): a short note on the grid, and the line turns amber. A double-click on
 * a panel's title maximises it (the others collapse to their titles); again restores. The corner stays as a second way.
 *
 * Owner bug 13 Nov ("panels do not become small or big by dragging"): the corner refused every step that touched a
 * neighbour, so in the Option seller layout it did nothing; it now moves the shared line like a gutter (layoutModel
 * resizeFromCorner), and so do Shift + the arrow keys. A line drag snaps to whole columns / rows, so a guide now follows
 * the pointer at once while the panels snap behind it; the grab area of a line is 16 px (the 8 px gap plus 4 px into each
 * panel's edge).
 */
export const ROW_REM = 2.5;
const LIMIT_MS = 4000;

type Gesture = {
  id: PanelId | null; kind: "move" | "resize" | "edge"; gutter?: Gutter; startX: number; startY: number; base: PanelPlace | null; colPx: number; rowPx: number;
};
type Limit = { text: string; gutter?: string };
/** The guide that follows the pointer while a line is dragged (px, relative to the grid). */
type Guide = { axis: "x" | "y"; left: number; top: number; length: number };

function cellSize(grid: HTMLElement | null): { colPx: number; rowPx: number } {
  if (!grid) return { colPx: 80, rowPx: 48 };
  const style = window.getComputedStyle(grid);
  const gap = parseFloat(style.columnGap) || 0;
  const rowGap = parseFloat(style.rowGap) || 0;
  const row = parseFloat(style.gridAutoRows) || 40;
  return { colPx: (grid.clientWidth + gap) / COLUMNS, rowPx: row + rowGap };
}

/** T4-3 "Fit to screen": how many grid rows the screen shows below the grid's top (above the status bar). */
export function screenRows(): number {
  const grid = document.querySelector<HTMLElement>("[data-terminal-grid]");
  if (!grid) return 20;
  const { rowPx } = cellSize(grid);
  const status = document.querySelector<HTMLElement>("[data-tour='terminal-status']")?.offsetHeight ?? 0;
  const top = grid.getBoundingClientRect().top + window.scrollY;
  const room = window.innerHeight - status - (top < window.innerHeight / 2 ? top : 0);
  return Math.max(1, Math.floor(room / rowPx));
}

const gutterKey = (g: Gutter) => `${g.axis}:${[...g.before, ...g.after].join("|")}`;
const same = (a: readonly PanelPlace[], b: readonly PanelPlace[]) =>
  a.length === b.length && a.every((q, i) => q.id === b[i].id && q.x === b[i].x && q.y === b[i].y && q.w === b[i].w && q.h === b[i].h && q.open === b[i].open);

export function TerminalGridV2({ panels, preset, ready, onChange, onClose, render, title }: {
  panels: readonly PanelPlace[];
  /** The ready layout the working one came from: a double-click on a line puts its sizes back (T4-4). */
  preset: PresetId;
  /** The ready layout as shown, when it is not the preset's own panels (UX-3: the watchlist off). */
  ready?: readonly PanelPlace[];
  onChange: (panels: PanelPlace[]) => void;
  onClose: (id: PanelId) => void;
  render: (place: PanelPlace) => ReactNode;
  /** The panel's title (a chart shows its symbol). */
  title: (place: PanelPlace) => string;
}) {
  const grid = useRef<HTMLDivElement>(null);
  const gesture = useRef<Gesture | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const [draft, setDraft] = useState<PanelPlace[] | null>(null);
  const [said, setSaid] = useState("");
  const [limit, setLimit] = useState<Limit | null>(null);
  const [maxed, setMaxed] = useState<PanelId | null>(null);
  const [guide, setGuide] = useState<Guide | null>(null);
  const guideStart = useRef<Guide | null>(null);
  const shown = draft ?? [...panels];
  const maxId = maxed && panels.some((x) => x.id === maxed && x.open) ? maxed : null;
  const view = maxId ? maximiseView(shown, maxId) : shown;

  useEffect(() => () => window.clearTimeout(timer.current), []);

  /** T4-2: the limit, shown on the grid (and the line amber); it goes after a moment unless a drag is still on it. */
  function showLimit(next: SizeLimit | null, gutter?: Gutter, linger = true) {
    window.clearTimeout(timer.current);
    setLimit(next ? { text: next.text, gutter: gutter ? gutterKey(gutter) : undefined } : null);
    if (next && linger) timer.current = window.setTimeout(() => setLimit(null), LIMIT_MS);
  }

  function begin(e: ReactPointerEvent, g: Gesture) {
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    gesture.current = g;
    setDraft([...panels]);
  }

  function start(e: ReactPointerEvent, id: PanelId, kind: "move" | "resize") {
    if (e.button !== 0 || maxId) return;
    const base = panels.find((x) => x.id === id);
    if (!base) return;
    begin(e, { id, kind, startX: e.clientX, startY: e.clientY, base, ...cellSize(grid.current) });
  }

  function startEdge(e: ReactPointerEvent, gutter: Gutter) {
    if (e.button !== 0) return;
    begin(e, { id: null, kind: "edge", gutter, startX: e.clientX, startY: e.clientY, base: null, ...cellSize(grid.current) });
    const box = grid.current?.getBoundingClientRect();
    const own = (e.currentTarget as HTMLElement).getBoundingClientRect();
    if (box) {
      const g: Guide = gutter.axis === "x"
        ? { axis: "x", left: own.left - box.left + own.width / 2, top: own.top - box.top, length: own.height }
        : { axis: "y", left: own.left - box.left, top: own.top - box.top + own.height / 2, length: own.width };
      guideStart.current = g;
      setGuide(g);
    }
  }

  function move(e: ReactPointerEvent) {
    const g = gesture.current;
    if (!g) return;
    const dx = Math.round((e.clientX - g.startX) / g.colPx);
    const dy = Math.round((e.clientY - g.startY) / g.rowPx);
    if (g.kind === "edge" && g.gutter) {
      const s0 = guideStart.current;
      if (s0) setGuide(s0.axis === "x" ? { ...s0, left: s0.left + e.clientX - g.startX } : { ...s0, top: s0.top + e.clientY - g.startY });
      const r = resizeAcrossEdge(panels, g.gutter, g.gutter.axis === "x" ? dx : dy);
      setDraft(r.panels);
      showLimit(r.limit, g.gutter, false);
      return;
    }
    if (!g.base || !g.id) return;
    if (g.kind === "resize") {                                 // the corner: the shared lines move (owner bug 13 Nov)
      const r = resizeFromCorner(panels, g.id, g.base.w + dx, g.base.h + dy);
      setDraft(r.panels);
      showLimit(r.limit, undefined, false);
      return;
    }
    const [a, b] = [g.base.x + dx, g.base.y + dy];
    const next = movePanel(panels, g.id, a, b);
    if (next) setDraft(next);                                  // a place that covers another panel is skipped: the last good one stays
    showLimit(dx || dy ? placeLimit(panels, g.id, "move", a, b) : null, undefined, false);
  }

  function end() {
    const g = gesture.current;
    gesture.current = null;
    guideStart.current = null;
    setGuide(null);
    if (g && draft && !same(draft, panels)) onChange(draft);
    setDraft(null);
    if (limit) {                                               // the note stays a moment after the drag ends
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setLimit(null), LIMIT_MS);
    }
  }

  function key(e: KeyboardEvent, place: PanelPlace) {
    const step: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const d = step[e.key];
    if (!d) return;
    e.preventDefault();
    const label = PANELS[place.id].label;
    const [a, b] = e.shiftKey ? [place.w + d[0], place.h + d[1]] : [place.x + d[0], place.y + d[1]];
    const sized = e.shiftKey ? resizeFromCorner(panels, place.id, a, b) : null;
    const next = sized ? sized.panels : movePanel(panels, place.id, a, b);
    const why = sized ? sized.limit : placeLimit(panels, place.id, "move", a, b);
    if (why) showLimit(why);
    const after = next?.find((x) => x.id === place.id);
    if (!next || !after || (after.x === place.x && after.y === place.y && after.w === place.w && after.h === place.h)) {
      setSaid(`${label} cannot go there: ${why?.text ?? "another panel is in the way."}`);
      return;
    }
    setSaid(e.shiftKey ? `${label}: ${after.w} columns wide, ${after.h} rows high.` : `${label}: column ${after.x + 1}, row ${after.y + 1}.`);
    onChange(next);
  }

  function edgeKey(e: KeyboardEvent, gutter: Gutter) {
    const step: Record<string, number> = gutter.axis === "x" ? { ArrowLeft: -1, ArrowRight: 1 } : { ArrowUp: -1, ArrowDown: 1 };
    const d = step[e.key];
    if (!d) return;
    e.preventDefault();
    const r = resizeAcrossEdge(panels, gutter, d);
    showLimit(r.limit, gutter);
    if (r.moved) {
      onChange(r.panels);
      setSaid(sizesText(r.panels, [...gutter.before, ...gutter.after], gutter.axis));
    }
  }

  function reset(gutter: Gutter) {
    const r = resetEdge(panels, gutter, preset, ready);
    showLimit(r.limit, gutter);
    if (!same(r.panels, panels)) {
      onChange(r.panels);
      setSaid(sizesText(r.panels, [...gutter.before, ...gutter.after], gutter.axis));
    }
  }

  function toggleMax(id: PanelId) {
    const next = maxId === id ? null : id;
    setMaxed(next);
    showLimit(null);
    setSaid(next ? `${PANELS[id].label} is maximised. Double-click its title again to restore the layout.` : "The layout is restored.");
  }

  const rows = usedRows(view);
  return (
    <div className="relative">
      <div ref={grid} role="region" aria-label="Terminal panels" data-terminal-grid data-maximised={maxId ?? undefined}
        className="grid grid-cols-12 gap-space-2" style={{ gridAutoRows: `${ROW_REM}rem`, gridTemplateRows: `repeat(${rows}, ${ROW_REM}rem)` }}
        onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
        {/* The order of the props (not of a panel being dragged), so nothing is re-ordered in the page while a drag runs. */}
        {readingOrder(panels).map((base) => view.find((x) => x.id === base.id) ?? base).map((place) => {
          const active = draft !== null && gesture.current?.id === place.id;
          const name = title(place);
          const collapsed = maxId !== null && maxId !== place.id;
          const isMax = maxId === place.id;
          return (
            <section key={place.id} aria-label={name} data-panel={place.id} data-maximised={isMax ? "true" : undefined} data-collapsed={collapsed ? "true" : undefined}
              style={{ gridColumn: `${place.x + 1} / span ${place.w}`, gridRow: `${place.y + 1} / span ${place.h}` }}
              className={cx("relative flex min-h-0 min-w-0 flex-col overflow-hidden rounded-panel border bg-surface-1 shadow-card",
                active ? "border-brand ring-2 ring-brand" : "border-subtle")}>
              <header className={cx("flex shrink-0 items-center gap-space-1 px-space-2", !collapsed && "border-b border-subtle")}>
                <div onPointerDown={(e) => start(e, place.id, "move")} onDoubleClick={() => toggleMax(place.id)} data-panel-handle={place.id}
                  title={isMax || collapsed ? "Double-click to restore the layout" : "Drag to move · double-click to maximise"}
                  className={cx("flex min-h-9 min-w-0 flex-1 touch-none select-none items-center gap-space-1", maxId ? "cursor-pointer" : "cursor-grab active:cursor-grabbing")}>
                  <GripVertical size={14} aria-hidden className="shrink-0 text-fg-muted" />
                  <h2 className="truncate text-t14 font-semibold text-fg">{name}</h2>
                </div>
                {isMax ? (
                  <button type="button" onClick={() => toggleMax(place.id)} aria-label="Restore the layout" title="Restore the layout" data-panel-restore={place.id}
                    className="inline-flex min-h-9 min-w-9 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                    <Minimize2 size={14} aria-hidden />
                  </button>
                ) : !maxId && (
                  <button type="button" onKeyDown={(e) => key(e, place)} data-panel-keys={place.id}
                    aria-label={`Move or resize ${name}: arrow keys move it, Shift and the arrow keys resize it`} title="Move with the arrow keys (Shift to resize)"
                    className="inline-flex min-h-9 min-w-9 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                    <Move size={14} aria-hidden />
                  </button>
                )}
                <button type="button" onClick={() => onClose(place.id)} aria-label={`Close ${name}`} title="Close this panel" data-panel-close={place.id}
                  className="inline-flex min-h-9 min-w-9 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                  <X size={16} aria-hidden />
                </button>
              </header>
              {/* Collapsed (another panel maximised): only the title shows; the content stays as it was for the restore. */}
              <div className={cx("min-h-0 flex-1 overflow-auto p-space-3", collapsed && "hidden")}>{render(place)}</div>
              {!maxId && (
                <div onPointerDown={(e) => start(e, place.id, "resize")} data-panel-resize={place.id} aria-hidden title="Drag to resize"
                  className="absolute bottom-0 right-0 h-4 w-4 cursor-se-resize touch-none rounded-tl-control border-l border-t border-subtle bg-surface-2" />
              )}
            </section>
          );
        })}
        {/* T4-1: a line on every shared edge, full length, in the gap between the panels (8 px). */}
        {!maxId && gutters(shown).map((g) => {
          const k = gutterKey(g);
          const vertical = g.axis === "x";
          const dragging = draft !== null && gesture.current?.kind === "edge" && !!gesture.current.gutter && gutterKey(gesture.current.gutter) === k;
          const warn = limit?.gutter === k;
          return (
            <div key={k} role="separator" tabIndex={0} aria-orientation={vertical ? "vertical" : "horizontal"} aria-label={gutterLabel(g)}
              aria-valuenow={g.at} aria-valuemin={0} aria-valuemax={vertical ? COLUMNS : ROWS} aria-valuetext={sizesText(shown, [...g.before, ...g.after], g.axis)}
              data-gutter={g.axis} data-gutter-ids={[...g.before, ...g.after].join("|")} data-limit={warn ? "true" : undefined}
              title="Drag to resize · double-click for the ready sizes"
              onPointerDown={(e) => startEdge(e, g)} onDoubleClick={() => reset(g)} onKeyDown={(e) => edgeKey(e, g)}
              style={vertical ? { gridColumn: `${g.at + 1} / span 1`, gridRow: `${g.from + 1} / ${g.to + 1}` }
                : { gridRow: `${g.at + 1} / span 1`, gridColumn: `${g.from + 1} / ${g.to + 1}` }}
              className={cx("group relative z-10 touch-none rounded-pill transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
                // The grab area: the 8 px gap plus 4 px into each panel's edge (16 px); the line itself stays 8 px.
                "after:absolute after:content-['']", vertical ? "after:-inset-x-1 after:inset-y-0" : "after:-inset-y-1 after:inset-x-0",
                vertical ? "-ml-space-2 w-space-2 cursor-col-resize justify-self-start" : "-mt-space-2 h-space-2 cursor-row-resize self-start",
                warn ? "bg-warn" : dragging ? "bg-brand" : "hover:bg-brand focus-visible:bg-brand")}>
              <span aria-hidden className={cx("pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-pill",
                vertical ? "h-space-8 w-0.5" : "h-0.5 w-space-8", warn || dragging ? "bg-surface-1" : "bg-border group-hover:bg-surface-1")} />
            </div>
          );
        })}
      </div>
      {guide && (
        <div aria-hidden data-terminal-guide={guide.axis}
          className={cx("pointer-events-none absolute z-20 rounded-pill bg-brand", guide.axis === "x" ? "w-0.5 -translate-x-1/2" : "h-0.5 -translate-y-1/2")}
          style={guide.axis === "x" ? { left: guide.left, top: guide.top, height: guide.length } : { left: guide.left, top: guide.top, width: guide.length }} />
      )}
      {limit && (
        <p role="status" data-terminal-limit
          className="pointer-events-none absolute left-1/2 top-space-2 z-20 -translate-x-1/2 whitespace-nowrap rounded-control border border-warn bg-warn-tint px-space-3 py-space-1 text-t13 font-medium text-fg shadow-raised">
          {limit.text}
        </p>
      )}
      <p className="sr-only" aria-live="polite">{said}</p>
    </div>
  );
}

function sizesText(panels: readonly PanelPlace[], ids: readonly PanelId[], axis: "x" | "y"): string {
  return ids.map((id) => {
    const q = panels.find((x) => x.id === id);
    return q ? `${PANELS[id].label} ${axis === "x" ? `${q.w} columns wide` : `${q.h} rows high`}` : "";
  }).filter(Boolean).join(", ") + ".";
}
