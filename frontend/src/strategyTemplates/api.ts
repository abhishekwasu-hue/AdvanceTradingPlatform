/** Strategy templates step 2 (flag `strategy_templates`; the server answers 404 while it is off): the template cards and
 * "Adopt as a paper strategy" - a PRE-FILL of the paper bot form. Adopting creates nothing; the user reviews and saves. */
import { request } from "../api/client";
import type { TemplateExitChoices } from "./exits";

export interface TemplateChoice { value: string; label: string }
export interface TemplateSetting {
  key: string; label: string; help: string; type: "int" | "float" | "enum" | "bool" | "timeframe" | "ma_type" | "source";
  default: number | string | boolean; min?: number | null; max?: number | null; step?: number | null; choices?: TemplateChoice[];
}
export interface StrategyTemplate {
  id: string; version: number; name: string; family: string; family_name: string; idea: string;
  difficulty: "Beginner" | "Intermediate" | "Advanced"; style: string;
  timeframes: TemplateChoice[]; default_timeframe: string | null; sides_default: "long" | "short" | "both";
  params: TemplateSetting[]; rules: string[]; available: boolean;
}
export interface TemplateAdoption {
  template_id: string; name: string; strategy_id: string;
  /** What the paper bot form takes over: the strategy, the candle timeframe and the holding. Always PAPER. */
  deployment: { strategy_id: string; timeframe: string; holding: "INTRADAY" | "SWING"; mode: "PAPER" };
  settings: { timeframe: string; sides: string; params: Record<string, number | string | boolean> };
  notes: string[];
  /** Section 5: the exit choices this template offers per instrument (absent from an older server: none shown). */
  exits?: TemplateExitChoices;
}
export interface AdoptRequest { timeframe?: string; sides?: "long" | "short" | "both"; params?: Record<string, number | string | boolean> }

export const templatesApi = {
  list: () => request<{ templates: StrategyTemplate[] }>("/strategy-templates"),
  adopt: (id: string, body: AdoptRequest = {}) =>
    request<TemplateAdoption>(`/strategy-templates/${encodeURIComponent(id)}/adopt`, { method: "POST", body: JSON.stringify(body) }),
};
