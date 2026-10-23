/** Signals day one (owner order 23 Oct; backend app/signals_feed/day_one.py): the "Pick a ready strategy and a list to
 * watch" card and, once he adopted one, his watch - where each symbol stands under his own strategy, in structure words.
 * `ui_v2` + `strategy_templates` (the server answers 404 while either is off). PAPER only, every entry waits for his
 * Approve; nothing here places, proposes or changes an order. */
import { request } from "../api/client";
import { withheld } from "./language";

export interface DayOneSetting { key: string; label: string; value: string; help: string }
export interface DayOneOffer {
  id: string; name: string; idea: string; difficulty: "Beginner" | "Intermediate" | "Advanced"; family: string; timeframe: string;
  settings: DayOneSetting[]; rules: string[];
}
export interface DayOneList { kind: "system" | "own"; key?: string; id?: number; name: string; group: string; count: number; default: boolean }
export interface DayOneListRef { kind: "system" | "own"; key?: string; id?: number }
export type DayOneState = "new" | "armed" | "recent" | "watching" | "warmup" | "no_data" | "not_read";
export interface DayOneRow {
  symbol: string; exchange: string; state: DayOneState; direction: "LONG" | "SHORT" | null; text: string;
  /** His own signal: the area as a price range ("Buyer area: a–b"), never a single price. */
  area: string | null; setup_day: string | null; bar_day: string | null;
}
export interface DayOneWatch {
  template: { id: string; name: string; idea: string; timeframe: string; settings: DayOneSetting[] };
  strategy_id: string; list: DayOneListRef & { name: string }; truncated: boolean; count: number; max_watch: number;
  mode: "PAPER"; entries: "APPROVAL"; adopted_at: string; evaluated_at: string | null; bar_day: string | null;
  rows: DayOneRow[]; summary: Record<DayOneState, number>; note: string | null;
}
export interface DayOne {
  offers: DayOneOffer[]; lists: DayOneList[]; default_list: DayOneListRef | null; can_adopt: boolean; max_watch: number;
  adopted: DayOneWatch | null; outdated: boolean;
}

export const dayOneApi = {
  get: () => request<DayOne>("/signals/day-one"),
  adopt: (templateId: string, list: DayOneListRef) =>
    request<DayOne>("/signals/day-one/adopt", { method: "POST", body: JSON.stringify({ template_id: templateId, list }) }),
  refresh: () => request<DayOne>("/signals/day-one/refresh", { method: "POST" }),
  stop: () => request<DayOne>("/signals/day-one", { method: "DELETE" }),
};

/** The chip of each state, in words (never colour alone). */
export const STATE_LABEL: Record<DayOneState, string> = {
  new: "New setup", armed: "Waiting for its trigger", recent: "Formed recently", watching: "Watching",
  warmup: "Not enough history", no_data: "No history yet", not_read: "Not read yet",
};
/** The states that stand out (shown first, never folded away). */
export const STANDS_OUT: readonly DayOneState[] = ["new", "armed", "recent"];

export function listValue(ref: DayOneListRef | null | undefined): string {
  if (!ref) return "";
  return ref.kind === "own" ? `own:${ref.id ?? ""}` : `system:${ref.key ?? ""}`;
}

export function listRef(value: string): DayOneListRef | null {
  const [kind, rest] = [value.slice(0, value.indexOf(":")), value.slice(value.indexOf(":") + 1)];
  if (kind === "system" && /^[a-z0-9-]{1,80}$/.test(rest)) return { kind, key: rest };
  if (kind === "own" && /^[1-9][0-9]*$/.test(rest)) return { kind, id: Number(rest) };
  return null;
}

/** The one line on top of the watch: what stands out today. */
export function headline(watch: DayOneWatch): string {
  const s = watch.summary;
  const parts: string[] = [];
  if (s.new) parts.push(`${s.new} new ${s.new === 1 ? "setup" : "setups"}`);
  if (s.armed) parts.push(`${s.armed} waiting for ${s.armed === 1 ? "its" : "their"} trigger`);
  if (s.recent) parts.push(`${s.recent} formed recently`);
  const read = watch.rows.length - s.no_data - s.not_read;
  if (!parts.length) {
    return read ? `No setup in place on the ${watch.count} symbols right now. You will see one here as soon as it forms.` : "";
  }
  return `${parts.join(", ")} on your list of ${watch.count}.`;
}

/** A row's shown line: his own signal, checked again here; a line that is not in the signal language is withheld. */
export function rowText(row: DayOneRow): string {
  return withheld(row.text, "signal_own", { ownSymbols: [row.symbol] });
}

export function rowArea(row: DayOneRow): string | null {
  if (!row.area) return null;
  return withheld(row.area, "signal_own", { ownSymbols: [row.symbol] }) === row.area ? row.area : null;
}
