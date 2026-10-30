/** OI Banner (O3): pure presentation helpers (tested in format.test.ts). No number here is invented: every value
 *  comes from the API row, and a missing value is shown as a dash. */
import { formatNumber } from "../ui/format";
import type { OiBannerResponse, OiDirection, OiStrikeSeries } from "./types";

/** Theme-token classes per direction: the banner's left border and the headline colour. */
export const DIRECTION_TONE: Record<OiDirection, { border: string; text: string; label: string }> = {
  BULLISH: { border: "border-l-up", text: "text-up", label: "Bullish" },
  BEARISH: { border: "border-l-down", text: "text-down", label: "Bearish" },
  MIXED: { border: "border-l-warn", text: "text-warn", label: "Mixed" },
  NEUTRAL: { border: "border-l-border", text: "text-fg-muted", label: "Neutral" },
};

export function tone(direction: string | null | undefined) {
  return DIRECTION_TONE[(direction ?? "NEUTRAL") as OiDirection] ?? DIRECTION_TONE.NEUTRAL;
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
  headline: string;
  classes: string | null;
  pcr: string | null;
  chips: string[];
  tone: ReturnType<typeof tone>;
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

/** The banner's text, line by line, exactly as the spec lays it out (OC1: no "Stale" / "Insufficient data" labels -
 * a late reading says how old it is, the first readings of the day say when the change shows). */
export function bannerLines(data: OiBannerResponse): BannerLines {
  const chips: string[] = [];
  if (!data.market_open) chips.push("Market closed");
  else if (data.stale) chips.push(data.age_minutes !== null ? `Last update ${Math.round(data.age_minutes)} min ago` : "No reading yet today");
  const b = data.banner;
  if (!b) {
    return { headline: plainOiText(data.message), classes: null, pcr: null, chips, tone: tone("NEUTRAL") };
  }
  if (b.dte !== null) chips.unshift(b.dte === 0 ? "Expiry today" : b.dte === 1 ? "Expiry tomorrow" : `DTE ${b.dte}`);
  if (b.max_pain !== null) chips.push(`Max pain ${formatNumber(b.max_pain, Number.isInteger(b.max_pain) ? 0 : 2)}`);   // a strike, not a count
  chips.push(`Data as of ${slotTime(b.slot)}`);
  return {
    headline: plainOiText(b.message),
    classes: b.first_of_day ? null : `Put: ${b.put_class} · Call: ${b.call_class}`,
    pcr: b.pcr === null ? "PCR — (no call OI)" : `PCR ${b.pcr.toFixed(2)} — ${b.pcr_label}`,
    chips,
    tone: tone(b.direction),
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
