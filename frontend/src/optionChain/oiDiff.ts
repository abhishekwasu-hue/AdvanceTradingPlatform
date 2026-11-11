/**
 * M96 L1 (owner, 11 Nov: "taken from the Trade repo AS IS"): the pure parts of the OI-difference table and the OI banner,
 * ported from the Trade dashboard (tested in oiDiff.test.tsx). The server sends the Trade "Net Diff" rows
 * (backend app/option_chain/net_diff.py: ATM ± 6 strikes, the change since the app's first reading of the day); this
 * file only reads them the way Trade shows them. No word here is ATP's own:
 * - Net Diff = PE Chg OI − CE Chg OI per strike; Dominance by its sign: "Bulls" (> 0), "Bears" (< 0), "Neutral" (0);
 * - the TOTAL row: the sums of CE Chg OI, CE Total OI, PE Total OI, PE Chg OI and Net Diff;
 * - the five PCR zones with Trade's edges (below 0.70 / below 0.90 / up to 1.0 / up to 1.3 / above), labels in English;
 * - the Put / Call classes in English; days to expiry in CALENDAR days (Trade compute_dte), "Expiry today" at 0.
 */
import { formatDateIST } from "../ui/format";
import type { NetDiff, NetDiffRow } from "./types";

export type Dominance = "Bulls" | "Bears" | "Neutral";

/** Trade: "Bulls" if diff > 0, "Bears" if diff < 0, "Neutral" otherwise (Trade's emoji left out). */
export function dominanceOf(diff: number): Dominance {
  return diff > 0 ? "Bulls" : diff < 0 ? "Bears" : "Neutral";
}

export interface NetDiffShown {
  strike: number;
  ceChg: number;
  ceOi: number;
  ceLtp: number | null;
  peLtp: number | null;
  peOi: number;
  peChg: number;
  /** PE Chg OI − CE Chg OI. */
  diff: number;
  dominance: Dominance;
}

/** The table's rows from the server's, Net Diff and Dominance read again from the two changes. */
export function netDiffRows(rows: readonly NetDiffRow[]): NetDiffShown[] {
  return rows.map((r) => {
    const diff = r.pe_chg - r.ce_chg;
    return { strike: r.strike, ceChg: r.ce_chg, ceOi: r.ce_oi, ceLtp: r.ce_ltp, peLtp: r.pe_ltp, peOi: r.pe_oi, peChg: r.pe_chg,
      diff, dominance: dominanceOf(diff) };
  });
}

export interface NetDiffTotal { ceChg: number; ceOi: number; peOi: number; peChg: number; diff: number }

/** Trade's TOTAL row: the column sums (LTP and Dominance blank). */
export function netDiffTotal(rows: readonly NetDiffShown[]): NetDiffTotal {
  const sum = (f: (r: NetDiffShown) => number) => rows.reduce((a, r) => a + f(r), 0);
  const ceChg = sum((r) => r.ceChg);
  const peChg = sum((r) => r.peChg);
  return { ceChg, ceOi: sum((r) => r.ceOi), peOi: sum((r) => r.peOi), peChg, diff: peChg - ceChg };
}

/** Trade's window: ATM − 6 steps ≤ strike ≤ ATM + 6 steps. */
export function inTradeWindow(strike: number, atm: number, step: number, range = 6): boolean {
  return strike >= atm - range * step && strike <= atm + range * step;
}

/** The table's state: "building" on the day's first reading, "none" when the server could not place the window. */
export function netDiffStatus(nd: NetDiff | undefined): "building" | "ready" | "none" {
  if (!nd || nd.status === "none" || nd.rows.length === 0) return "none";
  return nd.status;
}

// --- The banner --------------------------------------------------------------------------------------------------

export type PcrZone = "OVERSOLD" | "MILD_BEARISH" | "SIDEWAYS" | "MILD_BULLISH" | "OVERBOUGHT";

