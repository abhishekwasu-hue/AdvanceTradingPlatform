/**
 * Strategy templates section 5 ("exits as settings", docs/design/STRATEGY_TEMPLATES_IMPL.md): the template's exits as
 * the user's own choice in the v2 Trading bot form - where the trade's stop comes from and when to take profit, in plain
 * words, pre-filled with the template's defaults (the template's own level; no fixed target). The server sends what
 * fits each instrument (`TemplateAdoption.exits`) and checks the choice again. Pure functions only (unit-tested).
 *
 * A choice equal to the default sends nothing, so the bot is stored exactly as before this feature. The signal text never
 * carries any of it: the levels belong to the order and position records.
 */
import type { DeploymentCreateRequest, TemplateExitsRequest } from "../types";

export type { TemplateExitsRequest };
export type ExitClass = "UNDERLYING" | "FUTURE" | "OPTION_SINGLE" | "OPTION_STRUCTURE";
export type StopSource = NonNullable<TemplateExitsRequest["stop_source"]>;
export type TargetRule = NonNullable<TemplateExitsRequest["target_rule"]>;

export interface TemplateExitChoices {
  /** Per instrument: the stop sources and target rules this template offers; the first of each is the default. */
  precedence: Record<ExitClass, { stops: StopSource[]; targets: TargetRule[] }>;
  exit_signal: boolean;
  wave_projection: boolean;
  base_waves: { value: string; label: string }[];
  target_r: { min: number; max: number };
  ratio: { min: number; max: number };
}

/** What the form holds (numbers as typed text until they are checked). */
export interface ExitPick {
  stop: StopSource | "";
  target: TargetRule | "";
  targetR: string;
  baseWave: string;
  ratio: string;
}

export const EMPTY_PICK: ExitPick = { stop: "", target: "", targetR: "", baseWave: "", ratio: "" };

export const STOP_WORDS: Record<StopSource, string> = {
  TEMPLATE_INVALIDATION: "Where the idea is wrong",
  PREMIUM_PCT: "Only my premium stop",
  CREDIT_PCT: "My spread's stop % of the credit",
};

export const TARGET_WORDS: Record<TargetRule, string> = {
  NONE: "No fixed target",
  R_MULTIPLE: "At a multiple of my risk",
  TEMPLATE_EXIT: "When the template says the idea has ended",
  WAVE_PROJECTION: "Where the wave projection points",
  CREDIT_PCT: "My spread's target % of the credit",
};

/** The instrument the bot will trade, as the server's precedence table names it. */
export function exitClass(request: Pick<DeploymentCreateRequest, "instrument_kind" | "option_strategy">): ExitClass {
  if (request.instrument_kind === "OPTION") return !request.option_strategy || request.option_strategy === "SINGLE" ? "OPTION_SINGLE" : "OPTION_STRUCTURE";
  return request.instrument_kind === "FUTURE" ? "FUTURE" : "UNDERLYING";
}

export function stopOptions(choices: TemplateExitChoices, cls: ExitClass) {
  return choices.precedence[cls].stops.map((value) => ({ value, label: STOP_WORDS[value] }));
}

export function targetOptions(choices: TemplateExitChoices, cls: ExitClass) {
  return choices.precedence[cls].targets.map((value) => ({ value, label: TARGET_WORDS[value] }));
}

/** The pick in force for this instrument: what the user chose when it is offered there, else the template's default. */
export function effectivePick(pick: ExitPick, choices: TemplateExitChoices, cls: ExitClass): ExitPick {
  const { stops, targets } = choices.precedence[cls];
  return {
    ...pick,
    stop: pick.stop && stops.includes(pick.stop) ? pick.stop : stops[0],
    target: pick.target && targets.includes(pick.target) ? pick.target : targets[0],
  };
}

function num(text: string): number | null {
  const t = text.trim();
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : null;
}

/** A plain message when the pick cannot be sent yet (a number missing or out of its range), else null. */
export function exitProblem(pick: ExitPick, choices: TemplateExitChoices): string | null {
  if (pick.target === "R_MULTIPLE") {
    const r = num(pick.targetR);
    const { min, max } = choices.target_r;
    if (r === null || r < min || r > max) return `Type how many times your risk to take profit at (${min} to ${max}).`;
  }
  if (pick.target === "WAVE_PROJECTION") {
    if (!choices.base_waves.some((w) => w.value === pick.baseWave)) return "Pick the wave the projection is measured on.";
    const r = num(pick.ratio);
    const { min, max } = choices.ratio;
    if (r === null || r <= min || r > max) return `Type the projection's size as a share of that wave (above ${min}, up to ${max}).`;
  }
  return null;
}

/** The request's `template_exits`: null when the pick is the default for this instrument (stored as before). */
export function templateExitsBody(pick: ExitPick, choices: TemplateExitChoices, cls: ExitClass): TemplateExitsRequest | null {
  const p = effectivePick(pick, choices, cls);
  const { stops, targets } = choices.precedence[cls];
  const body: TemplateExitsRequest = {};
  if (p.stop !== stops[0]) body.stop_source = p.stop as StopSource;
  if (p.target !== targets[0]) body.target_rule = p.target as TargetRule;
  if (p.target === "R_MULTIPLE") body.target_r = num(p.targetR);
  if (p.target === "WAVE_PROJECTION") {
    body.target_base_wave = p.baseWave;
    body.target_ratio = num(p.ratio);
  }
  return Object.keys(body).length ? body : null;
}

/** The short notes under the choice (what else still applies). */
export function exitNotes(pick: ExitPick, cls: ExitClass): string[] {
  const notes: string[] = [];
  if (pick.target === "R_MULTIPLE" || pick.target === "WAVE_PROJECTION") {
    notes.push("Your minimum reward to risk in your risk settings still applies.");
  }
  if (pick.target === "WAVE_PROJECTION") notes.push("When the count shows no projection at the signal, the trade has no fixed target.");
  if (pick.target === "TEMPLATE_EXIT") notes.push("The trade closes at the next price after the bar where the idea ended.");
  if (cls === "OPTION_SINGLE") notes.push("Your premium stop always stays as a safety net.");
  if (cls === "OPTION_STRUCTURE") notes.push("Your spread's own target and stop % always stay as set.");
  notes.push("Trailing, break-even and time exits work as before.");
  return notes;
}
