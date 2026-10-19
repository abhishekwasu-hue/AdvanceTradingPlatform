import type { MultiView, MultiViewAnalysis, MultiViewSummary } from "../../../types";

/** H-C4 helpers: the summary's sections in reading order, a view's scalar facts as label / value rows, its headlines. */

export const SUMMARY_SECTIONS = ["leans_up", "leans_down", "risks", "possibilities"] as const;
export type SummarySection = (typeof SUMMARY_SECTIONS)[number];

/** The non-empty sections of the summary, in reading order. */
export function sections(summary: MultiViewSummary): { key: SummarySection; items: string[] }[] {
  return SUMMARY_SECTIONS.map((key) => ({ key, items: (summary[key] ?? []).filter((x) => typeof x === "string" && x.trim()) }))
    .filter((s) => s.items.length > 0);
}

const FACT_LABEL: Record<string, string> = {
  adx: "ADX", atr_pct: "ATR % of price", atr_ratio: "ATR vs its median", ema_slope_pct: "EMA slope %", change_pct: "Change %",
  pcr: "PCR", atm_strike: "ATM strike", max_pain: "Max pain", underlying_ltp: "Underlying LTP", total_call_oi: "Call OI",
  total_put_oi: "Put OI", india_vix: "India VIX", high_severity: "High severity", window_hours: "Window (hours)",
  first_bar: "First bar", last_bar: "Last bar",
};

export function factLabel(key: string): string {
  if (FACT_LABEL[key]) return FACT_LABEL[key];
  const words = key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Scalar facts (numbers, short text) as rows; lists and objects are shown elsewhere or not at all. */
export function factRows(view: MultiView): [string, string][] {
  return Object.entries(view.facts ?? {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "" && (typeof v === "number" || typeof v === "string" || typeof v === "boolean"))
    .map(([k, v]) => [factLabel(k), typeof v === "number" ? v.toLocaleString("en-IN", { maximumFractionDigits: 4 }) : String(v)]);
}

export interface Headline { headline: string; source: string | null; published_at: string | null; severity: number | null; verified: boolean }

/** The news view's headlines (third-party text: shown as plain text, never as markup or a link). */
export function headlines(view: MultiView): Headline[] {
  const raw = view.id === "news" && Array.isArray(view.facts?.headlines) ? (view.facts.headlines as Record<string, unknown>[]) : [];
  return raw.filter((h) => h && typeof h.headline === "string").map((h) => ({
    headline: String(h.headline), source: typeof h.source === "string" ? h.source : null,
    published_at: typeof h.published_at === "string" ? h.published_at : null,
    severity: typeof h.severity === "number" ? h.severity : null, verified: h.verified === true,
  }));
}

/** Tokens and rupees across both tiers, or null when no model was called. */
export function totalUsage(a: MultiViewAnalysis): { tokens: number; inr: number } | null {
  const parts = [a.usage?.cheap, a.usage?.strong].filter((u): u is NonNullable<typeof u> => !!u);
  if (!parts.length) return null;
  return { tokens: parts.reduce((n, u) => n + u.tokens_input + u.tokens_output, 0), inr: parts.reduce((n, u) => n + u.cost_inr, 0) };
}

/** A symbol the route accepts (letters, digits, space and & . _ : -), upper-cased and trimmed. */
export function cleanSymbol(text: string): string | null {
  const s = text.trim().toUpperCase();
  return s && s.length <= 40 && /^[A-Z0-9 &._:-]+$/.test(s) ? s : null;
}
