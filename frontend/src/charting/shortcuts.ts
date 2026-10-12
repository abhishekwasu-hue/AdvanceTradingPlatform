/**
 * CH5: the workspace's keyboard shortcuts, as a pure mapping from a key press to an action (tested without a browser).
 *
 * Every workspace shortcut uses Alt (Option on a Mac), so none collides with the drawing tools (Escape, Delete,
 * Ctrl/Cmd+Z / Y on the active chart) or the browser's own keys; "?" opens the list. The physical key (`code`) is read
 * for digits, because Option+1 types "¡" on a Mac. A key typed into a field (the symbol box) is never a shortcut.
 */
import { CHART_TYPES, type ChartType, type Grid } from "./workspace";

export type WorkspaceAction =
  | { type: "grid"; grid: Grid }
  | { type: "focus"; delta: number }
  | { type: "timeframe"; delta: number }
  | { type: "cycleGroup" }
  | { type: "cycleChartType" }
  | { type: "maximize" }
  | { type: "symbol" }
  | { type: "save" }
  | { type: "help" };

export interface ShortcutKey { key: string; code?: string; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean; target?: unknown }

/** The list shown by "?" (the same table the tests check `shortcutFor` against). */
export const SHORTCUTS: { keys: string; does: string }[] = [
  { keys: "Alt+1 / 2 / 4 / 6 / 8", does: "1, 2, 4, 6 or 8 charts" },
  { keys: "Alt+→ / Alt+←", does: "next / previous chart" },
  { keys: "Alt+↑ / Alt+↓", does: "longer / shorter timeframe on the active chart (and its interval-linked charts)" },
  { keys: "Alt+G", does: "link group of the active chart: none → A → B → C → D" },
  { keys: "Alt+T", does: "chart type of the active chart" },
  { keys: "Alt+Enter", does: "active chart full size / back to the grid" },
  { keys: "Alt+K", does: "type a symbol for the active chart" },
  { keys: "Alt+S", does: "save the layout" },
  { keys: "?", does: "this list" },
];

export function typing(target: unknown): boolean {
  const t = target as { tagName?: string; isContentEditable?: boolean } | null;
  return !!t && (t.isContentEditable === true || ["INPUT", "TEXTAREA", "SELECT"].includes(String(t.tagName ?? "").toUpperCase()));
}

const GRID_DIGITS: Record<string, Grid> = { Digit1: 1, Digit2: 2, Digit4: 4, Digit6: 6, Digit8: 8, Numpad1: 1, Numpad2: 2, Numpad4: 4, Numpad6: 6, Numpad8: 8 };

export function shortcutFor(e: ShortcutKey): WorkspaceAction | null {
  if (typing(e.target) || e.ctrlKey || e.metaKey) return null;
  if (!e.altKey) return e.key === "?" ? { type: "help" } : null;
  const grid = e.code ? GRID_DIGITS[e.code] : undefined;
  if (grid) return { type: "grid", grid };
  if (!e.code && ["1", "2", "4", "6", "8"].includes(e.key)) return { type: "grid", grid: Number(e.key) as Grid };
  switch (e.key) {
    case "ArrowRight": return { type: "focus", delta: 1 };
    case "ArrowLeft": return { type: "focus", delta: -1 };
    case "ArrowUp": return { type: "timeframe", delta: 1 };
    case "ArrowDown": return { type: "timeframe", delta: -1 };
    case "Enter": return { type: "maximize" };
  }
  switch (e.code ?? `Key${e.key.toUpperCase()}`) {
    case "KeyG": return { type: "cycleGroup" };
    case "KeyT": return { type: "cycleChartType" };
    case "KeyK": return { type: "symbol" };
    case "KeyS": return { type: "save" };
  }
  return null;
}

/** candles -> hollow -> Heikin Ashi -> Renko -> range -> line -> area -> candles */
export function nextChartType(t: ChartType): ChartType {
  return CHART_TYPES[(CHART_TYPES.indexOf(t) + 1) % CHART_TYPES.length];
}
