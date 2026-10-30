import { slotTime } from "../oi/format";
import type { OiBannerResponse, OiBannerRow, OiHistoryResponse, OiStrikeSeries, OiStrikesResponse } from "../oi/types";
import { EMPTY, formatCount, formatCountChange, formatDateIST, formatNumber, formatTimeIST } from "../ui/format";

/**
 * M61 (owner, 30 Oct): the pure rules of the v2 option chain's OI tab - information only, in numbers and neutral
 * words (never buy / sell / bullish / bearish / overbought). Every figure comes from the OI endpoints
 * (`/api/option-chain/{u}/banner|history|strikes`, the stored readings); nothing here is invented.
 *
 * - OI-2: the history shows only the readings where call OI or put OI changed (a "Show all" toggle keeps every row);
 *   readings outside the session (pre-open, after close) and partial ones are labelled and shown only under "Show all".
 * - OI-3: ONE put / call order and colour for the page, the chain header's: Calls first (red, `oi-call`), Puts second
 *   (green, `oi-put`).
 * - OI-4: one "Data as of HH:MM" for the tab: the oldest of the sources shown, each source's own time behind (?).
 * - OI-5: the summary card is numbers first; the one-line read is neutral ("Put OI above call OI (PCR 1.04)").
 */

/** OI-3: the page's one order and colour for the two sides (the chain header's CALLS | PUTS). */
export const SIDES = [
  { key: "call", label: "Call OI", fill: "fill-oi-call", swatch: "bg-oi-call", text: "text-oi-call" },
  { key: "put", label: "Put OI", fill: "fill-oi-put", swatch: "bg-oi-put", text: "text-oi-put" },
] as const;

/** A strike as written in a sentence or a chip: 23,000; 122.5. */
export function strikeText(v: number | null | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return EMPTY;
  return formatNumber(v, Number.isInteger(v) ? 0 : 2);
}

/** A strike on a chart axis, as the exchange writes it: 23000, 122.5 (no grouping, so it stays short). */
export function strikeTick(v: number): string {
  return String(Number(v.toFixed(2)));
}

// ---------------------------------------------------------------- OI-2: the history

export type HistoryColumn = "time" | "call" | "put" | "pcr" | "dcall" | "dput" | "read";
export const HISTORY_COLUMNS: readonly { key: HistoryColumn; label: string; phone: boolean }[] = [
  { key: "time", label: "Time", phone: true },
  { key: "call", label: "Call OI", phone: true },
  { key: "put", label: "Put OI", phone: true },
  { key: "pcr", label: "PCR", phone: true },
  { key: "dcall", label: "ΔCall", phone: false },
  { key: "dput", label: "ΔPut", phone: false },
  { key: "read", label: "Read", phone: false },
];
/** The columns a phone can switch on and off (Time and the two OI columns always show). */
export const PHONE_PICKABLE: readonly HistoryColumn[] = ["pcr", "dcall", "dput", "read"];

export interface HistoryRowView {
  row: OiBannerRow;
  dCall: number | null;
  dPut: number | null;
  /** Call OI or put OI differs from the reading before it (the first reading counts as a change). */
  changed: boolean;
  /** A reading inside the session that is not partial (rows from before M61's server carry no phase: session). */
  session: boolean;
  read: string;
}

function inSession(row: OiBannerRow): boolean {
  return (row.phase ?? "session") === "session" && row.partial !== true;
}

function sideWords(side: "Call" | "Put", d: number | null): string {
  if (d === null) return `${side} OI not known`;
  if (d > 0) return `${side} OI up ${formatCount(d)}`;
  if (d < 0) return `${side} OI down ${formatCount(-d)}`;
  return `${side} OI unchanged`;
}

const PHASE_WORDS: Record<string, string> = { pre_open: "Pre-open reading", after_close: "After close", closed_day: "Market closed" };

/** The reading in full words: "Call OI up 1.2 L, Put OI unchanged"; a reading outside the session or partial says so. */
export function readWords(row: OiBannerRow, dCall: number | null, dPut: number | null, first: boolean): string {
  const label = PHASE_WORDS[row.phase ?? ""] ?? (row.partial ? `Partial reading (${row.strikes ?? EMPTY} strikes)` : null);
  const what = first ? "First reading" : `${sideWords("Call", dCall)}, ${sideWords("Put", dPut)}`;
  return label ? `${label} · ${what}` : what;
}

/** Every history row (newest first, as the server sends them) with its change against the reading before it. */
export function historyRows(rows: readonly OiBannerRow[]): HistoryRowView[] {
  return rows.map((row, i) => {
    const older = rows[i + 1];
    const dCall = older ? diff(row.total_call_oi, older.total_call_oi) : null;
    const dPut = older ? diff(row.total_put_oi, older.total_put_oi) : null;
    const changed = !older || dCall !== 0 || dPut !== 0;
    return { row, dCall, dPut, changed, session: inSession(row), read: readWords(row, dCall, dPut, !older) };
  });
}

function diff(a: number | null | undefined, b: number | null | undefined): number | null {
  return typeof a === "number" && typeof b === "number" && Number.isFinite(a) && Number.isFinite(b) ? a - b : null;
}

/** OI-2: the rows shown - only session readings that changed, unless "Show all" is on. */
export function shownRows(views: readonly HistoryRowView[], showAll: boolean): HistoryRowView[] {
  return showAll ? [...views] : views.filter((v) => v.changed && v.session);
}

