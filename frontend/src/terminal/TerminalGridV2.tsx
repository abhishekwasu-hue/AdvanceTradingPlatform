import { GripVertical, Move, X } from "lucide-react";
import { useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { cx } from "../components/primitives/cx";
import { COLUMNS, PANELS, movePanel, readingOrder, resizePanel, usedRows, type PanelId, type PanelPlace } from "./layoutModel";

/**
 * Trading Terminal T1 (TT-17): ATP's own 12-column panel grid - no grid library. A panel moves by its title bar (drag) and
 * resizes by its corner (drag); with the keyboard, its move button takes the arrow keys (move) and Shift + the arrow keys
 * (resize). A move or resize that would cover another panel or leave the grid is refused (the layout model's rule), so
 * the panels never overlap. Close is the panel's own ×; the toolbar opens panels again. Desktop only: the phone shows the
 * panels as tabs (PhoneTabsV2).
 */
export const ROW_REM = 2.5;

type Gesture = { id: PanelId; kind: "move" | "resize"; startX: number; startY: number; base: PanelPlace; colPx: number; rowPx: number };

function cellSize(grid: HTMLElement | null): { colPx: number; rowPx: number } {
  if (!grid) return { colPx: 80, rowPx: 48 };
  const style = window.getComputedStyle(grid);
  const gap = parseFloat(style.columnGap) || 0;
  const rowGap = parseFloat(style.rowGap) || 0;
  const row = parseFloat(style.gridAutoRows) || 40;
  return { colPx: (grid.clientWidth + gap) / COLUMNS, rowPx: row + rowGap };
}

export function TerminalGridV2({ panels, onChange, onClose, render, title }: {
  panels: readonly PanelPlace[];
  onChange: (panels: PanelPlace[]) => void;
  onClose: (id: PanelId) => void;
  render: (place: PanelPlace) => ReactNode;
  /** The panel's title (a chart shows its symbol). */
  title: (place: PanelPlace) => string;
}) {
  const grid = useRef<HTMLDivElement>(null);
  const gesture = useRef<Gesture | null>(null);
  const [draft, setDraft] = useState<PanelPlace[] | null>(null);
  const [said, setSaid] = useState("");
  const shown = draft ?? [...panels];

  function start(e: ReactPointerEvent, id: PanelId, kind: Gesture["kind"]) {
    if (e.button !== 0) return;
    const base = panels.find((x) => x.id === id);
    if (!base) return;
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    gesture.current = { id, kind, startX: e.clientX, startY: e.clientY, base, ...cellSize(grid.current) };
    setDraft([...panels]);
  }

  function move(e: ReactPointerEvent) {
    const g = gesture.current;
    if (!g) return;
    const dx = Math.round((e.clientX - g.startX) / g.colPx);
    const dy = Math.round((e.clientY - g.startY) / g.rowPx);
    const next = g.kind === "move" ? movePanel(panels, g.id, g.base.x + dx, g.base.y + dy) : resizePanel(panels, g.id, g.base.w + dx, g.base.h + dy);
    if (next) setDraft(next);                                  // a place that covers another panel is skipped: the last good one stays
  }

  function end() {
    const g = gesture.current;
    gesture.current = null;
    if (g && draft) {
      const after = draft.find((x) => x.id === g.id);
      if (after && (after.x !== g.base.x || after.y !== g.base.y || after.w !== g.base.w || after.h !== g.base.h)) onChange(draft);
    }
    setDraft(null);
  }

  function key(e: KeyboardEvent, place: PanelPlace) {
    const step: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const d = step[e.key];
    if (!d) return;
    e.preventDefault();
    const label = PANELS[place.id].label;
    const next = e.shiftKey ? resizePanel(panels, place.id, place.w + d[0], place.h + d[1]) : movePanel(panels, place.id, place.x + d[0], place.y + d[1]);
    if (!next) { setSaid(`${label} cannot go there: another panel is in the way.`); return; }
    const after = next.find((x) => x.id === place.id);
    if (after) setSaid(e.shiftKey ? `${label}: ${after.w} columns wide, ${after.h} rows high.` : `${label}: column ${after.x + 1}, row ${after.y + 1}.`);
    onChange(next);
  }

  const rows = usedRows(shown);
  return (
    <div>
      <div ref={grid} role="region" aria-label="Terminal panels" data-terminal-grid
        className="grid grid-cols-12 gap-space-2" style={{ gridAutoRows: `${ROW_REM}rem`, gridTemplateRows: `repeat(${rows}, ${ROW_REM}rem)` }}
        onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
        {/* The order of the props (not of a panel being dragged), so nothing is re-ordered in the page while a drag runs. */}
        {readingOrder(panels).map((base) => shown.find((x) => x.id === base.id) ?? base).map((place) => {
          const active = draft !== null && gesture.current?.id === place.id;
          const name = title(place);
          return (
            <section key={place.id} aria-label={name} data-panel={place.id}
              style={{ gridColumn: `${place.x + 1} / span ${place.w}`, gridRow: `${place.y + 1} / span ${place.h}` }}
              className={cx("relative flex min-h-0 min-w-0 flex-col overflow-hidden rounded-panel border bg-surface-1 shadow-card",
                active ? "border-brand ring-2 ring-brand" : "border-subtle")}>
              <header className="flex shrink-0 items-center gap-space-1 border-b border-subtle px-space-2">
                <div onPointerDown={(e) => start(e, place.id, "move")} data-panel-handle={place.id} title="Drag to move"
                  className="flex min-h-9 min-w-0 flex-1 cursor-grab touch-none select-none items-center gap-space-1 active:cursor-grabbing">
                  <GripVertical size={14} aria-hidden className="shrink-0 text-fg-muted" />
                  <h2 className="truncate text-t14 font-semibold text-fg">{name}</h2>
                </div>
                <button type="button" onKeyDown={(e) => key(e, place)} data-panel-keys={place.id}
                  aria-label={`Move or resize ${name}: arrow keys move it, Shift and the arrow keys resize it`} title="Move with the arrow keys (Shift to resize)"
                  className="inline-flex min-h-9 min-w-9 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                  <Move size={14} aria-hidden />
                </button>
                <button type="button" onClick={() => onClose(place.id)} aria-label={`Close ${name}`} title="Close this panel" data-panel-close={place.id}
                  className="inline-flex min-h-9 min-w-9 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                  <X size={16} aria-hidden />
                </button>
              </header>
              <div className="min-h-0 flex-1 overflow-auto p-space-3">{render(place)}</div>
              <div onPointerDown={(e) => start(e, place.id, "resize")} data-panel-resize={place.id} aria-hidden title="Drag to resize"
                className="absolute bottom-0 right-0 h-4 w-4 cursor-se-resize touch-none rounded-tl-control border-l border-t border-subtle bg-surface-2" />
            </section>
          );
        })}
      </div>
      <p className="sr-only" aria-live="polite">{said}</p>
    </div>
  );
}
