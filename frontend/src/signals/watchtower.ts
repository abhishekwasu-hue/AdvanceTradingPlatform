import type { AiAction } from "../types";

/** UI2-3 (UI v2 §3 M7): with `ui_v2` on, the Watchtower lists the agent's other proposals and links entry approvals to
 * Signals (the same /ai/actions rows); with it off, everything stays in the Watchtower as today. A tiny module on
 * purpose: the Copilot route reads it, so it must not pull the Signals page's code into that chunk. */

type Row = Pick<AiAction, "action" | "expires_at"> & { owner_user_id?: number | null };

/** An entry proposal Signals lists for this user: their own or an organisation-wide one (the feed's own rule). Another
 * member's entry is not in their Signals, so it stays on the Watchtower (the organisation OWNER may decide it there). */
export function inSignals(a: Row, me: number | null | undefined): boolean {
  return a.action === "ENTRY" && (a.owner_user_id == null || (me != null && a.owner_user_id === me));
}

/** `entries`: the waiting entry proposals Signals counts under "Waiting for you" (not expired); `others`: what the
 * Watchtower keeps. An entry that is in Signals is never in `others`. */
export function watchtowerSplit<T extends Row>(pending: readonly T[], v2: boolean, me: number | null | undefined, now: Date = new Date()): { entries: T[]; others: T[] } {
  if (!v2) return { entries: [], others: [...pending] };
  const open = (a: T) => !a.expires_at || new Date(a.expires_at).getTime() > now.getTime();
  return { entries: pending.filter((a) => inSignals(a, me) && open(a)), others: pending.filter((a) => !inSignals(a, me)) };
}

/** Where the Watchtower's "Open Signals" goes: the first waiting entry proposal, opened (MOBILE_APP_PLAN §6.2). */
export function signalsLinkFor(entries: readonly Pick<AiAction, "id">[]): string {
  return entries.length > 0 ? `/signals?proposal=${entries[0].id}` : "/signals";
}
