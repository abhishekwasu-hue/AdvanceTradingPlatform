/** U5: the screener endpoints the funnel page uses (S1d API). Same auth, refresh and error handling as every request. */
import { request } from "../api/client";
import type { DataTierBadgeInfo } from "../types";
import type { FunnelResult, Registry } from "./model";
import type { LegendsLibrary } from "./legends";
import type { StagesResponse } from "./importStages";
import type { Taxonomy } from "./taxonomy";

export interface Problem { message: string; pos: number | null }
export interface ValidateResponse { ok: boolean; problems: Problem[]; text: string | null; plan: { cost: number; lookback: Record<string, number> } }
/** U8: `evidence_id` - the stored evidence pack of a matched symbol (flag evidence_charts; absent while it is off). */
export interface RunResult { symbol: string; matched: boolean; reason: string | null; evidence_id?: number | null }
export interface RunResponse {
  run_id: number; text: string; base_tf: string; data_source: string; scanned: number; matched: string[];
  results: RunResult[]; funnel: FunnelResult; disclaimer: string;
  /** BT1b (flag byob_data_tier): the data tier the run actually used, timed by its newest bars. */
  data_tier?: DataTierBadgeInfo;
}
/** A live preview: counts only (not stored, so no match list, removed symbols or per-symbol passes). */
export interface PreviewResponse {
  run_id: null; preview: true; text: string; base_tf: string; scanned: number; matched_count: number; funnel: FunnelResult;
  data_tier?: DataTierBadgeInfo;
}
export interface SavedScreen { id: number; name: string; text: string; base_tf: string }

export const screenerApi = {
  registry: () => request<{ version: string; timeframes: string[]; entries: Registry }>("/screener/registry"),
  validate: (source: string, baseTf: string) =>
    request<ValidateResponse>("/screener/validate", { method: "POST", body: JSON.stringify({ source, base_tf: baseTf }) }),
  run: (source: string, baseTf: string, symbols: string[], exchange = "NSE") =>
    request<RunResponse>("/screener/run", { method: "POST", body: JSON.stringify({ source, base_tf: baseTf, symbols, exchange }) }),
  /** D2 live counts while editing: the same evaluation, not stored as a run. */
  preview: (source: string, baseTf: string, symbols: string[], exchange = "NSE") =>
    request<PreviewResponse>("/screener/run", { method: "POST", body: JSON.stringify({ source, base_tf: baseTf, symbols, exchange, preview: true }) }),
  /** U9 TX1: the screen library's taxonomy (flag `screener_gallery`; 503 while it is off - the page then shows no gallery). */
  taxonomy: () => request<Taxonomy>("/screener/taxonomy"),
  /** U7 LG1: the Legends cards (flag `screener_legends`; 503 while it is off - the gallery then shows no card text). */
  legends: () => request<LegendsLibrary>("/screener/legends"),
  /** U2 (flag `screener_builder_u2`; 503 while off): a screen -> the funnel's stages (top-level conjuncts). */
  stages: (body: { library_id?: string; source?: string; base_tf?: string }) =>
    request<StagesResponse>("/screener/stages", { method: "POST", body: JSON.stringify(body) }),
  save: (name: string, source: string, baseTf: string, id?: number) =>
    request<SavedScreen>(id ? `/screener/screens/${id}` : "/screener/screens",
      { method: id ? "PUT" : "POST", body: JSON.stringify({ name, source, base_tf: baseTf }) }),
};