/** The newest session reading where call OI or put OI changed (HH:MM), or null. */
export function lastChangeAt(views: readonly HistoryRowView[]): string | null {
  const hit = views.find((v) => v.changed && v.session);
  return hit ? slotTime(hit.row.slot) : null;
}

/** One history cell as text. */
export function historyCell(v: HistoryRowView, key: HistoryColumn): string {
  switch (key) {
    case "time": return slotTime(v.row.slot);
    case "call": return formatCount(v.row.total_call_oi);
    case "put": return formatCount(v.row.total_put_oi);
    case "pcr": return v.row.pcr === null ? EMPTY : formatNumber(v.row.pcr, 2);
    case "dcall": return v.dCall === null ? EMPTY : formatCountChange(v.dCall);
    case "dput": return v.dPut === null ? EMPTY : formatCountChange(v.dPut);
    default: return v.read;
  }
}

// ---------------------------------------------------------------- OI-5: the summary

export interface StrikeHigh { strike: number; oi: number }

/** The strike with the largest latest call / put OI among the strikes shown (a tie: the lower strike). */
export function highestOi(series: readonly OiStrikeSeries[] | null | undefined, side: "call" | "put"): StrikeHigh | null {
  let best: StrikeHigh | null = null;
  for (const s of [...(series ?? [])].sort((a, b) => a.strike - b.strike)) {
    const last = s.points[s.points.length - 1];
    const oi = side === "call" ? last?.call_oi : last?.put_oi;
    if (typeof oi === "number" && Number.isFinite(oi) && (best === null || oi > best.oi)) best = { strike: s.strike, oi };
  }
  return best;
}

/** OI-5: the one-line read in neutral words: "Put OI above call OI (PCR 1.04)" - one helper, shared with the
 * dashboard and the OI banner (../oi/format.ts). */
export { oiRead } from "../oi/format";

/** OI-5: the change line - "No OI change since 15:29" when the newest readings repeat the last change, else what the
 * last reading changed ("Last reading: Call OI up 1.2 L, Put OI down 30,000"). Null before the second reading. */
export function changeLine(views: readonly HistoryRowView[]): string | null {
  const sessionRows = views.filter((v) => v.session);
  if (sessionRows.length < 2) return null;
  const newest = sessionRows[0];
  const last = lastChangeAt(views);
  if (!newest.changed && last) return `No OI change since ${last}`;
  return `Last reading: ${sideWords("Call", newest.dCall)}, ${sideWords("Put", newest.dPut)}`;
}

/** "DTE 4", "Expiry today", "Expiry tomorrow". */
export function dteText(dte: number | null | undefined): string {
  if (typeof dte !== "number") return EMPTY;
  return dte === 0 ? "Expiry today" : dte === 1 ? "Expiry tomorrow" : `${dte}`;
}

// ---------------------------------------------------------------- OI-4: one data time, and the market's state

export interface SourceTime { label: string; at: string | null }

/** The tab's sources and their data times (the newest reading of each). */
export function sourceTimes(banner: OiBannerResponse | null, history: OiHistoryResponse | null, strikes: OiStrikesResponse | null): SourceTime[] {
  return [
    { label: "Summary", at: banner?.banner?.data_as_of ?? null },
    { label: "OI history", at: history?.rows[0]?.data_as_of ?? null },
    { label: "OI by strike", at: strikes?.data_as_of ?? null },
  ];
}

/** OI-4: "Data as of HH:MM" - the oldest of the sources that have a time (so no number on the tab is newer than it
 * says); null when none has one. */
export function dataAsOf(sources: readonly SourceTime[]): string | null {
  const times = sources.map((s) => (s.at ? Date.parse(s.at) : Number.NaN)).filter((t) => Number.isFinite(t));
  if (!times.length) return null;
  return `Data as of ${formatTimeIST(Math.min(...times), { suffix: false })}`;
}

/** OI-4: when the market is closed, when the next reading comes: "Next update at 09:15" (today), "Next update tomorrow
 * at 09:15", "Next update on Mon 2 Nov at 09:15" - from the exchange calendar's next open (system status). */
export function nextUpdateText(nextOpen: string | null | undefined, nowMs: number = Date.now()): string {
  const at = nextOpen ? Date.parse(nextOpen) : Number.NaN;
  if (!Number.isFinite(at)) return "Next update when the market opens";
  const time = formatTimeIST(at, { suffix: false });
  const day = (ms: number) => formatDateIST(ms);
  if (day(at) === day(nowMs)) return `Next update at ${time}`;
  if (day(at) === day(nowMs + 86_400_000)) return `Next update tomorrow at ${time}`;
  const full = formatDateIST(at, { weekday: true });
  return `Next update on ${full.replace(/ \d{4}$/, "")} at ${time}`;
}

/** OI-4: the tab reads again every minute while the market is open, never while it is closed. */
export const OI_POLL_MS = 60_000;
export function shouldPoll(bannerMarketOpen: boolean | null | undefined, statusOpen: boolean | null): boolean {
  if (statusOpen === false) return false;
  return bannerMarketOpen !== false;
}

/** The words a test checks never show on the OI or PCR tabs (information only). */
export const VERDICT_WORDS = /\b(buy|buying|sell|selling|bullish|bearish|overbought|oversold|target|stop[- ]loss)\b/i;
