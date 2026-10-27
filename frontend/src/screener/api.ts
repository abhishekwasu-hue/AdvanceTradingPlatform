/** U5: the screener endpoints the funnel page uses (S1d API). Same auth, refresh and error handling as every request. */
import { request } from "../api/client";
import type { DataTierBadgeInfo } from "../types";
import type { FunnelResult, Registry, Stage } from "./model";
import type { LegendsLibrary } from "./legends";
import type { StagesResponse } from "./importStages";
import type { ConfluenceResponse, MirrorsResponse, WatchListsResponse, WatchResponse } from "./library";
import type { EtfResponse } from "./etf";
import type { Taxonomy } from "./taxonomy";
import type { BatchBudget } from "./universe";

export interface Problem { message: string; pos: number | null }
export interface ValidateResponse { ok: boolean; problems: Problem[]; text: string | null; plan: { cost: number; lookback: Record<string, number> } }
/** U8: `evidence_id` - the stored evidence pack of a matched symbol (flag evidence_charts; absent while it is off). */
export interface RunResult { symbol: string; matched: boolean; reason: string | null; evidence_id?: number | null }
export interface RunResponse {
  run_id: number; text: string; base_tf: string; data_source: string; scanned: number; matched: string[];
  results: RunResult[]; funnel: FunnelResult; disclaimer: string;
  /** BT1b (flag byob_data_tier): the data tier the run actually used, timed by its newest bars. */
  data_tier?: DataTierBadgeInfo;
  /** SCR-FIX A6: symbols outside the live session budget that were still read from history (absent when none). */
  session_held_back?: number;
}
/** A live preview: counts only (not stored, so no match list, removed symbols or per-symbol passes). */
export interface PreviewResponse {
  run_id: null; preview: true; text: string; base_tf: string; scanned: number; matched_count: number; funnel: FunnelResult;
  data_tier?: DataTierBadgeInfo;
}
export interface SavedScreen { id: number; name: string; text: string; base_tf: string }

/** LAB-18 (AMW-7): the builder blocks a save keeps with its version ({stages, texts, summaries}). */
export interface VersionBuilder { stages: Stage[]; texts: string[]; summaries: string[] }

/** LAB-18: one immutable server version of a saved scan, as the Versions dialog lists it (oldest first). */
export interface ServerVersion {
  version: number;          // the server's number for it (never reused)
  number: number;           // its place in the list, 1 = the oldest kept
  latest: boolean;
  name: string;
  base_tf: string;
  conditions: number;
  change: string[];         // what changed from the one before, in plain lines
  origin: "save" | "device";
  created_at: string | null;
}
export interface ServerVersionDetail extends ServerVersion {
  text: string;
  params: Record<string, number | string | boolean>;
  builder: VersionBuilder | null;
}
export interface VersionsResponse { screen_id: number; imported: boolean; max: number; versions: ServerVersion[] }
export interface ImportResponse { imported: number; skipped: number; already: boolean }

/** Handover 11j "idea -> scan": one recognised part of the idea (registry functions only). */
export interface IdeaPart {
  rule: string; phrase: string; condition: string; reads: string; assumptions: string[]; ok: boolean;
  problems: { kind: string; message: string; pos: number | null }[];
}
/** Handover 11j step 6: the plain feedback on one edit by chat ("drop the volume condition"). */
export interface IdeaEdit {
  edit: string; kind: "removed" | "added" | "replaced" | "timeframe" | "unchanged"; message: string; changed: boolean;
  removed: string[]; added: string[];
}
export interface IdeaResponse {
  ok: boolean; text: string | null; base_tf: string; stages: StagesResponse["stages"]; parts: IdeaPart[];
  not_supported: { phrase: string; why: string }[]; disclaimer: string;
  /** The feedback on each edit sent, in order (absent from servers before 11j step 6). */
  edits?: IdeaEdit[];
}

/** S3a alert rules (POST /api/alerts/rules) as the Screener's "Tell me when it matches" reads them: screen rules only. */
export interface ScanAlertRule {
  id: number; name: string; kind: "screen" | "instrument"; screen_id: number | null; base_tf: string; status: "active" | "paused";
  symbols?: string[]; fire_on?: "bar_close" | "intrabar";
}
export interface ScanAlertInput { name: string; screen_id: number; symbols: string[]; fire_on: "bar_close" | "intrabar" }

