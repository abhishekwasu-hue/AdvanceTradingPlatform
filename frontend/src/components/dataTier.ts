import type { DataTierBadgeInfo } from "../types";

/** BT1: pure helpers for the data-tier badge (no React) - what every page shows next to a number when the
 * `byob_data_tier` flag is on: the tier ("Live · your Upstox" or "EOD · <date>") and its timestamp. */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10-09" -> "9 Oct 2026" (a calendar date, no timezone shift). */
export function formatEodDate(iso: string | null | undefined): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
  if (!m) return null;
  const month = MONTHS[Number(m[2]) - 1];
  return month ? `${Number(m[3])} ${month} ${m[1]}` : null;
}

/** An instant as IST wall-clock time "HH:MM:SS IST" (exchange time, whatever the viewer's timezone). */
export function formatIstTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const ist = new Date(t + 330 * 60_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(ist.getUTCHours())}:${pad(ist.getUTCMinutes())}:${pad(ist.getUTCSeconds())} IST`;
}

/** The badge text: "Live · your Upstox · 10:42:05 IST" or "EOD · 9 Oct 2026" ("EOD" alone before any file). */
export function tierText(badge: DataTierBadgeInfo): string {
  if (badge.tier === "A") {
    const when = formatIstTime(badge.as_of);
    return when ? `${badge.label} · ${when}` : badge.label;
  }
  const day = formatEodDate(badge.eod_date);
  return day ? `EOD · ${day}` : "EOD";
}

/** The hover text: why this tier, and why alerts are held back when the data is stale. */
export function tierTitle(badge: DataTierBadgeInfo): string {
  const parts: string[] = [];
  parts.push(badge.tier === "A"
    ? "Live data through your own broker session - shown only to you."
    : "End-of-day reference data (exchange files).");
  if (badge.reason) parts.push(badge.reason);
  if (badge.stale && badge.stale_reason) parts.push(`Stale: ${badge.stale_reason}. Alerts on this data are held back.`);
  if (badge.degraded) parts.push("Your session is at its symbol budget: some symbols are EOD and refresh is slower.");
  return parts.join(" ");
}

export type TierTone = "live" | "eod" | "stale";

export function tierTone(badge: DataTierBadgeInfo): TierTone {
  if (badge.stale) return "stale";
  return badge.tier === "A" ? "live" : "eod";
}
