/** U5: the screener endpoints the funnel page uses (S1d API). Same auth, refresh and error handling as every request. */
import { request } from "../api/client";
import type { FunnelResult, Registry } from "./model";

export interface Problem { message: string; pos: number | null }
export interface ValidateResponse { ok: boolean; problems: Problem[]; text: string | null; plan: { cost: number; lookback: Record<string, number> } }
export interface RunResult { symbol: string; matched: boolean; reason: string | null }
export interface RunResponse {
  run_id: number; text: string; base_tf: string; data_source: string; scanned: number; matched: string[];
  results: RunResult[]; funnel: FunnelResult; disclaimer: string;
}
export interface SavedScreen { id: number; name: string; text: string; base_tf: string }

export const screenerApi = {
  registry: () => request<{ version: string; timeframes: string[]; entries: Registry }>("/screener/registry"),
  validate: (source: string, baseTf: string) =>
    request<ValidateResponse>("/screener/validate", { method: "POST", body: JSON.stringify({ source, base_tf: baseTf }) }),
  run: (source: string, baseTf: string, symbols: string[], exchange = "NSE") =>
    request<RunResponse>("/screener/run", { method: "POST", body: JSON.stringify({ source, base_tf: baseTf, symbols, exchange }) }),
  save: (name: string, source: string, baseTf: string, id?: number) =>
    request<SavedScreen>(id ? `/screener/screens/${id}` : "/screener/screens",
      { method: id ? "PUT" : "POST", body: JSON.stringify({ name, source, base_tf: baseTf }) }),
};
