/** OI Banner (O3): pure presentation helpers (tested in format.test.ts). No number here is invented: every value
 *  comes from the API row, and a missing value is shown as a dash. */
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

/** Contract counts in the Indian grouping (12,34,567); a missing value is a dash. */
export function formatOi(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return Math.round(value).toLocaleString("en-IN");
}

export function formatSigned(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const text = formatOi(Math.abs(value));
  return value > 0 ? `+${text}` : value < 0 ? `-${text}` : text;
}

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

/** The banner's text, line by line, exactly as the spec lays it out. */
export function bannerLines(data: OiBannerResponse): BannerLines {
  const chips: string[] = [];
  if (!data.market_open) chips.push("Market closed");
  else if (data.stale) chips.push(data.age_minutes !== null ? `Stale · ${Math.round(data.age_minutes)} min old` : "Stale · no data yet");
  const b = data.banner;
  if (!b) {
    return { headline: data.message ?? "Insufficient data — history builds through the session", classes: null, pcr: null, chips, tone: tone("NEUTRAL") };
  }
  if (b.dte !== null) chips.unshift(b.dte === 0 ? "Expiry today" : b.dte === 1 ? "Expiry tomorrow" : `DTE ${b.dte}`);
  if (b.max_pain !== null) chips.push(`Max pain ${formatOi(b.max_pain)}`);
  chips.push(`Data as of ${slotTime(b.slot)}`);
  return {
    headline: b.message,
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
