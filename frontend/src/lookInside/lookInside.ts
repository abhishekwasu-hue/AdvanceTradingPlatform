/**
 * M95 PR-5 (owner order M95 F3): "Look inside without signing in" - one stored past market day of the two index
 * references, read from `GET /api/guest-demo` (no sign-in; 404 while the operator has it off or nothing is stored).
 * Every figure is a distance: the chart in % from the day's close (no price axis), strikes in steps from the money,
 * open interest in lakhs.
 */
import { request } from "../api/client";
import { formatNumber, formatPct } from "../ui/format";

export const LOOK_INSIDE_PATH = "/look-inside";

export interface GuestArea {
  role: "demand" | "supply" | string; kind: string; state: string; timeframe: string; touches: number;
  low_pct: number; high_pct: number; words: string;
}
export interface GuestChainRow { steps: number; call_oi_lakh: number | null; put_oi_lakh: number | null }
export interface GuestChain {
  expiry: string | null; rows: GuestChainRow[]; pcr: number | null;
  most_call_oi_steps: number | null; most_put_oi_steps: number | null;
}
export interface GuestToday {
  change_pct: number | null; range_position_pct: number | null; structure_daily: string; structure_weekly: string;
  direction_daily: string; direction_weekly: string; lines: string[];
}
export interface GuestIndex {
  name: string; today: GuestToday;
  chart: { timeframe: string; first_date: string; last_date: string; bars: [number, number, number, number][]; areas: GuestArea[] };
  chain: GuestChain | null; report: string[];
}
export interface GuestScreen { name: string; definition: string; timeframe: string; results: { name: string; matched: boolean; note: string | null }[] }
export interface GuestSnapshot { version: number; as_of: string; indices: GuestIndex[]; screens: GuestScreen[] }

let shared: Promise<GuestSnapshot | null> | null = null;

/** One read per page load, shared by the sign-in link and the page; null while the guest view is closed. */
export function guestSnapshot(): Promise<GuestSnapshot | null> {
  shared ??= request<GuestSnapshot>("/guest-demo").then((s) => (s && Array.isArray(s.indices) && s.indices.length ? s : null), () => null);
  return shared;
}

/** Tests: forget the shared read. */
export function resetGuestSnapshot(): void {
  shared = null;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-07-31" -> "31 Jul 2026" (the app's date form). */
export function dayText(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
  if (!m) return "";
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1] ?? ""} ${m[1]}`;
}

/** +0.82% / -1.20% / 0.00% */
export function pctText(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  return formatPct(v, 2);
}

/** The strike row's label: "ATM", "+3", "-2" (steps from the at-the-money strike). */
export function stepLabel(steps: number): string {
  return steps === 0 ? "ATM" : `${steps > 0 ? "+" : ""}${steps}`;
}

export function lakhText(v: number | null | undefined): string {
  return v === null || v === undefined ? "-" : `${formatNumber(v, 1)} L`;
}

/** The sticky bar's call to action: sign-up open or invite-only. */
export function signUpLabel(open: boolean): string {
  return open ? "Create your free account" : "Ask for an invite";
}

export const FOOTER_LINE = "Information and education only, not investment advice.";
