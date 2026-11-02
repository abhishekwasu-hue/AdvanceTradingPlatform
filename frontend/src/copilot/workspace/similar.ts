/**
 * M76 MP-14 L-1 (owner order M76 L-1; flag `copilot_workspace`): "Similar past setups" beside the chart - his own closed
 * trades taken at the same kind of area (source, the area's timeframe, side and state) as the area the chart's price is
 * inside now (`GET /api/charts/workspace/similar`, backend app/charts/workspace_recall.py). The area context of a trade is
 * recorded by the SERVER at entry (`POST /api/charts/workspace/entry-context`, after a fill from the workspace); a trade
 * without one never matches. Pure parts and the reads; outcomes in the setup recall's own words.
 */
import { request } from "../../api/client";
import type { Annotations } from "./annotations";

export interface SimilarMatch {
  trade_id: number; symbol: string | null; entered_at: string | null; closed_at: string | null; mode: string;
  r: number | null; result: "gain" | "loss" | "flat" | null; outcome: string; how_closed: string;
}
export interface SimilarSide {
  side: "LONG" | "SHORT"; side_words: string; count: number; enough: boolean; line: string; wins: number | null; losses: number | null;
  avg_r: number | null; matches: SimilarMatch[];
}
export interface SimilarRead { context: Record<string, string | null>; as_of: string; count: number; sides: SimilarSide[]; line: string | null; blocks: false }
export interface SimilarKey { source: "atp" | "engine"; tf: string; side: "buyer" | "seller"; state: string | null }

/** The match key from the chart's reading: the area its last close is inside, or null (inside no area). Pure. */
export function similarKey(a: Annotations | null): SimilarKey | null {
  const inside = a?.next_areas.inside;
  if (!a || !inside || !/^[0-9a-zA-Z]{1,6}$/.test(inside.timeframe)) return null;
  return { source: a.source, tf: inside.timeframe, side: inside.side, state: inside.state && /^[a-z_]{1,24}$/.test(inside.state) ? inside.state : null };
}

export const similarApi = {
  read: (k: SimilarKey) => {
    const q = new URLSearchParams({ source: k.source, tf: k.tf, side: k.side });
    if (k.state) q.set("state", k.state);
    return request<SimilarRead>(`/charts/workspace/similar?${q.toString()}`);
  },
  /** After a fill from the workspace: the server reads the open chart as of the entry and records the area (if any). */
  record: (tradeIds: number[], instrument: string, tf: string) =>
    request<{ stored: number }>("/charts/workspace/entry-context", { method: "POST", body: JSON.stringify({ trade_ids: tradeIds.slice(0, 20), instrument, tf }) }),
};
export type SimilarApi = typeof similarApi;

/** The key in words: "Buyer area · 1h · tested". Pure. */
export function keyWords(k: SimilarKey): string {
  return [k.side === "buyer" ? "Buyer area" : "Seller area", k.tf, k.state].filter(Boolean).join(" · ");
}