/** Trade compute_pcr_zone_label, in English (the meaning kept; no emoji). */
export const PCR_ZONE_LABEL: Record<PcrZone, string> = {
  OVERSOLD: "Oversold - possible reversal (bear-trap risk)",
  MILD_BEARISH: "Mild bearish trend (not yet oversold)",
  SIDEWAYS: "Range-bound (sideways) trend",
  MILD_BULLISH: "Mild bullish trend (not yet overbought)",
  OVERBOUGHT: "Overbought - possible reversal (bull-trap risk)",
};

/** Trade's colour of each zone (its emoji: green oversold, red mild bearish, amber sideways, green mild bullish, red
 *  overbought). */
export const PCR_ZONE_TONE: Record<PcrZone, "up" | "down" | "warn"> = {
  OVERSOLD: "up", MILD_BEARISH: "down", SIDEWAYS: "warn", MILD_BULLISH: "up", OVERBOUGHT: "down",
};

/** Trade's five zones (the PCR is already rounded to two places, as Trade's compute_pcr_signal does). */
export function pcrZone(pcr: number | null | undefined): PcrZone | null {
  if (typeof pcr !== "number" || !Number.isFinite(pcr)) return null;
  if (pcr < 0.7) return "OVERSOLD";
  if (pcr < 0.9) return "MILD_BEARISH";
  if (pcr <= 1.0) return "SIDEWAYS";
  if (pcr <= 1.3) return "MILD_BULLISH";
  return "OVERBOUGHT";
}

/** Trade's Put / Call classes (oi_analysis.classify_oi_price_action) in English. */
const CLASS_TEXT: Record<string, string> = {
  WRITING: "Writing", BUYING: "Buying", "SHORT COVERING": "Short covering", "LONG UNWINDING": "Long unwinding",
  "FLAT/UNCLEAR": "Flat or unclear", "INSUFFICIENT DATA": "Not enough data",
};
export function classText(c: string | null | undefined): string {
  return CLASS_TEXT[(c ?? "").trim().toUpperCase().replace(/_/g, " ")] ?? "Not enough data";
}

/** The banner's colour by direction (Trade: green / red / amber / grey). */
export type BannerTone = "up" | "down" | "warn" | "neutral";
export function bannerTone(direction: string | null | undefined): BannerTone {
  const d = (direction ?? "").toUpperCase();
  return d === "BULLISH" ? "up" : d === "BEARISH" ? "down" : d === "MIXED" ? "warn" : "neutral";
}

/** Today's date in IST as YYYY-MM-DD. */
export function todayIst(now: number = Date.now()): string {
  return new Date(now + 5.5 * 3_600_000).toISOString().slice(0, 10);
}

/** Trade compute_dte: calendar days from today (IST) to the expiry date; null when the date is not readable. */
export function calendarDte(expiry: string | null | undefined, today: string): number | null {
  const e = Date.parse(`${(expiry ?? "").slice(0, 10)}T00:00:00Z`);
  const t = Date.parse(`${today.slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(e) || !Number.isFinite(t)) return null;
  return Math.round((e - t) / 86_400_000);
}

/** Trade's expiry line: "Expiry: 13 Nov 2026 (2 days left)"; "Expiry today" at 0. */
export function expiryLine(expiry: string | null | undefined, dte: number | null): string | null {
  if (!expiry || dte === null) return null;
  const day = formatDateIST(`${expiry.slice(0, 10)}T12:00:00+05:30`);
  const left = dte === 0 ? "Expiry today" : dte === 1 ? "1 day left" : `${dte} days left`;
  return `Expiry: ${day} (${left})`;
}

/** Trade's History "Signal" colour: Strong up green, Strong down red, a weakening side the OTHER colour, else grey. */
export function signalTone(signal: string | null | undefined): "up" | "down" | "muted" {
  const s = signal ?? "";
  const weak = s.includes("Weakening");
  if (s.includes("BULLISH")) return weak ? "down" : "up";
  if (s.includes("BEARISH")) return weak ? "up" : "down";
  return "muted";
}