export const screenerApi = {
  registry: () => request<{ version: string; timeframes: string[]; entries: Registry }>("/screener/registry"),
  validate: (source: string, baseTf: string) =>
    request<ValidateResponse>("/screener/validate", { method: "POST", body: JSON.stringify({ source, base_tf: baseTf }) }),
  run: (source: string, baseTf: string, symbols: string[], exchange = "NSE") =>
    request<RunResponse>("/screener/run", { method: "POST", body: JSON.stringify({ source, base_tf: baseTf, symbols, exchange }) }),
  /** Owner order 23 Oct: one batch of a larger universe - its broker calls take tokens from the session's shared API
   * budget, and the answer says how many it made (`budget`), so the page paces the next batch. */
  runBatch: (source: string, baseTf: string, symbols: string[], exchange = "NSE") =>
    request<RunResponse & { budget?: BatchBudget }>("/screener/run", { method: "POST", body: JSON.stringify({ source, base_tf: baseTf, symbols, exchange, batch: true }) }),
  /** D2 live counts while editing: the same evaluation, not stored as a run. */
  preview: (source: string, baseTf: string, symbols: string[], exchange = "NSE") =>
    request<PreviewResponse>("/screener/run", { method: "POST", body: JSON.stringify({ source, base_tf: baseTf, symbols, exchange, preview: true }) }),
  /** U9 TX1: the screen library's taxonomy (flag `screener_gallery`; 503 while it is off - the page then shows no gallery). */
  taxonomy: () => request<Taxonomy>("/screener/taxonomy"),
  /** U7 LG1: the Legends cards (flag `screener_legends`; 503 while it is off - the gallery then shows no card text). */
  legends: () => request<LegendsLibrary>("/screener/legends"),
  /** U2 (flag `screener_builder_u2`; 503 while off): a screen -> the funnel's stages (top-level conjuncts). */
  stages: (body: { library_id?: string; source?: string; base_tf?: string; params?: Record<string, number | string | boolean> }) =>
    request<StagesResponse>("/screener/stages", { method: "POST", body: JSON.stringify(body) }),
  /** U9 TX2 (flag `screener_tx2`; 503 while it is off - the page then shows no library tools). */
  libraryMirrors: () => request<MirrorsResponse>("/screener/library/mirrors"),
  watchLists: () => request<WatchListsResponse>("/screener/library/watch-lists"),
  confluence: (screens: string[], symbols: string[], exchange = "NSE") =>
    request<ConfluenceResponse>("/screener/library/confluence", { method: "POST", body: JSON.stringify({ screens, symbols, exchange }) }),
  watchTrigger: (body: { list_id?: string; ready?: string; trigger?: string; base_tf?: string; symbols: string[]; exchange?: string }) =>
    request<WatchResponse>("/screener/library/watch-trigger", { method: "POST", body: JSON.stringify(body) }),
  /** S10 J (flag `screener_etf`; 503 while off - the page then shows no ETF button). */
  etf: (query: string) => request<EtfResponse>(`/screener/etf?${query}`),
  /** Handover 11j (flag `screener_copilot`; 404 / 503 while off): a plain idea -> a draft scan, part by part, as blocks. */
  idea: (text: string, baseTf: string, edits: string[] = []) =>
    request<IdeaResponse>("/screener/copilot/idea", { method: "POST",
      body: JSON.stringify(edits.length ? { text, base_tf: baseTf, edits } : { text, base_tf: baseTf }) }),
  /** LAB-18: with `builder` (flag `screener_copilot`), the save keeps the builder blocks with its new version. */
  save: (name: string, source: string, baseTf: string, id?: number, builder?: VersionBuilder) =>
    request<SavedScreen>(id ? `/screener/screens/${id}` : "/screener/screens",
      { method: id ? "PUT" : "POST", body: JSON.stringify(builder ? { name, source, base_tf: baseTf, builder } : { name, source, base_tf: baseTf }) }),
  /** LAB-18 (flag `screener_copilot`; 503 while off): a saved scan's versions, one version in full, and the one upload
   * of the history this device kept before the server kept it. */
  versions: (id: number) => request<VersionsResponse>(`/screener/screens/${id}/versions`),
  version: (id: number, version: number) => request<ServerVersionDetail>(`/screener/screens/${id}/versions/${version}`),
  importVersions: (id: number, versions: unknown[]) =>
    request<ImportResponse>(`/screener/screens/${id}/versions/import`, { method: "POST", body: JSON.stringify({ versions }) }),
};

/** 11j step 7 "Tell me when it matches": the existing alert rules (S3a / S4a bar-close engine), screen rules only.
 * A rule only notifies; it never places or changes an order. */
export const scanAlertsApi = {
  rules: () => request<ScanAlertRule[]>("/alerts/rules"),
  create: (body: ScanAlertInput) =>
    request<ScanAlertRule>("/alerts/rules", { method: "POST", body: JSON.stringify({
      name: body.name, kind: "screen", screen_id: body.screen_id, symbols: body.symbols, fire_on: body.fire_on, mode: "instant", priority: "normal" }) }),
  setStatus: (id: number, action: "pause" | "resume") => request<ScanAlertRule>(`/alerts/rules/${id}/${action}`, { method: "POST" }),
};
