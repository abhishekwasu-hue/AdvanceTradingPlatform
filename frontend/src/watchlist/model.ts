import type { SystemListsReply } from "../chart/systemLists";

/**
 * P1-8 (2) W-PR: the Watchlist page at /watchlist (v2 only, flag `watchlists`). The page shows the same watchlist panel
 * as Charts (src/chart/WatchlistPanel.tsx); this file only holds what the page itself decides.
 */

/** What the ready-made lists are right now: "filled" (at least one list has symbols), "empty" (the daily list update has
 * not filled them yet) or "unknown" (not read yet, or the read failed - then the page says nothing about them). */
export type ReadyMade = "filled" | "empty" | "unknown";

export function readyMadeState(reply: SystemListsReply | null | undefined): ReadyMade {
  if (!reply || !Array.isArray(reply.groups)) return "unknown";
  const lists = reply.groups.flatMap((g) => (Array.isArray(g?.lists) ? g.lists : []));
  return lists.some((l) => (l?.count ?? 0) > 0) ? "filled" : "empty";
}

/** The honest note while the ready-made lists are not filled yet (never sample rows in their place). */
export const NOT_FILLED_NOTE =
  "The ready-made lists (Indices, Index stocks, Sectors, F&O) fill after the daily list update. Your own lists work now.";

/** The page's one-line intro (what to do here). */
export const INTRO_LINE = "Tap a symbol to open its chart. Ready-made lists are read-only; copy one to make a list of your own.";
