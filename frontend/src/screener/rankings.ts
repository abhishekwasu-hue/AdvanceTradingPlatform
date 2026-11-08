/**
 * FN2-k (screener, behind `ui_v2` and the `screener_rankings` flag): "Rankings" - the platform's ranking systems and the
 * organisation's own (`GET /screener/rankings`), and one run that orders the trader's symbols 0-100 with every factor
 * shown (`POST /screener/rankings/run`). A rank is information about the symbols' data; nothing here places an order.
 */
import { request } from "../api/client";

export interface RankingSystem { id: number | null; name: string; description: string; platform: boolean; archived: boolean }
export interface RankingSystemsReply { platform: RankingSystem[]; systems: RankingSystem[]; disclaimer: string }

export interface RankComponent {
  path: string; name: string; depth: number; weight: number; score: number | null;
  factor?: string; value?: number | null; higher_is_better?: boolean;
}
export interface RankedRow { symbol: string; rank: number | null; composite: number | null; sector: string | null; as_of: string | null; components: RankComponent[] }
export interface RankingRun {
  system: { name: string; description: string };
  ranked: RankedRow[];
  not_ranked: Record<string, string>;
  scanned: number;
  data_source: string;
  disclaimer: string;
  method: string;
}

export const RANKING_RUN_MAX = 50;

export const rankingsApi = {
  list: () => request<RankingSystemsReply>("/screener/rankings"),
  run: (system: RankingSystem, symbols: string[], exchange = "NSE") =>
    request<RankingRun>("/screener/rankings/run", {
      method: "POST",
      body: JSON.stringify({ ...(system.platform || system.id == null ? { system: system.name } : { system_id: system.id }), symbols, exchange }),
    }),
};

export function isRankingSystems(value: unknown): value is RankingSystemsReply {
  const v = value as RankingSystemsReply;
  return !!v && typeof v === "object" && Array.isArray(v.platform) && Array.isArray(v.systems)
    && [...v.platform, ...v.systems].every((s) => !!s && typeof s.name === "string");
}

export function isRankingRun(value: unknown): value is RankingRun {
  const v = value as RankingRun;
  return !!v && typeof v === "object" && Array.isArray(v.ranked) && typeof v.scanned === "number"
    && v.ranked.every((r) => !!r && typeof r.symbol === "string" && Array.isArray(r.components));
}

/** The name without the platform prefix: "platform:Momentum" -> "Momentum". */
export const systemLabel = (s: Pick<RankingSystem, "name">): string => s.name.replace(/^platform:/i, "");

/** The system's own (non-archived) list after the platform's. */
export const allSystems = (r: RankingSystemsReply): RankingSystem[] => [...r.platform, ...r.systems.filter((s) => !s.archived)];

export type RankTone = "up" | "down" | "flat";
/** The colour of a 0-100 rank: the top third green, the bottom third red, the middle neutral (a position among the symbols ranked). */
export function rankTone(rank: number | null | undefined): RankTone {
  if (rank == null || !Number.isFinite(rank)) return "flat";
  return rank >= 100 * 2 / 3 ? "up" : rank <= 100 / 3 ? "down" : "flat";
}

/** The symbols that could not be ranked, each with its sentence, in symbol order. */
export const notRankedList = (r: Pick<RankingRun, "not_ranked">): { symbol: string; why: string }[] =>
  Object.entries(r.not_ranked ?? {}).map(([symbol, why]) => ({ symbol, why })).sort((a, b) => a.symbol.localeCompare(b.symbol));
