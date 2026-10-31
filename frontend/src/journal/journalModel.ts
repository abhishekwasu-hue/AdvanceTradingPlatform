import { tagLabel } from "./format";
import type { JournalFilters, JournalListRow, ManualTradeBody, Outcome } from "./types";

/**
 * M62 JN-3: pure parts of the v2 Journal page - the filter choices, "what you tagged most", the manual-trade form
 * (checked before it is sent) and the downloads. Descriptive words only; nothing here suggests a trade.
 */

export const OUTCOME_LABELS: Record<Outcome, string> = { win: "Profit", loss: "Loss", flat: "Flat", open: "Open" };

/** The tags of an entry that count: his own, and the detector's that he has not dismissed. */
export function entryTags(e: { tags: { tag: string; status: string }[]; user_tags?: string[] }): string[] {
  return [...e.tags.filter((t) => t.status !== "DISMISSED").map((t) => t.tag), ...(e.user_tags ?? [])];
}

/** A tag as shown: the detector's own label, else his own words as he wrote them. */
export function tagText(tag: string): string {
  return tagLabel(tag);
}

export interface TagCount { tag: string; label: string; count: number }

/** "What you tagged most": every counted tag over the entries, most first (ties by name). */
export function mostTagged(entries: readonly { tags: { tag: string; status: string }[]; user_tags?: string[] }[], top = 5): TagCount[] {
  const counts = new Map<string, number>();
  for (const e of entries) for (const t of new Set(entryTags(e))) counts.set(t, (counts.get(t) ?? 0) + 1);
  return [...counts.entries()].map(([tag, count]) => ({ tag, label: tagText(tag), count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)).slice(0, top);
}

/** How many filters narrow the list. */
export function activeFilters(f: JournalFilters): number {
  return [f.setup, f.tag, f.outcome, f.date_from, f.date_to, f.include_manual === false].filter(Boolean).length;
}

/** True when any filter narrows the list (the empty state then says so). */
export function filtersOn(f: JournalFilters): boolean {
  return activeFilters(f) > 0;
}

/** The row's key in a list (an ATP trade and a manual entry may share an id). */
export const rowKey = (r: Pick<JournalListRow, "kind" | "id">) => `${r.kind}-${r.id}`;

// --- the "Add a trade done outside ATP" form ---------------------------------------------------------------------------

export interface ManualForm {
  symbol: string;
  direction: "LONG" | "SHORT";
  quantity: string;
  entry_time: string;      // <input type="datetime-local">, read as IST
  entry_price: string;
  stop_loss: string;
  target: string;
  exit_time: string;
  exit_price: string;
  exit_reason: string;
  charges: string;
  setup: string;
  timeframe: string;
  notes: string;
}

export const EMPTY_MANUAL: ManualForm = {
  symbol: "", direction: "LONG", quantity: "", entry_time: "", entry_price: "", stop_loss: "", target: "", exit_time: "", exit_price: "",
  exit_reason: "", charges: "", setup: "", timeframe: "", notes: "",
};

/** A datetime-local value ("2026-10-29T10:15") as an instant in IST ("2026-10-29T10:15:00+05:30"); "" stays null. */
export function istInstant(local: string): string | null {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::(\d{2}))?$/.exec(local.trim());
  return m ? `${m[1]}T${m[2]}:${m[3] ?? "00"}+05:30` : null;
}

function num(raw: string): number | null {
  const text = raw.replace(/,/g, "").trim();
  if (text === "") return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : Number.NaN;
}

/** What keeps the form from being sent (empty = ready). Only symbol, side, quantity, entry time and price are needed. */
export function manualProblems(f: ManualForm, now: Date = new Date()): string[] {
  const out: string[] = [];
  if (!f.symbol.trim()) out.push("Enter the symbol.");
  const qty = num(f.quantity);
  if (qty === null || Number.isNaN(qty) || qty <= 0) out.push("Enter the quantity (more than 0).");
  const entry = istInstant(f.entry_time);
  if (!entry) out.push("Enter the entry date and time.");
  else if (new Date(entry).getTime() > now.getTime() + 5 * 60_000) out.push("The entry time is in the future.");
  const price = num(f.entry_price);
  if (price === null || Number.isNaN(price) || price <= 0) out.push("Enter the entry price.");
  for (const [key, label] of [["stop_loss", "Stop"], ["target", "Target"], ["exit_price", "Exit price"], ["charges", "Charges"]] as const) {
    const v = num(f[key]);
    if (v !== null && (Number.isNaN(v) || v < 0)) out.push(`${label}: enter a number.`);
  }
  const exitAt = istInstant(f.exit_time);
  const hasExitPrice = num(f.exit_price) !== null;
  if (Boolean(exitAt) !== hasExitPrice) out.push("Exit time and exit price go together: enter both, or neither.");
  if (exitAt && entry && new Date(exitAt).getTime() < new Date(entry).getTime()) out.push("The exit time is before the entry time.");
  return out;
}

/** The request body from a ready form (values he left empty stay out). */
export function manualBody(f: ManualForm): ManualTradeBody {
  const opt = (raw: string) => { const v = num(raw); return v === null || Number.isNaN(v) ? null : v; };
  const text = (raw: string) => raw.trim() || null;
  return {
    symbol: f.symbol.trim().toUpperCase(), direction: f.direction, quantity: opt(f.quantity) as number,
    entry_time: istInstant(f.entry_time) as string, entry_price: opt(f.entry_price) as number,
    stop_loss: opt(f.stop_loss), target: opt(f.target), exit_time: istInstant(f.exit_time), exit_price: opt(f.exit_price),
    exit_reason: text(f.exit_reason), charges: opt(f.charges), setup: text(f.setup), timeframe: text(f.timeframe), notes: text(f.notes),
  };
}

/** Saves a file on this device (the export). */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** One line per item from a textarea (blank lines and repeats dropped). */
export function lines(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split("\n")) {
    const item = raw.trim().replace(/\s+/g, " ");
    if (item && !out.includes(item)) out.push(item);
  }
  return out;
}
