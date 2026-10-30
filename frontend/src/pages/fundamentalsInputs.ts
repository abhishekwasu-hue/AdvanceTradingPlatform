import type { DCFAssumptions } from "../types/fundamentals";

/**
 * REV-1 M2 (Company research): the DCF inputs start empty - no made-up sample numbers. A calculation runs only when
 * every figure it needs is his own.
 */
export type DcfKey = "base_revenue" | "revenue_growth_pct" | "ebitda_margin_pct" | "tax_rate_pct" | "capex_pct_of_revenue"
  | "working_capital_pct_of_revenue" | "wacc_pct" | "terminal_growth_pct" | "net_debt" | "shares_outstanding";
export type DcfInputs = Record<DcfKey, number | null>;

export const DCF_FIELDS: readonly (readonly [DcfKey, string])[] = [
  ["base_revenue", "Base revenue"],
  ["revenue_growth_pct", "Revenue growth % a year"],
  ["ebitda_margin_pct", "EBITDA margin %"],
  ["tax_rate_pct", "Tax rate %"],
  ["capex_pct_of_revenue", "Capex % of revenue"],
  ["working_capital_pct_of_revenue", "Working capital % of revenue"],
  ["wacc_pct", "WACC %"],
  ["terminal_growth_pct", "Terminal growth %"],
  ["net_debt", "Net debt"],
  ["shares_outstanding", "Shares outstanding"],
];

/** The five projection years the calculator uses; his one growth figure applies to each. */
export const DCF_YEARS = 5;

export const EMPTY_DCF: DcfInputs = Object.fromEntries(DCF_FIELDS.map(([k]) => [k, null])) as DcfInputs;

/** A typed number, or null for an empty or non-numeric box (never 0 for an empty box). */
export function numberOrNull(raw: string): number | null {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** The request body, or null while any figure is still empty. */
export function dcfAssumptionsFrom(inputs: DcfInputs): DCFAssumptions | null {
  if (DCF_FIELDS.some(([k]) => inputs[k] === null)) return null;
  const v = inputs as Record<DcfKey, number>;
  return {
    base_revenue: v.base_revenue,
    revenue_growth_pct: Array.from({ length: DCF_YEARS }, () => v.revenue_growth_pct),
    ebitda_margin_pct: v.ebitda_margin_pct,
    tax_rate_pct: v.tax_rate_pct,
    capex_pct_of_revenue: v.capex_pct_of_revenue,
    working_capital_pct_of_revenue: v.working_capital_pct_of_revenue,
    wacc_pct: v.wacc_pct,
    terminal_growth_pct: v.terminal_growth_pct,
    net_debt: v.net_debt,
    shares_outstanding: v.shares_outstanding,
  };
}
