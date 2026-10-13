/** N1 F3: the portfolio risk view's types and pure helpers (kept out of the component so they are unit-tested). The
 * server computes every number (backend app/portfolio/risk.py); nothing here places or closes an order. */
import type { PortfolioT } from "./i18n";

export interface RiskBucket {
  bucket: string; positions: number; gross_notional: number; net_notional: number; open_risk: number; symbols: string[];
  gross_pct: number | null; risk_pct: number | null;
}

export interface BetaEntry { value: number; sessions: number; known: boolean; source: string }

export interface RiskLimits { max_sector_pct: number; max_correlated_risk_pct: number; max_open_positions: number }

export interface PortfolioRiskView {
  capital: number; positions: number;
  exposure: { gross: number; net: number; long: number; short: number; gross_pct: number | null; net_pct: number | null };
  open_risk: { total: number; pct: number | null };
  buckets: RiskBucket[];
  beta: { net: number; gross: number; net_pct: number | null; unknown: string[]; not_weighted: string[]; per_underlying: Record<string, BetaEntry> };
  direction: { bullish: number; bearish: number; neutral: number; warning: string | null };
  priced_at_entry: string[]; as_of: string; mode: string; capital_source: string; limits?: RiskLimits | null;
}

export function isRiskView(x: unknown): x is PortfolioRiskView {
  const v = x as PortfolioRiskView | null;
  return !!v && typeof v === "object" && typeof v.positions === "number" && Array.isArray(v.buckets) && typeof v.exposure === "object";
}

export const money = (v: number): string => v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
export const signed = (v: number): string => `${v >= 0 ? "+" : "-"}${money(Math.abs(v))}`;
export const pct = (v: number | null | undefined): string => (v === null || v === undefined ? "-" : `${v}%`);

/** A bucket's name in words: the index bucket and an unclassified name are spelled out. */
export function bucketLabel(bucket: string, t: PortfolioT): string {
  if (bucket === "INDEX") return t("buckets.index");
  if (bucket.startsWith("UNCLASSIFIED:")) return t("buckets.unclassified", { name: bucket.slice("UNCLASSIFIED:".length) });
  return bucket;
}

/** Which of the user's limits a bucket is at or above already (exposure, open risk). */
export function overLimit(row: RiskBucket, limits: RiskLimits | null | undefined): { sector: boolean; risk: boolean } {
  return {
    sector: !!limits && limits.max_sector_pct > 0 && (row.gross_pct ?? 0) >= limits.max_sector_pct,
    risk: !!limits && limits.max_correlated_risk_pct > 0 && (row.risk_pct ?? 0) >= limits.max_correlated_risk_pct,
  };
}

/** The user's limits as lines (none set -> one "no limits" line); null limits (caps off) -> no lines. */
export function limitLines(limits: RiskLimits | null | undefined, t: PortfolioT): string[] {
  if (!limits) return [];
  const out: string[] = [];
  if (limits.max_sector_pct > 0) out.push(t("limits.sector", { value: limits.max_sector_pct }));
  if (limits.max_correlated_risk_pct > 0) out.push(t("limits.risk", { value: limits.max_correlated_risk_pct }));
  if (limits.max_open_positions > 0) out.push(t("limits.positions", { value: limits.max_open_positions }));
  return out.length ? out : [t("limits.none")];
}
