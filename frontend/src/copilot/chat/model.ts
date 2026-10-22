import { request } from "../../api/client";
import type { AnalysisChart, AnalysisTable } from "../../optionChain/analyses";

/**
 * Item 5 (owner's handover, the AI Copilot as a trader's brain), first slice: the chat-first Copilot page's data and its
 * pure rules. The server (backend app/ai/copilot_chat.py, app/ai/chat_routes.py) routes each question to one of eight
 * jobs, reads ATP's own tools and builds the answer; this module only fetches and arranges it. Nothing here can place,
 * change or cancel an order (ADR-0006).
 */

export type JobId = "brief" | "chart" | "trade_check" | "positions" | "review" | "strategy" | "option_chain" | "teacher";

export interface ChatJob {
  id: JobId; number: number; title: string; line: string; starter: string; built: boolean;
  page: { label: string; to: string } | null;
}
export interface AiState {
  ready: boolean; title: string | null; line: string | null; settings: string; reason: string | null;
  /** Settings redesign A (ai_one_key on only): the refusal code when nothing is connected, and whether the key is the user's own. */
  code?: string | null; own_key?: boolean;
}
export interface MarketLine { line: string | null; reason: string | null; as_of: string | null }
export interface ChatHome {
  top_line: string; ai: AiState; market_line: MarketLine; disclaimer: string; jobs: ChatJob[]; starters: string[];
}
export interface WorkedStep {
  tool: string; label: string; ok: boolean; as_of: string | null; source: string | null;
  numbers: { label: string; value: number }[]; note: string | null; args: Record<string, unknown>;
}
export type AnswerBlock =
  | { type: "table"; table: AnalysisTable }
  | { type: "chart"; title: string; chart: AnalysisChart };
export interface ChatAnswer {
  job: JobId | "refuse" | "help"; job_number: number | null; title: string;
  summary: string[]; answer: string; sections: { title: string; lines: string[] }[]; blocks: AnswerBlock[]; unknowns: string[];
  worked: WorkedStep[]; action: { label: string; to: string } | null; refused: "order" | "advice" | "tip" | null; starters: string[];
  concepts: { id: string; title: string }[]; notes: string[]; disclaimer: string; source: "rules" | "ai"; tool_calls: string[];
  ai?: AiState; note?: string;
}

/** One turn of the conversation on the page (kept in memory only). */
export interface Turn { id: number; question: string; answer: ChatAnswer | null; error: string | null }

export const chatApi = {
  home: () => request<ChatHome>("/ai/chat/home"),
  ask: (message: string) => request<ChatAnswer>("/ai/chat", { method: "POST", body: JSON.stringify({ message }) }),
};

export const MAX_QUESTION = 1000;
export const PHONE_MAX_WIDTH = 767;

/** The question as it is sent: trimmed, at most MAX_QUESTION characters; null when too short to ask. */
export function cleanQuestion(text: string): string | null {
  const q = text.replace(/\s+/g, " ").trim().slice(0, MAX_QUESTION);
  return q.length >= 2 ? q : null;
}

/** Where the question box sits: fixed at the bottom on a phone (as in a chat app), under the starters on a wider screen. */
export function composerPlacement(width: number): "bottom" | "inline" {
  return width <= PHONE_MAX_WIDTH ? "bottom" : "inline";
}

/** "Today's market in one line": the line, or the plain reason it is empty. */
export function marketLineText(home: ChatHome | null): { text: string; empty: boolean } {
  if (!home) return { text: "", empty: true };
  const m = home.market_line;
  return m.line ? { text: m.line, empty: false } : { text: m.reason ?? "Today's market line is not ready yet.", empty: true };
}

/** The no-key card is shown while no AI provider is set; the rules still answer. */
export function needsKey(home: ChatHome | null): boolean {
  return !!home && !home.ai.ready;
}

/** The "What I can do" cards: every job, a built one asks its starter, one not built yet says so and links today's page. */
export function jobCards(jobs: readonly ChatJob[]): { job: ChatJob; status: string; link: { label: string; to: string } | null }[] {
  return jobs.map((job) => ({
    job,
    status: job.built ? "Ready" : "Coming later",
    link: job.built ? null : job.page,
  }));
}

/** Appends a turn (the newest last) and keeps the last `keep` turns. */
export function withTurn(turns: readonly Turn[], turn: Turn, keep = 20): Turn[] {
  return [...turns, turn].slice(-keep);
}

/** Puts the answer (or the error) on its turn. */
export function settled(turns: readonly Turn[], id: number, answer: ChatAnswer | null, error: string | null): Turn[] {
  return turns.map((t) => (t.id === id ? { ...t, answer, error } : t));
}

/** "How I worked this out": one line per tool, its time stamp and the numbers it returned. */
export function workedLine(step: WorkedStep): string {
  if (!step.ok) return `${step.label}: no answer (${step.note ?? "not available"})`;
  const numbers = step.numbers.map((n) => `${n.label.replace(/_/g, " ")} ${n.value}`).join(", ");
  return `${step.label}${numbers ? `: ${numbers}` : ""}`;
}

/** A tool's time stamp as a short IST time ("21 Oct, 09:00 IST"), or null. */
export function stampText(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
  return `${d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" })} IST`;
}
