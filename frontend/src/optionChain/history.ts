/**
 * M96 N1: the OI view's history sections - OI change over a From-To range, the replay of OI per strike, the
 * multi-expiry view and the expected move - their reads and their rules, free of React (tested in history.test.ts).
 * Reads (the user's own stored chains only, backend app/market_lake/chain_history_routes.py):
 * - `GET /api/chain-history/coverage?underlying=` the days with stored chains (first / last reading);
 * - `GET /api/chain-history/oi-range` per strike call / put OI at two times and the change;
 * - `GET /api/chain-history/oi-replay` every stored reading of one day and expiry;
 * - `GET /api/chain-history/expiries` per stored expiry: total OI, OI change, PCR, max pain, ATM IV.
 * Every number is the broker's or computed from it; a missing one stays null (shown as "–"), never a sample.
 */
import { request } from "../api/client";
import { formatLakhs } from "../ui/format";
import type { EodChainReply } from "../api/eod";
import type { ChainExpiry, LiveChain } from "./types";

export interface CoverageDay { underlying: string; day: string; chains: number; expiries: number; first_slot: string; last_slot: string }
export interface CoverageReply { days: CoverageDay[]; slot_minutes: number }

export interface ReadingMeta { slot: string; captured_at: string; spot: number | null; expiry: string }

export interface RangeRow {
  strike: number;
  ce_from: number | null; ce_to: number | null; ce_change: number | null;
  pe_from: number | null; pe_to: number | null; pe_change: number | null;
  /** Put OI change minus call OI change. */
  diff: number | null;
}
export interface RangeReply {
  underlying: string; expiry: string; start: string; end: string;
  /** Which end has no stored reading ("same": both times fall on the same reading). */
  missing: "start" | "end" | "both" | "same" | null;
  from: ReadingMeta | null; to: ReadingMeta | null; atm: number | null; rows: RangeRow[];
  totals: { ce_change: number; pe_change: number; diff: number } | null;
}

export interface ReplayFrame extends ReadingMeta { ce: (number | null)[]; pe: (number | null)[] }
export interface ReplayReply { underlying: string; expiry: string; day: string; around: number; strikes: number[]; frames: ReplayFrame[] }

export interface ExpirySummary extends ReadingMeta {
  total_call_oi: number | null; total_put_oi: number | null;
  call_oi_change: number | null; put_oi_change: number | null;
  pcr_oi: number | null; max_pain: number | null; atm_strike: number | null;
  /** Percent (13.2 = 13.2%). */
  atm_iv: number | null;
  days_to_expiry: number | null;
  since: string | null;
}
export interface ExpiriesReply { underlying: string; day: string; at: string; expiries: ExpirySummary[] }

// OI units (owner Q3: always lakhs, 2 decimals) - the shared formatter in src/ui/format.ts.
export { formatLakhs };
/** A change in lakhs, signed: "+0.61 L", "−1.70 L", "0.00 L". */
export function formatLakhsChange(v: number | null | undefined): string {
  return formatLakhs(v, { signed: true });
}

const q = (o: Record<string, string | number>) => Object.entries(o).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

export const historyApi = {
  coverage: (underlying: string, signal?: AbortSignal) => request<CoverageReply>(`/chain-history/coverage?${q({ underlying })}`, { signal }),
  range: (underlying: string, expiry: string, start: string, end: string, signal?: AbortSignal) =>
    request<RangeReply>(`/chain-history/oi-range?${q({ underlying, expiry, start, end })}`, { signal }),
  replay: (underlying: string, expiry: string, day: string, around: number, signal?: AbortSignal) =>
    request<ReplayReply>(`/chain-history/oi-replay?${q({ underlying, expiry, day, around })}`, { signal }),
  expiries: (underlying: string, at?: string | null, signal?: AbortSignal) =>
    request<ExpiriesReply>(`/chain-history/expiries?${q(at ? { underlying, at } : { underlying })}`, { signal }),
};

// ---------------------------------------------------------------- 1. From-To range

/** "HH:MM" of an IST ISO time ("2026-03-10T10:05:20+05:30" -> "10:05"). */
export function hhmm(iso: string | null | undefined): string {
  const m = /T(\d{2}:\d{2})/.exec(iso ?? "");
  return m ? m[1] : "";
}

/** The pickers' value as the API's IST date-time ("2026-03-10" + "10:05" -> "2026-03-10T10:05:00"). Null when either is empty. */
export function pickerTime(day: string, time: string): string | null {
  return /^\d{4}-\d{2}-\d{2}$/.test(day) && /^\d{2}:\d{2}$/.test(time) ? `${day}T${time}:00` : null;
}

/** The rows within `around` strikes of `atm` (all when `around` is 0 or the ATM is not known). */
export function rowsAround<T extends { strike: number }>(rows: readonly T[], atm: number | null, around: number): T[] {
  if (!around || atm === null || rows.length <= around * 2 + 1) return [...rows];
  let mid = 0;
  rows.forEach((r, i) => { if (Math.abs(r.strike - atm) < Math.abs(rows[mid].strike - atm)) mid = i; });
  return rows.slice(Math.max(0, mid - around), mid + around + 1);
}

