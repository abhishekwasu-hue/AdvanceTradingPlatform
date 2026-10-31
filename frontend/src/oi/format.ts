/** OI Banner (O3): pure presentation helpers (tested in format.test.ts). No number here is invented: every value
 *  comes from the API row, and a missing value is shown as a dash. */
import { formatCount, formatNumber } from "../ui/format";
import type { OiBannerResponse, OiBannerRow, OiDirection, OiStrikeSeries } from "./types";

/** M61 follow-up (owner order M61, OI-5; CLAUDE.md section 3): the engine's internal labels in neutral words, mapped
 * here at the presentation layer - the engine's values (BULLISH / BEARISH / MIXED / NEUTRAL, the five PCR bands, the
 * stable OI-lead signal) are not changed. The same table as the server's app/option_chain/oi_words.py (notifications).
 * Never a verdict word, never an action word: what the numbers did. */
export const DIRECTION_WORDS: Record<OiDirection, string> = {
  BULLISH: "Put premium down / call premium up",
  BEARISH: "Put premium up / call premium down",
  MIXED: "Put and call readings differ",
  NEUTRAL: "No clear OI change",
};

const upper = (v: string | null | undefined) => (v ?? "").trim().toUpperCase();

/** The banner direction (from the put / call OI-premium classes) in words; anything unknown reads as no clear change. */
export function directionWords(direction: string | null | undefined): string {
  return DIRECTION_WORDS[upper(direction) as OiDirection] ?? DIRECTION_WORDS.NEUTRAL;
}

/** The stable signal ("BULLISH (Strong)", "BEARISH Weakening"): which side's total OI leads and whether the lead grew. */
export function leadWords(label: string | null | undefined): string {
  const t = upper(label);
  const side = t.includes("BULLISH") ? "Put OI lead" : t.includes("BEARISH") ? "Call OI lead" : null;
  if (!side) return "No OI lead";
  return t.includes("WEAKENING") ? `${side}, not growing` : t.includes("STRONG") ? `${side}, growing` : side;
}

/** The five PCR bands as the option chain's zone names (very low / low / balanced / high / very high). */
export const PCR_BAND_WORDS: Record<string, string> = {
  OVERSOLD: "Very low PCR zone", MILD_BEARISH: "Low PCR zone", SIDEWAYS: "Balanced PCR zone",
  MILD_BULLISH: "High PCR zone", OVERBOUGHT: "Very high PCR zone",
};
export function pcrBandWords(band: string | null | undefined): string {
  return PCR_BAND_WORDS[upper(band)] ?? "PCR zone not known";
}

/** One side's OI-premium class in what the numbers did ("Writing" -> "OI up, premium down"). */
const CLASS_WORDS: Record<string, string> = {
  WRITING: "OI up, premium down", BUYING: "OI up, premium up", "SHORT COVERING": "OI down, premium up",
  "LONG UNWINDING": "OI down, premium down", "FLAT/UNCLEAR": "no clear change", "INSUFFICIENT DATA": "not known yet",
};
export function classWords(c: string | null | undefined): string {
  return CLASS_WORDS[upper(c).replace(/_/g, " ")] ?? "not known yet";
}

/** An alert-log state in the words above (max pain, DTE and an OI wall "CE@25000" are already neutral). */
export function alertStateWords(alertType: string, state: string | null | undefined): string {
  if (state === null || state === undefined || state === "") return "—";
  if (alertType === "DIRECTION_CHANGE") return directionWords(state);
  if (alertType === "STABLE_FLIP" || alertType === "STRENGTH_CHANGE") return leadWords(state);
  if (alertType === "PCR_BAND") return pcrBandWords(state);
  return state;
}

/** The option-chain analysis' bias (app/option_chain/analysis.py: BULLISH only when PCR > 1.2 AND put OI builds while
 * call OI is flat or falling; BEARISH the mirror; CONFLICTING when the two disagree) as what the two numbers show. */
export const CHAIN_READ_WORDS: Record<string, string> = {
  BULLISH: "PCR above 1.2, put OI building",
  BEARISH: "PCR below 0.8, call OI building",
  CONFLICTING: "PCR and OI change differ",
  NEUTRAL: "No one-sided read",
};
export function chainReadWords(bias: string | null | undefined): string {
  return CHAIN_READ_WORDS[upper(bias)] ?? CHAIN_READ_WORDS.NEUTRAL;
}

/** OI-5: the one-line read in neutral words: "Put OI above call OI (PCR 1.04)" (the option chain's OI tab, the
 * banner and the dashboard use this one helper). */
