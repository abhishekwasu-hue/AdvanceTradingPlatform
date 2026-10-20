import type { AiAction } from "../types";

/** UI2-3 (UI v2 §3 M7): with `ui_v2` on, the Watchtower lists the agent's other proposals and links entry approvals to
 * Signals (the same /ai/actions rows); with it off, everything stays in the Watchtower as today. A tiny module on
 * purpose: the Copilot route reads it, so it must not pull the Signals page's code into that chunk. */
export function watchtowerSplit<T extends Pick<AiAction, "action">>(pending: readonly T[], v2: boolean): { entries: T[]; others: T[] } {
  if (!v2) return { entries: [], others: [...pending] };
  return { entries: pending.filter((a) => a.action === "ENTRY"), others: pending.filter((a) => a.action !== "ENTRY") };
}

/** Where the Watchtower's "Open Signals" goes: the first waiting entry proposal, opened (MOBILE_APP_PLAN §6.2). */
export function signalsLinkFor(entries: readonly Pick<AiAction, "id">[]): string {
  return entries.length > 0 ? `/signals?proposal=${entries[0].id}` : "/signals";
}
