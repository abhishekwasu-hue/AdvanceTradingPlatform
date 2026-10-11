/** P1-c2 / Z5: the builder endpoints. Research only - nothing here places an order. */
import { request } from "../api/client";
import type { Basket, Evaluation, Leg, TemplateInfo } from "./model";

export interface TemplateRequest {
  name: string; atm_strike: number; width: number; near_expiry: string; next_expiry?: string | null; lots: number;
  lot_size: number; spot: number; iv: number; as_of: string;
}
type WireLeg = Omit<Leg, "id" | "premium_source" | "priced_for">;
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
  basket: (underlying: string, legs: Leg[]) =>
    request<Basket>("/options-builder/basket", { method: "POST", body: JSON.stringify({ underlying, legs: wireLegs(legs) }) }),
  // "Save strategy": 404 while the operator flag options_builder_saved is off
  saved: () => request<SavedSummary[]>("/options-builder/strategies"),
  save: (body: { name: string; underlying: string; inputs: SavedInputs; legs: (WireLeg & { premium_source?: "model" | "manual" })[] }) =>
    request<SavedSummary>("/options-builder/strategies", { method: "POST", body: JSON.stringify(body) }),
  load: (id: number) => request<SavedStrategy>(`/options-builder/strategies/${id}`),
  remove: (id: number) => request<{ id: number; archived: boolean }>(`/options-builder/strategies/${id}`, { method: "DELETE" }),
};