/** One plain line for a range answer without numbers - or null when it has them. */
export function rangeNote(r: RangeReply | null): string | null {
  if (!r) return null;
  const at = (iso: string) => `${hhmm(iso)} on ${iso.slice(8, 10)}-${iso.slice(5, 7)}`;
  switch (r.missing) {
    case "both": return `No stored reading at or before ${at(r.start)} or ${at(r.end)} for this expiry.`;
    case "start": return `No stored reading at or before ${at(r.start)} for this expiry. Pick a later 'From' time.`;
    case "end": return `No stored reading at or before ${at(r.end)} for this expiry.`;
    case "same": return "Both times fall on the same stored reading. Move them further apart.";
    default: return null;
  }
}

/** The reading actually used differs from the time asked (the newest reading at or before it). */
export function usedNote(asked: string, used: ReadingMeta | null): string | null {
  if (!used) return null;
  const a = hhmm(asked), u = hhmm(used.captured_at);
  return a && u && a !== u ? `reading of ${u}` : null;
}

// ---------------------------------------------------------------- 2. Replay

export const SPEEDS = [1, 2, 4, 8] as const;
export type Speed = (typeof SPEEDS)[number];
/** One frame per this many milliseconds at speed 1. */
export const FRAME_MS = 1000;

/** The next frame while playing; null at the last one (the replay stops there). */
export function nextFrame(index: number, count: number): number | null {
  return index + 1 < count ? index + 1 : null;
}

/** Keeps a slider value inside the frames. */
export function clampFrame(index: number, count: number): number {
  if (count <= 0) return 0;
  return Math.min(Math.max(0, Math.round(index)), count - 1);
}

export interface ReplayRow { strike: number; ce: number | null; pe: number | null; ceChange: number | null; peChange: number | null }

/** Frame `index`'s OI per strike and its change since the replay's first frame (earlier frames only - no look-ahead). */
export function replayRows(reply: ReplayReply, index: number): ReplayRow[] {
  const frame = reply.frames[index];
  const first = reply.frames[0];
  if (!frame || !first) return [];
  const d = (now: number | null, base: number | null) => (now !== null && base !== null ? now - base : null);
  return reply.strikes.map((strike, i) => ({ strike, ce: frame.ce[i], pe: frame.pe[i], ceChange: d(frame.ce[i], first.ce[i]), peChange: d(frame.pe[i], first.pe[i]) }));
}

/** The largest OI in the replay's whole day (a fixed bar scale, so the bars do not jump between frames). */
export function replayScale(reply: ReplayReply): number {
  let top = 0;
  for (const f of reply.frames) for (const v of [...f.ce, ...f.pe]) if (v !== null && v > top) top = v;
  return top;
}

// ---------------------------------------------------------------- 3. Multi-expiry

/** The expiries shown side by side: the two nearest, then the nearest monthly not already among them. */
export function pickExpiries(list: readonly ChainExpiry[]): ChainExpiry[] {
  const out = list.slice(0, 2);
  const monthly = list.find((e) => e.kind === "monthly" && !out.some((o) => o.date === e.date));
  return monthly ? [...out, monthly] : out;
}

export type SummarySource = "stored" | "live" | "last_close";
export interface ExpiryColumn {
  expiry: string; kind: "weekly" | "monthly"; source: SummarySource | null; asOf: string | null;
  totalCall: number | null; totalPut: number | null; callChange: number | null; putChange: number | null;
  pcr: number | null; maxPain: number | null; atmIv: number | null; dte: number | null;
}

export function emptyColumn(e: ChainExpiry): ExpiryColumn {
  return { expiry: e.date, kind: e.kind, source: null, asOf: null, totalCall: null, totalPut: null, callChange: null, putChange: null,
    pcr: null, maxPain: null, atmIv: null, dte: null };
}

export function storedColumn(e: ChainExpiry, s: ExpirySummary): ExpiryColumn {
  return { expiry: e.date, kind: e.kind, source: "stored", asOf: s.captured_at, totalCall: s.total_call_oi, totalPut: s.total_put_oi,
    callChange: s.call_oi_change, putChange: s.put_oi_change, pcr: s.pcr_oi, maxPain: s.max_pain, atmIv: s.atm_iv, dte: s.days_to_expiry };
}

function sumOf(values: (number | null)[]): number | null {
  const known = values.filter((v): v is number => v !== null && Number.isFinite(v));
  return known.length ? known.reduce((a, b) => a + b, 0) : null;
}

/** One live chain (the same page read as the chain table) as a column; OI change only when the chain has it. */
export function liveColumn(e: ChainExpiry, live: LiveChain): ExpiryColumn {
  const ready = live.oi_change.status === "ready";
  return { expiry: e.date, kind: e.kind, source: "live", asOf: live.data_as_of ?? live.fetched_at,
    totalCall: live.summary.total_call_oi, totalPut: live.summary.total_put_oi,
    callChange: ready ? sumOf(live.rows.map((r) => r.call.oi_change)) : null, putChange: ready ? sumOf(live.rows.map((r) => r.put.oi_change)) : null,
    pcr: live.summary.pcr_oi, maxPain: live.summary.max_pain, atmIv: live.summary.atm_iv, dte: live.summary.days_to_expiry };
}

