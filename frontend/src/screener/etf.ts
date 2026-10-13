/** S10 J: the ETF screen as the server sends it (GET /api/screener/etf; flag `screener_etf`, off by default). */
export interface EtfRow {
  symbol: string; name: string; underlying: string | null; session: string | null; close: number | null; traded_value: number | null;
  change_pct: number | null; index_change_pct: number | null; tracking_diff_pct: number | null; index_key: string | null; notes: string[];
}
export interface EtfResponse { sessions: number; total: number; shown: number; rows: EtfRow[]; note: string; source: string }
export const ETF_SORTS = ["traded_value", "change_pct", "tracking_diff_pct", "symbol"] as const;
export type EtfSort = (typeof ETF_SORTS)[number];
export const SORT_LABEL: Record<EtfSort, string> = {
  traded_value: "Traded value (high first)", change_pct: "Change (high first)", tracking_diff_pct: "Tracking difference (closest first)", symbol: "Symbol",
};

export function isEtfResponse(x: unknown): x is EtfResponse {
  const r = x as Partial<EtfResponse> | null;
  return !!r && Array.isArray(r.rows) && typeof r.note === "string";
}

/** Indian grouping in crores / lakhs for traded value ("12.3 Cr", "45.6 L"). */
export function inrShort(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "–";
  if (v >= 1e7) return `${(v / 1e7).toFixed(1)} Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(1)} L`;
  return v.toFixed(0);
}

export function pct(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "–";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function etfQuery(p: { sessions: number; q: string; minCr: number; maxTracking: number | null; sort: EtfSort }): string {
  const params = new URLSearchParams({ sessions: String(p.sessions), sort: p.sort, min_traded_value: String(Math.max(0, p.minCr) * 1e7) });
  if (p.q.trim()) params.set("q", p.q.trim());
  if (p.maxTracking !== null) params.set("max_abs_tracking", String(p.maxTracking));
  return params.toString();
}
