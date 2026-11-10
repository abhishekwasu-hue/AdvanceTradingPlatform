/** Strategy templates step 2 (flag `strategy_templates`; the server answers 404 while it is off): the template cards and
 * "Adopt as a paper strategy" - a PRE-FILL of the trading bot form. Adopting creates nothing; the user reviews and saves. */
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
  /** The sentence a signal of this template says, per side, from the template's own data (null: none for that side). Absent from an older server. */
  reads?: { long: string | null; short: string | null };
  /** M94 (flag intraday_pack): the defaults are reference values from a study - shown as REFERENCE_LABEL. Absent from an older server. */
  reference_values?: boolean;
}

/** M94: the label of a template whose defaults are reference values (a label only: every value stays the user's own). */
export const REFERENCE_LABEL = "Reference values - edit before use";

/** M94 (flag intraday_pack): whether a template shows the reference-values label. */
export const showsReferenceLabel = (t: Pick<StrategyTemplate, "reference_values">, intradayPackOn: boolean): boolean =>
  intradayPackOn && t.reference_values === true;
export interface TemplateAdoption {
  template_id: string; name: string; strategy_id: string;
  /** What the trading bot form takes over: the strategy, the candle timeframe and the holding. Always PAPER. */
  deployment: { strategy_id: string; timeframe: string; holding: "INTRADAY" | "SWING"; mode: "PAPER" };
  settings: { timeframe: string; sides: string; params: Record<string, number | string | boolean> };
  notes: string[];
  /** Section 5: the exit choices this template offers per instrument (absent from an older server: none shown). */
  exits?: TemplateExitChoices;
}
export interface AdoptRequest { timeframe?: string; sides?: "long" | "short" | "both"; params?: Record<string, number | string | boolean> }

/** M94 F15 (flag intraday_pack, family H): one line of a template's "how it works" screen, from its own data. */
export interface HowLine { key: "idea" | "reads" | "wrong" | "runs" | "exits"; label: string; text: string }
/** The schematic: one signal of the template on a synthetic walk, prices rescaled to 0..scale (no real market, no price). */
export interface Schematic {
  timeframe: string; timeframe_words: string; side: "long" | "short";
  /** [open, high, low, close] per bar, 0..scale */
  candles: [number, number, number, number][];
  setup: number; trigger: number; invalidation: number; scale: number; same_bar: boolean; seed: number;
}
export interface ScannerSource { text: string; params: Record<string, number | string | boolean>; base_tf: string }
export interface HowItWorks {
  template: StrategyTemplate; lines: HowLine[]; schematic: Schematic | null;
  scanner: { long: ScannerSource | null; short: ScannerSource | null };
  reference_label: string | null; window_note: string; footer: string;
}
/** M94 F16: one compiled part of one side, read through the shared screen evaluation (M91 C2). */
export interface OverlayRow { id: string; block: string; text: string; state: "met" | "failed" | "not_needed" | "unknown"; value: string }
export interface OverlayPart {
  part: "setup" | "trigger" | "invalidation"; side: "long" | "short"; label: string;
  /** true: the engine keeps this invalidation from the setup bar (no condition to read); its level shows at each signal */
  kept: boolean; matched: boolean | null; reason: string | null; rows: OverlayRow[]; hits: string[];
}
export interface OverlaySignal { bar: string; setup_bar: string; side: "long" | "short"; invalidation: number | null }
export interface TemplateOverlay {
  template: { id: string; name: string }; symbol: string; exchange: string; base_tf: string; data_source?: string; footer: string;
  available: boolean; reason?: string | null; parts?: OverlayPart[]; signals?: OverlaySignal[]; as_of?: string;
}
export interface OverlayRequest { symbol: string; exchange?: string; timeframe?: string; sides?: "long" | "short" | "both"; params?: Record<string, number | string | boolean> }

/** M94 (Q10): the order's sentence, exactly, on every M94 surface. */
export const M94_FOOTER = "Information and education only, not investment advice.";

export const templatesApi = {
  list: () => request<{ templates: StrategyTemplate[] }>("/strategy-templates"),
  howItWorks: (id: string) => request<HowItWorks>(`/strategy-templates/${encodeURIComponent(id)}/how-it-works`),
  overlay: (id: string, body: OverlayRequest) =>
    request<TemplateOverlay>(`/strategy-templates/${encodeURIComponent(id)}/overlay`, { method: "POST", body: JSON.stringify(body) }),
  adopt: (id: string, body: AdoptRequest = {}) =>
    request<TemplateAdoption>(`/strategy-templates/${encodeURIComponent(id)}/adopt`, { method: "POST", body: JSON.stringify(body) }),
};
