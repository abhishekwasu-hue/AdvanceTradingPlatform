import type { ContractRight, Side } from "../../fno/api";

/**
 * M72 PR-9 (CP-9, finding F11, M72Q-4): the Copilot PREPARES an order ticket; the user reviews it and presses Place
 * himself on the same ticket the chain opens (PAPER / LIVE alike - the ticket decides, never the Copilot).
 *
 * What may fill a field (F11): his own words in this message (the contract, the side, lots, a limit price, a stop-loss),
 * his saved Trade settings (applied by the ticket itself, marked "from your defaults"), and the object he opened Ask AI
 * from. Nothing else: a value that is not in his message is dropped (never a model-chosen strike, lots, price or stop),
 * and every field he did not state stays Not set for the ticket to ask. The expiry is picked by him from the list unless
 * he typed one that is on it. Nothing here places, previews or changes an order.
 */
export type PrefillField = "lots" | "price" | "stopLoss";
export type PrefillSource = "typed" | "object";
export interface TicketPrefill {
  lots?: number;
  price?: string;
  stopLoss?: string;
  from: Partial<Record<PrefillField, PrefillSource>>;
}

export interface TicketAsk {
  underlying: string;
  strike: number | null;
  right: ContractRight;
  /** His typed expiry as an IST date (YYYY-MM-DD) or a day + month ("28 NOV") to match on the list; null = he picks. */
  expiryWords: string | null;
  side: Side | null;
  prefill: TicketPrefill;
}

const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
const NUM = String.raw`(\d{1,7}(?:\.\d{1,2})?)`;
const CONTRACT = new RegExp(String.raw`\b(BANK\s+NIFTY|[A-Z][A-Z&-]{1,19})\s+(?:(\d{4}-\d{2}-\d{2}|\d{1,2}\s*(?:${MONTHS.join("|")})[A-Z]*)\s+)?` +
  String.raw`(?:${NUM}\s*(CE|PE|CALL|PUT)\b|(FUT|FUTURES?)\b)`, "i");

/** Every number in his message (as typed), the only numbers a prefill may carry. */
export function numbersIn(text: string): Set<number> {
  return new Set([...text.replace(/,(?=\d{3}\b)/g, "").matchAll(/\d+(?:\.\d+)?/g)].map((m) => Number(m[0])));
}

/** A prefill whose every value is in his message (a `typed` value) or came from the object he opened Ask AI from
 * (`object`, checked against `objectValues`); any other value is dropped, so the field stays Not set. */
export function acceptPrefill(proposed: TicketPrefill, question: string, objectValues: readonly number[] = []): TicketPrefill {
  const typed = numbersIn(question);
  const ok = (field: PrefillField, value: number) => {
    const from = proposed.from[field];
    return Number.isFinite(value) && value > 0 && ((from === "typed" && typed.has(value)) || (from === "object" && objectValues.includes(value)));
  };
  const out: TicketPrefill = { from: {} };
  if (proposed.lots !== undefined && Number.isInteger(proposed.lots) && ok("lots", proposed.lots)) { out.lots = proposed.lots; out.from.lots = proposed.from.lots; }
  for (const f of ["price", "stopLoss"] as const) {
    const v = proposed[f];
    if (v !== undefined && ok(f, Number(v))) { out[f] = v; out.from[f] = proposed.from[f]; }
  }
  return out;
}

/** The contract and his own values in his message, or null when it names no contract (an underlying, a strike and CE / PE,
 * or FUT). */
export function parseTicketAsk(question: string): TicketAsk | null {
  const text = ` ${question.replace(/,(?=\d{3}\b)/g, "")} `;
  const m = CONTRACT.exec(text.toUpperCase());
  if (!m) return null;
  const underlying = m[1].replace(/\s+/g, "") === "BANKNIFTY" ? "BANKNIFTY" : m[1].toUpperCase();
  if (["LOTS", "LOT", "AT", "STOP", "SL", "PRICE", "LIMIT"].includes(underlying)) return null;
  const right: ContractRight = m[5] ? "FUT" : /^(CE|CALL)$/.test(m[4] ?? "") ? "CE" : "PE";
  const strike = right === "FUT" ? null : Number(m[3]);
  const lower = text.toLowerCase();
  const side: Side | null = /\b(buy|go long)\b/.test(lower) ? "BUY" : /\b(sell|short|write)\b/.test(lower) ? "SELL" : null;
  const proposed: TicketPrefill = { from: {} };
  const lots = /\b(\d{1,4})\s*lots?\b/i.exec(text);
  if (lots) { proposed.lots = Number(lots[1]); proposed.from.lots = "typed"; }
  const price = new RegExp(String.raw`\b(?:limit|at|price)\s*(?:of\s*)?${NUM}`, "i").exec(text.slice((m.index ?? 0) + m[0].length));
  if (price) { proposed.price = price[1]; proposed.from.price = "typed"; }
  const stop = new RegExp(String.raw`\b(?:stop[- ]?loss|stop|sl)\s*(?:at|of)?\s*${NUM}`, "i").exec(text);
  if (stop) { proposed.stopLoss = stop[1]; proposed.from.stopLoss = "typed"; }
  return { underlying, strike, right, expiryWords: m[2] ? m[2].replace(/\s+/g, " ").trim() : null, side, prefill: acceptPrefill(proposed, question) };
}

/** His typed expiry matched on the list of expiries (YYYY-MM-DD); null = not on it, or none typed (he picks). */
export function matchExpiry(words: string | null, expiries: readonly string[]): string | null {
  if (!words) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(words)) return expiries.includes(words) ? words : null;
  const m = /^(\d{1,2})\s*([A-Z]{3})/.exec(words.toUpperCase());
  if (!m) return null;
  const month = MONTHS.indexOf(m[2]) + 1;
  const hits = expiries.filter((e) => Number(e.slice(8, 10)) === Number(m[1]) && Number(e.slice(5, 7)) === month);
  return hits.length === 1 ? hits[0] : null;
}

const FIELD_WORDS: Record<PrefillField, string> = { lots: "lots", price: "limit price", stopLoss: "stop-loss" };
const SOURCE_WORDS: Record<PrefillSource, string> = { typed: "from your message", object: "from what you asked about" };

/** The source chips the ticket shows: "2 lots from your message". */
export function prefillChips(p: TicketPrefill): { field: PrefillField; text: string }[] {
  return (["lots", "price", "stopLoss"] as const).filter((f) => p[f] !== undefined && p.from[f]).map((f) => ({
    field: f, text: `${f === "lots" ? `${p.lots} lots` : `${FIELD_WORDS[f]} ${p[f]}`} ${SOURCE_WORDS[p.from[f] as PrefillSource]}`,
  }));
}
