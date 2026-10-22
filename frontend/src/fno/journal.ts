/**
 * F&O workspace 8b, slice 3 (owner order ATP_SESSION_FNO_WORKSPACE.md, section 8b-k): the auto journal's pure parts
 * (tested in journal.test.tsx). A closed PAPER F&O trade (or legs placed together) is a journal draft made of facts only -
 * what was held, the fills, the P&L, how it was closed as recorded - plus the structure words of his OWN signal it came
 * from (the server keeps only the lines in the signal language). The user adds his own notes with the existing notes
 * field; nothing here changes a trade, an order or a setting, and nothing here tells him what to do.
 */
import { request } from "../api/client";
import { humanise } from "../dashboard/model";
import { formatDateTimeIST, formatNumber, formatPrice, formatSignedINR } from "../ui/format";

export interface DraftLeg {
  trade_id: number; symbol: string; side: "long" | "short"; quantity: number; lot_size: number | null; entry_price: number; entry_at: string | null;
  exit_price: number | null; exit_at: string | null; exit_reason: string | null; pnl: number | null; charges: number;
}

export interface JournalDraft {
  key: string;
  kind: "basket" | "structure" | "trade";
  title: string;
  underlying: string;
  trade_ids: number[];
  opened_at: string | null;
  closed_at: string | null;
  legs: DraftLeg[];
  pnl: number | null;
  charges: number;
  placed_by: "you" | "paper_bot";
  strategy: string | null;
  signal: { setup: string | null; words: string[] } | null;
  notes: string | null;
  status: "draft" | "noted";
}

export interface JournalDrafts { mode: "PAPER"; days: number; items: JournalDraft[] }

export const journalApi = {
  drafts: () => request<JournalDrafts>("/paper/fno/journal/drafts"),
  /** The existing notes field of each trade (`PATCH /api/trades/{id}/journal`): his own words, nothing else changes. */
  saveNotes: (tradeIds: readonly number[], notes: string) =>
    Promise.all(tradeIds.map((id) => request(`/trades/${id}/journal`, { method: "PATCH", body: JSON.stringify({ notes }) }))),
};
export type JournalApi = typeof journalApi;

const qty = (v: number) => formatNumber(v, Number.isInteger(v) ? 0 : 2);

/** "Short NIFTY24O2822800CE · 75 units · in 120.50 · out 80.00 · ₹3,037.50". Facts only. */
export function legLine(l: DraftLeg): string {
  const lots = l.lot_size && l.lot_size > 0 && l.quantity % l.lot_size === 0 ? ` (${qty(l.quantity / l.lot_size)} lot${l.quantity / l.lot_size === 1 ? "" : "s"})` : "";
  const out = l.exit_price === null ? "" : ` · out ${formatPrice(l.exit_price)}`;
  const pnl = l.pnl === null ? "" : ` · ${formatSignedINR(l.pnl)}`;
  return `${l.side === "short" ? "Short" : "Long"} ${l.symbol} · ${qty(l.quantity)} units${lots} · in ${formatPrice(l.entry_price)}${out}${pnl}`;
}

/** How it was closed, as recorded ("exit now", "expiry"), one per kind; null when nothing was recorded. */
export function closedHow(d: Pick<JournalDraft, "legs">): string | null {
  const reasons = [...new Set(d.legs.map((l) => l.exit_reason).filter((r): r is string => !!r).map((r) => humanise(r.toLowerCase())))];
  return reasons.length ? reasons.join(", ") : null;
}

/** "Opened 21 Oct, 10:15 · closed 21 Oct, 14:20". */
export function whenLine(d: Pick<JournalDraft, "opened_at" | "closed_at">): string {
  const parts = [d.opened_at && `Opened ${formatDateTimeIST(d.opened_at)}`, d.closed_at && `closed ${formatDateTimeIST(d.closed_at)}`].filter(Boolean);
  return parts.join(" · ");
}

/** Who placed it: him (the ticket, the basket, an approval) or his paper bot. */
export function placedByLine(d: Pick<JournalDraft, "placed_by" | "strategy">): string {
  if (d.placed_by === "paper_bot") return d.strategy ? `Placed by your paper bot (${d.strategy})` : "Placed by your paper bot";
  return "Placed by you";
}

export const DRAFT_NOTE = "Filled in from your trade. Add what you saw and what you would keep or change; only you see it.";
export const NOTES_MAX = 5000;
