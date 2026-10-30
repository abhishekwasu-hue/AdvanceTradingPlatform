import { request } from "../api/client";
import { timeLeft } from "../proposals/snooze";
import type { AiAction } from "../types";
import { formatINR, formatNumber } from "../ui/format";

/**
 * UI2-3 (UI v2 proposal §7): the Signals inbox's data - `GET /api/signals/feed` (read-only; app/signals_feed) - and the
 * pure rules the page shows it by. Approve, skip and snooze never go through here: they use the existing
 * `/api/ai/actions/{id}/approve|reject|snooze` (with their step-up and owner rules), so nothing on this page can place
 * an order on its own (ADR-0006).
 */

export type FeedTab = "waiting" | "today" | "history";
export type FeedSource = "all" | "autopilot" | "scan" | "copilot" | "library";
export type SignalKind = "autopilot" | "scan" | "copilot" | "library";
export type SignalStatus = "waiting" | "snoozed" | "taken" | "skipped" | "expired" | "not_placed" | "info";

/** One row, the same shape for every source (proposal, scan setup or match, Autopilot fill, generated signal). */
export interface SignalFeedItem {
  /** "<source>:<id>" - action, intent, alert, trade or history. */
  id: string;
  at: string;
  symbol: string;
  exchange: string;
  direction: "LONG" | "SHORT" | null;
  setup: string;
  entry: number | null;
  stop: number | null;
  target: number | null;
  rr: number | null;
  source: { kind: SignalKind; name: string };
  mode: "PAPER" | "LIVE" | null;
  status: SignalStatus;
  expires_at: string | null;
  snoozed_until: string | null;
  reasons: string[];
  /** The entry proposal behind the row: approve / skip / snooze go to /api/ai/actions/{action_id}/... */
  action_id: number | null;
  /** Why it ended the way it did (the server's own words), when it did. */
  outcome: string | null;
}

export interface SignalFeed {
  tab: FeedTab;
  source: FeedSource;
  symbol: string | null;
  items: SignalFeedItem[];
  next_cursor: string | null;
  /** The tab counts, on the first page only (null on the next pages). */
  counts: FeedCounts | null;
  as_of: string;
  since: string | null;
  sources: Record<string, boolean>;
}

/** `capped`: the server stopped counting at its read limit, so there are at least that many ("2000+"). */
export interface FeedCounts { waiting: number; today: number; capped?: { waiting: boolean; today: boolean } }

/** A tab's count as shown: "12", or "2000+" when the server stopped counting. */
export function countLabel(counts: FeedCounts | null | undefined, tab: FeedTab): string | undefined {
  if (!counts || tab === "history") return undefined;
  const n = tab === "waiting" ? counts.waiting : counts.today;
  return counts.capped?.[tab] ? `${n}+` : String(n);
}

/** Who may approve, skip or snooze: the server's require_trader - the trading roles, and SUPER_ADMIN, which passes every
 * role check. A viewer reads the inbox only. */
export function canTradeRole(role: string | null | undefined): boolean {
  return role === "OWNER" || role === "USER" || role === "STRATEGY_CREATOR" || role === "SUPER_ADMIN";
}

export interface FeedParams { tab: FeedTab; source?: FeedSource; symbol?: string | null; cursor?: string | null; limit?: number }

export function feedQuery({ tab, source = "all", symbol, cursor, limit }: FeedParams): string {
  const q = new URLSearchParams({ tab });
  if (source !== "all") q.set("source", source);
  if (symbol?.trim()) q.set("symbol", symbol.trim().toUpperCase());
  if (cursor) q.set("cursor", cursor);
  if (limit) q.set("limit", String(limit));
  return q.toString();
}

export function fetchFeed(params: FeedParams): Promise<SignalFeed> {
  return request<SignalFeed>(`/signals/feed?${feedQuery(params)}`);
}

export const TABS: readonly { id: FeedTab; label: string; short: string }[] = [
  { id: "waiting", label: "Waiting for you", short: "Waiting" },
  { id: "today", label: "Today", short: "Today" },
  { id: "history", label: "History", short: "History" },
];

