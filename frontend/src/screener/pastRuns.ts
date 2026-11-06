/**
 * FN2-g / FN2-i (screener, behind `ui_v2`): "Save matches as a watchlist" and "Past runs".
 * - Save: the matched symbols of a result become a new watchlist of the trader's own (the `watchlists` API; behind the
 *   `watchlists` flag). A watchlist is a list of names: nothing here places, changes or proposes an order.
 * - Past runs: the trader's own earlier runs (`GET /screener/runs`, newest first; never another member's), and one
 *   reopened (`GET /screener/runs/{id}`) with its stored results.
 */
import { request } from "../api/client";
import { MAX_ITEMS, NAME_MAX, type WatchItem } from "../chart/watchlistsV2";
import type { RunResult, UnreadCounts } from "./api";
import { savedAt } from "./versions";

/** One row of `GET /screener/runs`. */
export interface PastRun {
  run_id: number;
  screen_id: number | null;
  screen_name: string | null;
  base_tf: string;
  scanned: number;
  matched: number;
  first_matches: string[];
  created_at: string | null;
}
export interface PastRunsReply { runs: PastRun[]; disclaimer: string }

/** `GET /screener/runs/{id}`: one stored run with its results. */
export interface StoredRun extends UnreadCounts {
  run_id: number;
  screen_id: number | null;
  base_tf: string;
  data_source: string;
  scanned: number;
  matched: number;
  universe: string[];
  results: RunResult[];
  created_at: string | null;
  disclaimer: string;
}

export const PAST_RUNS_LIMIT = 20;

export const pastRunsApi = {
  list: (limit = PAST_RUNS_LIMIT) => request<PastRunsReply>(`/screener/runs?limit=${limit}`),
  get: (runId: number) => request<StoredRun>(`/screener/runs/${runId}`),
};

export function isPastRuns(value: unknown): value is PastRunsReply {
  const v = value as PastRunsReply;
  return !!v && typeof v === "object" && Array.isArray(v.runs)
    && v.runs.every((r) => !!r && typeof r.run_id === "number" && typeof r.matched === "number" && Array.isArray(r.first_matches));
}

/** The matched symbols of a result, in the board's order, each once (upper-case), at most the list's limit. */
export function matchedSymbols(results: readonly RunResult[] | null | undefined): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const r of results ?? []) {
    const s = r.symbol.trim().toUpperCase();
    if (!r.matched || !s || seen.has(s)) continue;
    seen.add(s);
    out.push(s);
  }
  return out.slice(0, MAX_ITEMS);
}

/** The rows of the new list: every symbol on NSE (the exchange the screener runs on). */
export const watchItems = (symbols: readonly string[], exchange = "NSE"): WatchItem[] => symbols.map((symbol) => ({ symbol, exchange }));

/** The name the save form starts with: the scan's name and "matches", within the name limit and without < or >. */
export function defaultListName(scanName: string): string {
  const base = scanName.replace(/[<>]/g, "").trim() || "Scan";
  const suffix = " matches";
  return `${base.slice(0, NAME_MAX - suffix.length).trim()}${suffix}`;
}

/** A name the server accepts (1-60 characters, no < or >); the reason in words when it does not. */
export function nameProblem(name: string): string | null {
  const n = name.trim();
  if (!n) return "Give the list a name.";
  if (n.length > NAME_MAX) return `Use at most ${NAME_MAX} characters.`;
  if (/[<>]/.test(n)) return "Leave out < and >.";
  return null;
}

/** One past run in words: "Trend up · Daily" or "Scan · Daily", then when it ran. */
export function runTitle(run: Pick<PastRun, "screen_name" | "base_tf">, tfWords: (tf: string) => string = (tf) => tf): string {
  return `${run.screen_name?.trim() || "Scan"} · ${tfWords(run.base_tf)}`;
}

export const runWhen = (run: Pick<PastRun, "created_at">): string => (run.created_at ? savedAt(run.created_at) : "");

/** "3 of 50 matched: A, B, C" - the first few matches, and how many more. */
export function runSummary(run: Pick<PastRun, "matched" | "scanned" | "first_matches">): string {
  const head = `${run.matched} of ${run.scanned} matched`;
  if (!run.first_matches.length) return head;
  const more = run.matched - run.first_matches.length;
  return `${head}: ${run.first_matches.join(", ")}${more > 0 ? ` and ${more} more` : ""}`;
}
