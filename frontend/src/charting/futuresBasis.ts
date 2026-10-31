/**
 * N4 CH4c: an underlying's futures basis, annualised cost of carry, calendar spread and rollover per trade date, from the
 * shared daily history (`GET /api/charts/contracts/basis`). Flags `chart_futures_basis` + `chart_contracts`; off -> 503.
 * Every number is the server's (near month = the earliest live expiry, the expiry day included; basis = near close -
 * spot close; carry annualised over the calendar days to expiry; rollover = next + far OI as % of all OI). Reference
 * only - no signal, no order here.
 */
import { ApiError, request } from "../api/client";
import { formatNumber } from "../ui/format";

export interface MonthSeries { expiry: (string | null)[]; close: (number | null)[]; oi: (number | null)[] }
export interface BasisSeries {
  schema: "basis/1"; at: string; start: string; underlying: string; spot_key: string; days: string[]; times: number[];
  near: MonthSeries; next: MonthSeries; far: MonthSeries;
  spot: (number | null)[]; basis: (number | null)[]; basis_pct: (number | null)[]; carry_pct: (number | null)[];
  calendar: (number | null)[]; total_oi: (number | null)[]; rollover_pct: (number | null)[]; dte: number[];
  rolls: string[]; sources: string[]; note: string; reason?: string;
}

export const basisApi = {
  basis: (underlying: string, opts: { at?: string; start?: string } = {}) => {
    const q = new URLSearchParams({ underlying: underlying.trim().toUpperCase() });
    for (const [k, v] of Object.entries(opts)) if (v) q.set(k, v);
    return request<BasisSeries>(`/charts/contracts/basis?${q.toString()}`);
  },
};

/** The basis page of an underlying, in its own tab: `/?basis=<SYM>`. */
export function basisUrl(underlying: string): string {
  return `/?${new URLSearchParams({ basis: underlying.trim().toUpperCase() }).toString()}`;
}

/** "1.25 %", "−0.40 %", "n/a". */
export function pctText(v: number | null | undefined, digits = 2): string {
  return v == null || !Number.isFinite(v) ? "n/a" : `${formatNumber(v, digits)} %`;
}

/** The last day's reading, from the series (never computed here). */
export function latest(s: BasisSeries): { day: string; near: string | null; basis: number | null; basisPct: number | null; carryPct: number | null;
  calendar: number | null; rolloverPct: number | null; dte: number } | null {
  const i = s.days.length - 1;
  if (i < 0) return null;
  return { day: s.days[i], near: s.near.expiry[i], basis: s.basis[i], basisPct: s.basis_pct[i], carryPct: s.carry_pct[i],
           calendar: s.calendar[i], rolloverPct: s.rollover_pct[i], dte: s.dte[i] };
}

/** SVG polyline points of a series (gaps break the line into segments), scaled into width x height. */
export function linePaths(values: (number | null)[], width: number, height: number): string[] {
  const finite = values.filter((v): v is number => v != null && Number.isFinite(v));
  if (!finite.length) return [];
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  const span = hi - lo || 1;
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  const out: string[] = [];
  let cur: string[] = [];
  values.forEach((v, i) => {
    if (v == null || !Number.isFinite(v)) {
      if (cur.length) out.push(cur.join(" "));
      cur = [];
      return;
    }
    cur.push(`${(i * step).toFixed(1)},${(height - ((v - lo) / span) * height).toFixed(1)}`);
  });
  if (cur.length) out.push(cur.join(" "));
  return out;
}

/** Days the availability probe asks for: a few days of bars, not the page's default year (review LOW). */
export const PROBE_DAYS = 3;

/** Whether the basis page is on for this organisation (a 503 means a flag is off). A cheap probe: a `start` a few days
 *  before now, so the server reads a handful of keys and rows; the page itself asks for the full window. */
export async function basisAvailable(underlying: string, now: Date = new Date()): Promise<boolean> {
  try {
    await basisApi.basis(underlying, { start: new Date(now.getTime() - PROBE_DAYS * 86_400_000).toISOString() });
    return true;
  } catch (e) {
    return !(e instanceof ApiError && e.status === 503);
  }
}