export const SOURCES: readonly { id: FeedSource; label: string }[] = [
  { id: "all", label: "All sources" },
  { id: "autopilot", label: "Trading bots" },
  { id: "scan", label: "Scans" },
  { id: "copilot", label: "Copilot" },
  { id: "library", label: "Signal generator" },      // REV-1 M3: its rows had no filter of their own (All sources only)
];

const KIND_LABEL: Record<SignalKind, string> = { autopilot: "Trading bot", scan: "Scan", copilot: "Copilot", library: "Signal generator" };

/** An internal bot number ("Deployment #4"): never shown (UX audit P09); the setup line already names the strategy. */
const INTERNAL_NAME = /^deployment\s*#?\s*\d+$/i;

/** "Trading bot", "Scan · Gap scan", "Signal generator" - the caption under the setup. */
export function sourceCaption(item: Pick<SignalFeedItem, "source">): string {
  const kind = KIND_LABEL[item.source.kind] ?? item.source.kind;
  const raw = item.source.name?.trim();
  const name = raw && INTERNAL_NAME.test(raw) ? "" : raw;
  // "Scan setup" under Scan says nothing new: the kind alone.
  return name && !name.toLowerCase().startsWith(kind.toLowerCase()) ? `${kind} · ${name}` : kind;
}

/** The phone default is "Waiting for you"; the desktop default is "Today" with the first waiting signal selected. */
export function defaultTab(wide: boolean): FeedTab {
  return wide ? "today" : "waiting";
}

/** A waiting row whose expiry has passed on this screen is expired too (the server says so on its next read). */
export function effectiveStatus(item: Pick<SignalFeedItem, "status" | "expires_at">, now: Date): SignalStatus {
  if ((item.status === "waiting" || item.status === "snoozed") && item.expires_at && !timeLeft(item.expires_at, now)) return "expired";
  return item.status;
}

/** True when the trader can decide this row now: a waiting entry proposal that has not expired. */
export function canDecide(item: SignalFeedItem, now: Date): boolean {
  return item.action_id !== null && effectiveStatus(item, now) === "waiting";
}

/** "8 min", "1 h 5 min"; null when expired or without an expiry. */
export function timeLeftLabel(expiresAt: string | null, now: Date): string | null {
  const left = timeLeft(expiresAt, now);
  if (!left) return null;
  if (left.hours === 0) return `${Math.max(left.minutes, 0) || "<1"} min`;
  return left.minutes ? `${left.hours} h ${left.minutes} min` : `${left.hours} h`;
}

/** The status chip's words. Waiting carries the time left ("Waiting · 8 min"). */
export function statusLabel(item: Pick<SignalFeedItem, "status" | "expires_at" | "snoozed_until">, now: Date): string {
  const status = effectiveStatus(item, now);
  switch (status) {
    case "waiting": {
      const left = timeLeftLabel(item.expires_at, now);
      return left ? `Waiting · ${left}` : "Waiting";
    }
    case "snoozed": return "Snoozed";
    case "taken": return "Taken";
    case "skipped": return "Skipped";
    case "expired": return "Expired";
    case "not_placed": return "Not placed";
    default: return "For info";
  }
}

export function directionLabel(direction: SignalFeedItem["direction"]): string | null {
  return direction === "LONG" ? "Long" : direction === "SHORT" ? "Short" : null;
}

/** A signed level distance from the entry ("+140.00", "−70.00"), or null. */
export function levelDelta(level: number | null, entry: number | null): number | null {
  if (level === null || entry === null || !Number.isFinite(level) || !Number.isFinite(entry)) return null;
  return Math.round((level - entry) * 100) / 100;
}

/** The sentences under "Fits your risk limits", from the existing risk pre-check on the proposal (Z5 / P1-d,
 * `risk_check`; flag risk_by_default) and the portfolio line (N1 F3). `warn` marks a line that would refuse it. */