/** Max pain from OI alone: the strike where option writers pay the least at expiry (calls and puts, every strike). */
export function maxPainOf(rows: readonly { strike: number; callOi: number | null; putOi: number | null }[]): number | null {
  let best: { k: number; pain: number } | null = null;
  for (const at of rows) {
    let pain = 0;
    for (const r of rows) {
      pain += (r.callOi ?? 0) * Math.max(0, at.strike - r.strike) + (r.putOi ?? 0) * Math.max(0, r.strike - at.strike);
    }
    if (best === null || pain < best.pain) best = { k: at.strike, pain };
  }
  return rows.some((r) => (r.callOi ?? 0) > 0 || (r.putOi ?? 0) > 0) ? best?.k ?? null : null;
}

/** The last close chain (NSE's F&O day file, no broker) as a column: OI totals, PCR and max pain - no IV, no change. */
export function eodColumn(e: ChainExpiry, eod: EodChainReply): ExpiryColumn {
  const calls = sumOf(eod.rows.map((r) => r.call?.oi ?? null));
  const puts = sumOf(eod.rows.map((r) => r.put?.oi ?? null));
  return { ...emptyColumn(e), source: "last_close", asOf: eod.date, totalCall: calls, totalPut: puts,
    pcr: calls && puts !== null ? puts / calls : null,
    maxPain: maxPainOf(eod.rows.map((r) => ({ strike: r.strike, callOi: r.call?.oi ?? null, putOi: r.put?.oi ?? null }))) };
}

/** A stored reading counts as current while the market is open only when it is at most this old. */
export const STORED_FRESH_MS = 3 * 60_000;

export function storedIsCurrent(s: ExpirySummary, marketOpen: boolean, now = Date.now()): boolean {
  if (!marketOpen) return true;
  const t = Date.parse(s.captured_at);
  return Number.isFinite(t) && now - t <= STORED_FRESH_MS;
}

// ---------------------------------------------------------------- 4. Expected move (from the option prices)

export interface ExpectedMoveInput {
  spot: number | null;
  /** ATM call + put last price. */
  straddle: number | null;
  /** ATM IV in percent (13.2 = 13.2%). */
  ivPct: number | null;
  /** Calendar days to the expiry close (fractional). */
  days: number | null;
}
export interface ExpectedMoveRange {
  straddle: { low: number; high: number; move: number } | null;
  iv: { low: number; high: number; move: number } | null;
}

const ok = (v: number | null): v is number => v !== null && Number.isFinite(v);

/** Straddle-based: spot ± straddle. IV-based: spot ± spot × IV × sqrt(days / 365). Each null when an input is missing. */
export function expectedMove({ spot, straddle, ivPct, days }: ExpectedMoveInput): ExpectedMoveRange {
  if (!ok(spot) || spot <= 0) return { straddle: null, iv: null };
  const s = ok(straddle) && straddle > 0 ? { low: spot - straddle, high: spot + straddle, move: straddle } : null;
  const ivMove = ok(ivPct) && ivPct > 0 && ok(days) && days > 0 ? spot * (ivPct / 100) * Math.sqrt(days / 365) : null;
  return { straddle: s, iv: ivMove !== null ? { low: spot - ivMove, high: spot + ivMove, move: ivMove } : null };
}

/** Calendar days from `asOf` to 15:30 IST on `expiry` ("YYYY-MM-DD"); 0 once passed; null when unknown. */
export function daysToExpiryClose(expiry: string | null, asOf: number): number | null {
  if (!expiry || !/^\d{4}-\d{2}-\d{2}$/.test(expiry)) return null;
  const close = Date.parse(`${expiry}T15:30:00+05:30`);
  if (!Number.isFinite(close) || !Number.isFinite(asOf)) return null;
  return Math.max(0, (close - asOf) / 86_400_000);
}

/** The ATM straddle of a live chain: call + put last price at the strike nearest the spot. */
export function straddleOf(live: LiveChain): number | null {
  const atm = live.summary.atm_strike;
  const row = atm === null ? undefined : live.rows.find((r) => r.strike === atm);
  return row && ok(row.call.ltp) && ok(row.put.ltp) ? row.call.ltp + row.put.ltp : null;
}

/** The ATM straddle of a last close chain (no broker): call + put close at the strike nearest the spot. */
export function eodStraddleOf(eod: EodChainReply): number | null {
  if (eod.spot === null || eod.rows.length === 0) return null;
  const row = eod.rows.reduce((a, b) => (Math.abs(b.strike - (eod.spot as number)) < Math.abs(a.strike - (eod.spot as number)) ? b : a));
  return row.call && row.put ? row.call.close + row.put.close : null;
}