export function oiRead(row: Pick<OiBannerRow, "total_call_oi" | "total_put_oi" | "pcr"> | null | undefined): string | null {
  if (!row || typeof row.total_call_oi !== "number" || typeof row.total_put_oi !== "number") return null;
  const pcr = row.pcr === null || row.pcr === undefined ? "PCR –" : `PCR ${formatNumber(row.pcr, 2)}`;
  if (row.total_put_oi > row.total_call_oi) return `Put OI above call OI (${pcr})`;
  if (row.total_put_oi < row.total_call_oi) return `Call OI above put OI (${pcr})`;
  return `Put OI equal to call OI (${pcr})`;
}

/** M61 OI-1: OI figures go through the app's ONE count formatter (ui/format.ts formatCount / formatCountChange:
 * "85,000", "12.35 L", "1.24 Cr", "+21.6 L"); these names stay as thin aliases for the older OI components. */
export { formatCount as formatOi, formatCountChange as formatSigned } from "../ui/format";

/** HH:MM of an ISO timestamp as written by the API (IST offset kept, no local-time conversion). */
export function slotTime(iso: string | null | undefined): string {
  const m = /T(\d{2}:\d{2})/.exec(iso ?? "");
  return m ? m[1] : "—";
}

export interface BannerLines {
  /** The neutral read: "Put OI above call OI (PCR 1.04)". */
  headline: string;
  /** "Put: OI up, premium down · Call: no clear change"; the first reading of the day says when the change shows. */
  classes: string | null;
  /** The totals behind the read: "Call OI 12.35 L · Put OI 12.84 L". */
  totals: string | null;
  chips: string[];
}

/** OC1: the first readings of the day, in plain words (the server says "Insufficient data - history builds ..."). */
export const OI_BUILDING_TEXT = "OI change will show after the first few minutes";

/** A server message in plain words: the "Insufficient data" wording becomes OI_BUILDING_TEXT; others are kept. */
export function plainOiText(text: string | null | undefined): string {
  if (!text || /^insufficient data/i.test(text.trim())) return OI_BUILDING_TEXT;
  return text;
}

/** OC1: a chip that says the readings are late (shown in the warning colour). */
export function isLateChip(chip: string): boolean {
  return chip.startsWith("Last update") || chip === "No reading yet today";
}

/** The banner's text, line by line (OC1: no "Stale" / "Insufficient data" labels - a late reading says how old it is,
 * the first readings of the day say when the change shows). M61 OI-5: numbers and neutral words only - the server's
 * verdict message, PCR band label and direction are not shown, and the banner has no direction colour. */
export function bannerLines(data: OiBannerResponse): BannerLines {
  const chips: string[] = [];
  if (!data.market_open) chips.push("Market closed");
  else if (data.stale) chips.push(data.age_minutes !== null ? `Last update ${Math.round(data.age_minutes)} min ago` : "No reading yet today");
  const b = data.banner;
  if (!b) {
    return { headline: plainOiText(data.message), classes: null, totals: null, chips };
  }
  if (b.dte !== null) chips.unshift(b.dte === 0 ? "Expiry today" : b.dte === 1 ? "Expiry tomorrow" : `DTE ${b.dte}`);
  if (b.max_pain !== null) chips.push(`Max pain ${formatNumber(b.max_pain, Number.isInteger(b.max_pain) ? 0 : 2)}`);   // a strike, not a count
  chips.push(`Data as of ${slotTime(b.slot)}`);
  const read = oiRead(b);
  return {
    headline: read ?? OI_BUILDING_TEXT,
    classes: b.first_of_day ? OI_BUILDING_TEXT : `Put: ${classWords(b.put_class)} · Call: ${classWords(b.call_class)}`,
    totals: read ? `Call OI ${formatCount(b.total_call_oi)} · Put OI ${formatCount(b.total_put_oi)}` : null,
    chips,
  };
}

/** Per strike: the latest call/put OI and the change since the day's first reading. */
export function strikeBars(series: OiStrikeSeries[]) {
  return series.map((s) => {
    const last = s.points[s.points.length - 1];
    const call = last?.call_oi ?? null;
    const put = last?.put_oi ?? null;
    return {
      strike: s.strike,
      call, put,
      callChange: call !== null && s.baseline_call_oi !== null ? call - s.baseline_call_oi : null,
      putChange: put !== null && s.baseline_put_oi !== null ? put - s.baseline_put_oi : null,
    };
  });
}
