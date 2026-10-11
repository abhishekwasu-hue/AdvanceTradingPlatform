/**
 * H-C3c-2: the logic behind the research studies panel, kept apart from React so it is unit-tested.
 * A study is a background job (queued -> running -> done | failed | interrupted); the panel polls while any is active.
 */
import type { ResearchStatus, ResearchStudy } from "../../../types";

export const ACTIVE_STATUSES: readonly ResearchStatus[] = ["queued", "running"];
export const POLL_MS = 10_000;
/** A study still queued after this long: the research worker is probably not running (it is opt-in). */
export const WORKER_HINT_AFTER_MS = 3 * 60_000;

export const isActive = (s: { status: ResearchStatus }) => ACTIVE_STATUSES.includes(s.status);

/** Poll only while something can still change. */
export const shouldPoll = (studies: readonly { status: ResearchStatus }[]) => studies.some(isActive);

/** True when a queued study has waited longer than a worker would take to pick it up. */
export function workerLooksIdle(s: Pick<ResearchStudy, "status" | "created_at">, now: number): boolean {
  if (s.status !== "queued" || !s.created_at) return false;
  const created = Date.parse(s.created_at);
  return Number.isFinite(created) && now - created > WORKER_HINT_AFTER_MS;
}

/** 0..1 for a progress bar; a finished study is full whatever its draft count. */
export function progressFraction(s: Pick<ResearchStudy, "status" | "progress">): number {
  if (!isActive(s)) return 1;
  const { drafts_tried, max_drafts } = s.progress;
  return max_drafts > 0 ? Math.min(1, Math.max(0, drafts_tried / max_drafts)) : 0;
}

/** A probability shown as 0.00-1.00, or "n/a" (never a made-up number). */
export const prob = (v: number | null | undefined) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(2) : "n/a");

/** The few metrics worth a column, in a fixed order; missing ones are skipped, numbers rounded. */
// The keys research_loop._metrics writes (backend/app/ai/research_loop.py).
const METRIC_ORDER: [key: string, label: string][] = [["trades", "trades"], ["win_rate", "win %"], ["profit_factor", "PF"], ["max_drawdown", "max DD"], ["net_pnl", "net"]];
export function metricLine(m: Record<string, unknown>): string {
  return METRIC_ORDER.filter(([k]) => typeof m[k] === "number" && Number.isFinite(m[k] as number))
    .map(([k, label]) => `${label} ${Number.isInteger(m[k]) ? m[k] : (m[k] as number).toFixed(2)}`)
    .join(" · ");
}
