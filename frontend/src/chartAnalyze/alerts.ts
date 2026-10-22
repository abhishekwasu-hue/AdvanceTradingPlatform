import type { AlertRuleInput } from "../alerts/rules";
import type { AlertSpec } from "./types";

/**
 * Analyze a chart: "Set an alert on this area" and "Alert me when this happens" create an ordinary alert rule through
 * ATP's existing alert creation (POST /api/alerts/rules, an instrument rule with a ScreenQL price condition). Only a
 * price condition can become an alert; anything else ("a lower-timeframe change of character") cannot yet, and the
 * page says so. The backend test test_the_alert_conditions_the_page_builds_are_valid_screenql pins these shapes.
 */

export const CANNOT_ALERT = "We can't set an alert on this condition yet.";
export const APPROXIMATE_NOTE = "Prices read from a picture are approximate - check them against your chart before saving.";

/** Prices in a condition: plain decimals, at most 4 places, no grouping commas. */
export function fmtPrice(value: number): string {
  return String(Math.round(value * 10_000) / 10_000);
}

export function validPrice(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

/** `close >= low and close <= high` for an area (a price band); the two ends in order. */
export function bandCondition(low: number, high: number): string | null {
  if (!validPrice(low) || !validPrice(high)) return null;
  const [a, b] = low <= high ? [low, high] : [high, low];
  return `close >= ${fmtPrice(a)} and close <= ${fmtPrice(b)}`;
}

/** The ScreenQL condition for an engine's alert spec, or null when it is not a price condition. */
export function alertCondition(spec: AlertSpec | null | undefined): string | null {
  if (!spec) return null;
  if (spec.kind === "above" && validPrice(spec.price)) return `close > ${fmtPrice(spec.price)}`;
  if (spec.kind === "below" && validPrice(spec.price)) return `close < ${fmtPrice(spec.price)}`;
  if (spec.kind === "between" && validPrice(spec.low) && validPrice(spec.high)) return bandCondition(spec.low, spec.high);
  return null;
}

const SYMBOL = /^[A-Za-z0-9 &._:-]{1,40}$/;
const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w", "1M"] as const;

/** The rule's bar timeframe from what the picture showed ("15m", "15 min", "1H", "D"), else 15m. */
export function ruleTimeframe(read: string | null | undefined): string {
  const t = (read ?? "").trim().toLowerCase().replace(/\s+/g, "");
  const m = /^(\d{1,3})(m|min|h|hr|d|w)?$/.exec(t);
  if (t === "d" || t === "1d" || t === "day") return "1d";
  if (t === "w" || t === "1w" || t === "week") return "1w";
  if (m) {
    const unit = m[2] ?? "m";
    const tf = unit.startsWith("h") ? `${m[1]}h` : unit === "d" ? `${m[1]}d` : unit === "w" ? `${m[1]}w` : `${m[1]}m`;
    if ((TIMEFRAMES as readonly string[]).includes(tf)) return tf;
  }
  return "15m";
}

export interface AlertDraft { symbol: string; condition: string; timeframe: string; name: string }

export function draftErrors(d: AlertDraft): string[] {
  const out: string[] = [];
  if (!SYMBOL.test(d.symbol.trim())) out.push("Enter the symbol of this chart.");
  if (!d.condition.trim()) out.push(CANNOT_ALERT);
  return out;
}

/** The body for POST /api/alerts/rules. Never an order: an alert only reports that the condition matched. */
export function ruleBody(d: AlertDraft): AlertRuleInput {
  return {
    name: d.name.trim().slice(0, 120) || "Chart analysis alert", kind: "instrument", symbol: d.symbol.trim().toUpperCase(),
    condition: d.condition.trim(), base_tf: d.timeframe, priority: "normal", cooldown_minutes: 60, mode: "instant", digest_every: "hourly",
  };
}
