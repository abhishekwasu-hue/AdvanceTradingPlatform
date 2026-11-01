import { request } from "../../api/client";

/**
 * M72 PR-11 (CP-11, flag `copilot_memory`): what the Copilot remembers about the user (backend app/ai/memory.py) - only
 * what he saves; ATP suggests from his own watchlists, trades and Copilot questions, each with where it came from.
 */
export type MemoryTimeframe = "15m" | "1h" | "1d" | "1w";
export interface CopilotMemory { instruments: string[]; timeframes: MemoryTimeframe[]; style: string | null }
export interface MemoryView {
  memory: CopilotMemory;
  suggested: { instruments: { symbol: string; count: number; from: string[] }[]; timeframes: { timeframe: MemoryTimeframe; count: number; from: string[] }[] };
  styles: Record<string, string>;
}
export const EMPTY_MEMORY: CopilotMemory = { instruments: [], timeframes: [], style: null };
export const TF_WORDS: Record<MemoryTimeframe, string> = { "15m": "15 minutes", "1h": "1 hour", "1d": "Daily", "1w": "Weekly" };
export const MAX_INSTRUMENTS = 12;

export const memoryApi = {
  read: () => request<MemoryView>("/copilot-memory"),
  save: (m: CopilotMemory) => request<MemoryView>("/copilot-memory", { method: "PUT", body: JSON.stringify(m) }),
  remove: () => request<{ deleted: number }>("/copilot-memory", { method: "DELETE" }),
};

/** Adds a suggestion he keeps (no duplicate, at most MAX_INSTRUMENTS / four timeframes). */
export function keep(m: CopilotMemory, what: { symbol?: string; timeframe?: MemoryTimeframe }): CopilotMemory {
  if (what.symbol && !m.instruments.includes(what.symbol) && m.instruments.length < MAX_INSTRUMENTS) return { ...m, instruments: [...m.instruments, what.symbol] };
  if (what.timeframe && !m.timeframes.includes(what.timeframe) && m.timeframes.length < 4) return { ...m, timeframes: [...m.timeframes, what.timeframe] };
  return m;
}

/** Drops one item he no longer wants remembered. */
export function drop(m: CopilotMemory, what: { symbol?: string; timeframe?: MemoryTimeframe }): CopilotMemory {
  return { ...m, instruments: m.instruments.filter((s) => s !== what.symbol), timeframes: m.timeframes.filter((t) => t !== what.timeframe) };
}
