/**
 * SCR-FIX B1 / B4: one count source for every place a scan's answer shows (the Conditions card, Results, the phone bar).
 * A symbol the scan could not read (the broker refused, the login ended, too little history) is never counted as
 * "did not match": the counts are "N matched · M could not be read", with the grouped reason in one line and ONE action.
 * The totals come from the funnel the server sent (`universe` symbols, `with_data` of them read), never from the
 * page's own list length - so the card and the bar always say the same.
 */
import { ApiError } from "../api/errors";
import type { ReadAction, RunResult, UnreadReason } from "./api";
import type { FunnelResult } from "./model";

export const ACTION_LABEL: Record<ReadAction, string> = {
  login: "Log in", connect: "Connect broker", retry: "Retry", timeframe: "Try another timeframe",
};
/** A tie between two groups of the same size: the action that fixes most first (as the server). */
const PRIORITY: Record<ReadAction, number> = { login: 0, connect: 1, timeframe: 2, retry: 3 };

export interface ReadTally {
  /** symbols the scan was asked for */
  total: number;
  /** of them, read and checked */
  withData: number;
  /** could not be read or checked */
  unread: number;
  matched: number;
  /** read, checked and did not match */
  notMatched: number;
}

/** The counts of one answer, from its funnel and its match count. */
export function tally(funnel: FunnelResult, matched: number): ReadTally {
  const total = Math.max(0, funnel.universe);
  const withData = Math.min(total, Math.max(0, funnel.with_data));
  return { total, withData, unread: total - withData, matched, notMatched: Math.max(0, withData - matched) };
}

/** "4 matched · 46 could not be read" (the second part only when some could not be read). */
export function countLine(t: ReadTally): string {
  return t.unread > 0 ? `${t.matched} matched · ${t.unread} could not be read` : `${t.matched} matched`;
}

function isAction(v: unknown): v is ReadAction {
  return v === "login" || v === "connect" || v === "retry" || v === "timeframe";
}

/** The rows that could not be read or checked: a reason, never matched. */
export function unreadRows(results: readonly RunResult[]): RunResult[] {
  return results.filter((r) => !r.matched && !!r.reason);
}

/** The grouped reason in one line + its one action: from the rows when there are any (a long list's batches merged),
 * else the server's own summary (a preview has no rows). */
export function unreadReason(answer: { results?: readonly RunResult[]; unread_reason?: UnreadReason }): UnreadReason | null {
  const rows = answer.results ? unreadRows(answer.results) : [];
  if (!rows.length) return answer.results && answer.results.length ? null : answer.unread_reason ?? null;
  const groups = new Map<string, { text: string; action: ReadAction; count: number; texts: Map<string, number> }>();
  for (const r of rows) {
    const code = r.code ?? "not_read";
    const g = groups.get(code) ?? { text: r.reason ?? "", action: isAction(r.action) ? r.action : "retry", count: 0, texts: new Map() };
    g.count += 1;
    g.texts.set(r.reason ?? "", (g.texts.get(r.reason ?? "") ?? 0) + 1);
    groups.set(code, g);
  }
  const [code, g] = [...groups.entries()].sort(([ca, a], [cb, b]) => b.count - a.count || PRIORITY[a.action] - PRIORITY[b.action] || ca.localeCompare(cb))[0];
  const text = [...g.texts.entries()].sort((a, b) => b[1] - a[1])[0][0];
  return { code, text, action: g.action, count: g.count };
}

/** SCR-FIX B2: a run where not one symbol could be read answers with a run-level error ({message, code: "nothing_read",
 * action}) - its sentence and action, or null for any other error. */
export function nothingRead(e: unknown): { text: string; action: ReadAction } | null {
  if (!(e instanceof ApiError) || !e.body) return null;
  try {
    const detail = (JSON.parse(e.body) as { detail?: { code?: unknown; message?: unknown; action?: unknown } }).detail;
    if (!detail || detail.code !== "nothing_read" || typeof detail.message !== "string") return null;
    return { text: detail.message, action: isAction(detail.action) ? detail.action : "retry" };
  } catch {
    return null;
  }
}
