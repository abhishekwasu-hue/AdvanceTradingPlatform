// N2-9: pure helpers for the Trade workspace - the saved layout (per viewer, browser storage), the keyboard map, the
// flatten-all targets and the data states. No order logic here: exits go through the existing Exit now.
import type { BoardPosition, PositionsBoard } from "../types";

export const LAYOUT_KEY = "atp.tradeWorkspace.v1";
export interface Layout { symbol: string; timeframe: string; panels: { chart: boolean; ticket: boolean; positions: boolean } }
export const DEFAULT_LAYOUT: Layout = { symbol: "", timeframe: "5min", panels: { chart: true, ticket: true, positions: true } };
export const TIMEFRAMES = ["1min", "5min", "15min", "30min", "60min", "day"];

type StorageLike = Pick<Storage, "getItem" | "setItem">;

function storage(): StorageLike | null {
  try { return typeof window !== "undefined" ? window.localStorage : null; } catch { return null; }
}

/** The viewer's saved layout, or the default when nothing (or nothing readable) is stored. */
export function loadLayout(s: StorageLike | null = storage()): Layout {
  try {
    const raw = s?.getItem(LAYOUT_KEY);
    if (!raw) return DEFAULT_LAYOUT;
    const v = JSON.parse(raw) as Partial<Layout>;
    return {
      symbol: typeof v.symbol === "string" ? v.symbol.slice(0, 40).toUpperCase() : "",
      timeframe: TIMEFRAMES.includes(String(v.timeframe)) ? String(v.timeframe) : DEFAULT_LAYOUT.timeframe,
      panels: { chart: v.panels?.chart !== false, ticket: v.panels?.ticket !== false, positions: v.panels?.positions !== false },
    };
  } catch { return DEFAULT_LAYOUT; }
}

export function saveLayout(layout: Layout, s: StorageLike | null = storage()): void {
  try { s?.setItem(LAYOUT_KEY, JSON.stringify(layout)); } catch { /* private mode, blocked storage: the layout is just not kept */ }
}

export type Shortcut = "ticket" | "chart" | "exit" | "flatten" | "help";

/** The action for a key press, or null. Never while typing in a field (except Escape-free keys are simply ignored). */
export function shortcutFor(e: { key: string; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean; altKey: boolean; targetTag?: string | null;
                                 editable?: boolean }): Shortcut | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  const tag = (e.targetTag ?? "").toUpperCase();
  if (e.editable || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return null;
  if (e.key === "?") return "help";
  if (e.shiftKey && (e.key === "F" || e.key === "f")) return "flatten";
  if (e.shiftKey) return null;
  if (e.key === "t") return "ticket";
  if (e.key === "c") return "chart";
  if (e.key === "x") return "exit";
  return null;
}

/** The positions "flatten all" exits: every one the caller can close with Exit now here (LIVE ones close in the broker
 * app; automatic ones have no button). Exit now on one leg closes its whole leg group, so a multi-leg structure is
 * one target (its first leg): the other legs would otherwise be reported as failures after the group has closed. */
export function flattenTargets(board: PositionsBoard | null): BoardPosition[] {
  const groups = new Set<string>();
  return (board?.positions ?? []).filter((p) => {
    if (p.exit !== "one_click") return false;
    if (!p.leg_group_id) return true;
    if (groups.has(p.leg_group_id)) return false;
    groups.add(p.leg_group_id);
    return true;
  });
}

/** Minutes since the last bar, or null; "stale" when more than three bars late while the market is open. */
export function staleness(lastBar: string | null | undefined, timeframe: string, now: Date, marketOpen: boolean): { minutes: number | null; stale: boolean } {
  if (!lastBar) return { minutes: null, stale: false };
  const t = Date.parse(lastBar);
  if (Number.isNaN(t)) return { minutes: null, stale: false };
  const minutes = Math.max(0, Math.round((now.getTime() - t) / 60000));
  const per = timeframe === "day" ? 24 * 60 : Number(timeframe.replace("min", "")) || 5;
  return { minutes, stale: marketOpen && timeframe !== "day" && minutes > 3 * per };
}
