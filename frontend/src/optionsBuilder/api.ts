/** P1-c2: the builder endpoints (P1-c1). Research only - nothing here places an order. */
import { request } from "../api/client";
import type { Evaluation, Leg, TemplateInfo } from "./model";

export interface TemplateRequest {
  name: string; atm_strike: number; width: number; near_expiry: string; next_expiry?: string | null; lots: number;
  lot_size: number; spot: number; iv: number; as_of: string;
}
export interface EvaluateRequest { legs: Omit<Leg, "id" | "premium_source">[]; spot: number; as_of: string; days_forward: number; iv_shift: number; range_pct: number; points: number }

export const builderApi = {
  catalog: () => request<{ families: Record<string, string[]>; templates: Record<string, TemplateInfo> }>("/options-builder/catalog"),
  template: (body: TemplateRequest) =>
    request<{ name: string; legs: Omit<Leg, "id">[]; priced_by_model: boolean }>("/options-builder/template", { method: "POST", body: JSON.stringify(body) }),
  evaluate: (body: EvaluateRequest) => request<Evaluation>("/options-builder/evaluate", { method: "POST", body: JSON.stringify(body) }),
};
