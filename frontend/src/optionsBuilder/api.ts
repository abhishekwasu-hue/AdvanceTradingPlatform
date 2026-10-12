/** P1-c2 / Z5: the builder endpoints. Research only - nothing here places an order. */
import { request } from "../api/client";
import type { Basket, Evaluation, Leg, TemplateInfo } from "./model";

export interface TemplateRequest {
  name: string; atm_strike: number; width: number; near_expiry: string; next_expiry?: string | null; lots: number;
  lot_size: number; spot: number; iv: number; as_of: string;
}
type WireLeg = Omit<Leg, "id" | "premium_source" | "priced_for">;
/** N2-7: one adjustment's request (flag builder_adjustments) - the evaluate fields plus the adjustment. */
export interface AdjustRequest {
  legs: WireLeg[]; spot: number; as_of: string; range_pct?: number; points?: number;
  kind: "roll" | "add_hedge" | "iron_condor" | "close_side"; strike_step: number;
  index?: number; steps?: number; expiry?: string; width_steps?: number; side?: "CE" | "PE"; new_iv?: number;
}
export interface AdjustSide {
  legs: WireLeg[]; at_expiry: number[]; single_expiry: boolean;
  extremes: { max_profit: number | null; max_loss: number | null; unbounded_profit: boolean; unbounded_loss: boolean } | null;
  breakevens: number[]; pop: number | null; pop_basis: string; greeks: Record<"delta" | "gamma" | "theta" | "vega" | "rho", number>; net_premium: number;
}
export interface AdjustResult {
  kind: AdjustRequest["kind"]; description: string; realised: number; cash: number; prices: number[];
  changes: { action: "open" | "close"; leg: WireLeg; price: number; realised?: number }[];
  after_legs: WireLeg[]; before: AdjustSide; after: AdjustSide; greeks_change: AdjustSide["greeks"];
  placeable: false; why_not: string; disclaimer: string;
}
export interface EvaluateRequest {
  legs: WireLeg[]; spot: number; as_of: string; days_forward: number; iv_shift: number; range_pct: number; points: number;
  brokerage_per_order?: number;
}

/** What the instrument row held when a strategy was saved (each optional). */
export interface SavedInputs {
  spot?: number | null; step?: number | null; lot_size?: number | null; width?: number | null; base_iv?: number | null;
  near_expiry?: string | null; next_expiry?: string | null;
}
export interface SavedSummary { id: number; name: string; underlying: string; legs: number; created_by: number | null; updated_at: string | null }
export interface SavedStrategy extends Omit<SavedSummary, "legs"> { inputs: SavedInputs; legs: (WireLeg & { premium_source?: "model" | "manual" | null })[] }

/** The legs as the server takes them (the page's own fields left out). */
export function wireLegs(legs: Leg[]): WireLeg[] {
  return legs.map(({ id: _id, premium_source: _src, priced_for: _key, ...l }) => l);
}

export const builderApi = {
  catalog: () => request<{ families: Record<string, string[]>; templates: Record<string, TemplateInfo> }>("/options-builder/catalog"),
  template: (body: TemplateRequest) =>
    request<{ name: string; legs: Omit<Leg, "id">[]; priced_by_model: boolean }>("/options-builder/template", { method: "POST", body: JSON.stringify(body) }),
  evaluate: (body: EvaluateRequest) => request<Evaluation>("/options-builder/evaluate", { method: "POST", body: JSON.stringify(body) }),
  // N2-7: 404 while the flag builder_adjustments is off. Models only: nothing is placed, staged or proposed.
  adjust: (body: AdjustRequest) => request<AdjustResult>("/options-builder/adjust", { method: "POST", body: JSON.stringify(body) }),
  basket: (underlying: string, legs: Leg[]) =>
    request<Basket>("/options-builder/basket", { method: "POST", body: JSON.stringify({ underlying, legs: wireLegs(legs) }) }),
  // "Save strategy": 404 while the operator flag options_builder_saved is off
  saved: () => request<SavedSummary[]>("/options-builder/strategies"),
  save: (body: { name: string; underlying: string; inputs: SavedInputs; legs: (WireLeg & { premium_source?: "model" | "manual" })[] }) =>
    request<SavedSummary>("/options-builder/strategies", { method: "POST", body: JSON.stringify(body) }),
  load: (id: number) => request<SavedStrategy>(`/options-builder/strategies/${id}`),
  remove: (id: number) => request<{ id: number; archived: boolean }>(`/options-builder/strategies/${id}`, { method: "DELETE" }),
};