export function riskSentences(a: Pick<AiAction, "risk_check" | "portfolio_impact"> | null | undefined): { text: string; warn: boolean }[] {
  const out: { text: string; warn: boolean }[] = [];
  const rc = a?.risk_check;
  if (rc) {
    const s = rc.sizing;
    out.push(s.within_cap
      ? { text: `Most you can lose (${formatINR(s.risk_amount)}) is within your ${formatINR(s.cap)} per-trade limit.`, warn: false }
      : { text: `Most you can lose (${formatINR(s.risk_amount)}) is over your ${formatINR(s.cap)} per-trade limit.`, warn: true });
    if (rc.costs) {
      const rr = rc.costs.net_reward_risk;
      out.push({ text: `Costs about ${formatINR(rc.costs.total)}${rr != null ? `; reward to risk after costs is ${formatNumber(rr, 2)}` : ""}.`, warn: false });
    }
    for (const r of [...rc.refusals, ...rc.errors]) out.push({ text: r, warn: true });
    for (const w of rc.warnings) out.push({ text: w, warn: true });
  }
  if (a?.portfolio_impact?.line) out.push({ text: a.portfolio_impact.line, warn: false });
  return out;
}

/** The size the trader's risk cap allows, as lots, and the most it can lose - from the same pre-check. */
export function sizeAndLoss(a: Pick<AiAction, "risk_check"> | null | undefined): { quantity: number; lotSize: number; maxLoss: number } | null {
  const s = a?.risk_check?.sizing;
  if (!s) return null;
  return { quantity: s.suggested_quantity, lotSize: s.lot_size, maxLoss: s.risk_amount };
}

/** The row a `?proposal=<id>` link names (a push or Telegram link, MOBILE_APP_PLAN §6.2), if it is on the page. */
export function itemForProposal(items: readonly SignalFeedItem[], proposal: string | null): SignalFeedItem | null {
  const id = Number(proposal);
  if (!proposal || !Number.isInteger(id) || id <= 0) return null;
  return items.find((i) => i.action_id === id) ?? null;
}

/** The message after a decision, in plain words. A LIVE proposal's approval is told in the server's own words (it never
 * claims a paper trade). */
export function decisionMessage(kind: "approve" | "skip" | "snooze", item: Pick<SignalFeedItem, "symbol"> & Partial<Pick<SignalFeedItem, "mode">>,
  result?: Pick<AiAction, "status" | "result" | "snoozed_until"> | null): string {
  if (kind === "skip") return `Skipped ${item.symbol}. Nothing was placed.`;
  if (kind === "snooze") return `Snoozed ${item.symbol}. It comes back here before it expires.`;
  if (result && result.status !== "EXECUTED" && result.status !== "APPROVED") {
    return `Not placed for ${item.symbol}: ${result.result ?? "the price moved too far"}. Nothing was bought or sold.`;
  }
  if (item.mode === "LIVE") return result?.result ? `Approved ${item.symbol}. ${result.result}` : `Approved ${item.symbol}.`;
  return `Approved. A paper trade for ${item.symbol} was placed at the price then.`;
}

/** What a failed decision says. 409 / 404: decided or expired meanwhile (the list reloads). 403: this login may not
 * decide it. Anything else: the server's readable reason, or a plain retry sentence - never a raw error. */
export function decisionFailure(status: number | null, detail = ""): { text: string; stale: boolean } {
  if (status === 409 || status === 404) return { text: "This signal was already decided or has expired. Nothing was placed.", stale: true };
  if (status === 403) return { text: "You can't decide this signal: your role can view signals but not trade, or it is another member's. Nothing was placed.", stale: false };
  const why = detail.trim().replace(/\.$/, "");
  return { text: why ? `Not done: ${why}. Nothing was placed.` : "That didn't go through. Nothing was placed. Try again in a moment.", stale: false };
}

/** The sentence for a `?proposal=<id>` link whose proposal is not waiting in this list - never a silent fallback.
 * `other`: the proposal is waiting but is another member's (it stays on the Watchtower). */
export function missingProposalMessage(proposal: string, other: boolean): string {
  return other
    ? `Signal #${proposal} is another member's, so it is not in your Signals. It stays on the Watchtower.`
    : `Signal #${proposal} is not waiting for you any more: it was decided, snoozed or has expired. Find it under Today or History.`;
}
