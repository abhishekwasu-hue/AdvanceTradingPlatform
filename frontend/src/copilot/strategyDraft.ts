import type { Condition, ConditionOperator, CustomStrategyConfig, IndicatorName, Operand } from "../types";

/**
 * Item 5 (owner's handover), job 6: the Copilot's DRAFT paper strategy and its rules on the page. The server
 * (backend app/ai/strategy_builder.py) builds the draft from the trader's own words with ATP's building blocks; a value
 * his words do not state comes as null and is listed in `asks`. The Trading bot page shows the draft in a review card: the
 * trader fills in every value himself, saves it as his own strategy (POST /custom-strategies, his click) and starts it
 * on paper with the page's existing "Start on paper". Nothing here fills in a number he did not give; nothing saves or
 * starts anything by itself (ADR-0006). Pure functions only, so every rule is unit-tested.
 */

export interface DraftOperand {
  type: "value" | "indicator";
  value?: number | null;
  indicator?: IndicatorName;
  period?: number | null;
  multiplier?: number | null;
  timeframe?: string | null;
  /** What a level belongs to ("RSI oversold"), for the ask's label. */
  of?: string;
}
export interface DraftRule {
  side: "long" | "short" | null;
  condition: { left: DraftOperand; operator: ConditionOperator | null; right: DraftOperand };
  text: string;
  words: string;
}
export interface DraftAsk { key: string; label: string }
export interface StrategyDraft {
  idea: string;
  name: string;
  symbol: string | null;
  timeframe: string | null;
  sides: ("long" | "short")[];
  rules: DraftRule[];
  settings: { stop_loss_atr_mult: number | null; atr_period: number | null; target_rr: [number, number] | null; min_rr: number | null };
  min_rr_source: "your words" | "your risk limits" | null;
  lots: number | null;
  asks: DraftAsk[];
  unsupported: string[];
  unread: string[];
  read_as: string[];
  ready_made: string[];
  complete: boolean;
  mode: "PAPER";
}

/** Where the Copilot hands the draft over: the Trading bot page, with the draft in the navigation state (never a URL). */
export const PAPER_BOT_PATH = "/deployments";
export interface DraftHandoff { copilotDraft: StrategyDraft }

/** The timeframes a saved strategy runs on (the server's list) and how the card names them. */
export const DRAFT_TIMEFRAMES: { value: string; label: string; chart: string }[] = [
  { value: "1min", label: "1 minute", chart: "1-minute" }, { value: "3min", label: "3 minutes", chart: "3-minute" },
  { value: "5min", label: "5 minutes", chart: "5-minute" }, { value: "15min", label: "15 minutes", chart: "15-minute" },
  { value: "30min", label: "30 minutes", chart: "30-minute" }, { value: "60min", label: "1 hour", chart: "1-hour" },
];
export type DraftSide = "long" | "short" | "both";

/** The trader's answers, by ask key. Text as typed (an empty string = not decided yet). */
export type DraftValues = Record<string, string>;

/** The draft from a navigation state, or null (anything else is ignored). */
export function draftFromState(state: unknown): StrategyDraft | null {
  const d = (state as Partial<DraftHandoff> | null)?.copilotDraft;
  if (!d || typeof d !== "object" || d.mode !== "PAPER" || !Array.isArray(d.rules) || d.rules.length === 0 || !Array.isArray(d.asks)) return null;
  return d;
}

/** The form's symbol for the draft's: the index names the Trading bot list uses; null when the idea named none. */
export function formSymbol(symbol: string | null): string | null {
  if (!symbol) return null;
  const s = symbol.trim().toUpperCase();
  return ({ NIFTY: "NIFTY 50", BANKNIFTY: "NIFTY BANK" } as Record<string, string>)[s] ?? s;
}

const ASK_RULE = /^rules\.(\d+)\.(left|right|operator)(?:\.(period|multiplier|value))?$/;

/** What each ask accepts, so the card can show the right field. */
export function askKind(key: string): "side" | "timeframe" | "direction" | "whole" | "number" | "pair" {
  if (key === "side") return "side";
  if (key === "timeframe") return "timeframe";
  if (key.endsWith(".operator")) return "direction";
  if (key === "target_rr") return "pair";
  if (key === "atr_period" || key.endsWith(".period")) return "whole";
  return "number";
}

function num(raw: string | undefined): number | null {
  const t = (raw ?? "").trim().replace(/,/g, "");
  if (!t || !/^\d+(\.\d+)?$/.test(t)) return null;
  const x = Number(t);
  return Number.isFinite(x) && x > 0 ? x : null;
}

/** "2" or "1.5, 3" -> the two objectives in R (one number: both the same); null when not a valid answer. */
export function targetPair(raw: string | undefined): [number, number] | null {
  const parts = (raw ?? "").split(/[,/ ]+|\bthen\b|\band\b/).map((p) => p.trim()).filter(Boolean);
  if (parts.length < 1 || parts.length > 2) return null;
  const a = num(parts[0]);
  const b = parts.length === 2 ? num(parts[1]) : a;
  return a !== null && b !== null && b >= a ? [a, b] : null;
}

/** The problem with one answer, in plain words, or null when it is fine. */
export function answerProblem(key: string, raw: string | undefined): string | null {
  const kind = askKind(key);
  const t = (raw ?? "").trim();
  if (!t) return "Still yours to decide.";
  if (kind === "side") return ["long", "short", "both"].includes(t) ? null : "Pick long, short or both.";
  if (kind === "timeframe") return DRAFT_TIMEFRAMES.some((x) => x.value === t) ? null : "Pick a timeframe.";
  if (kind === "direction") return t === "CROSSES_ABOVE" || t === "CROSSES_BELOW" ? null : "Pick above or below.";
  if (kind === "pair") return targetPair(t) ? null : "Write one number, or two (the second not below the first), for example 2 or 1.5, 3.";
  const x = num(t);
  if (x === null) return "Write a number above zero.";
  if (kind === "whole" && (!Number.isInteger(x) || x > 500)) return "Write a whole number up to 500.";
  return null;
}

/** The asks still open (empty or not valid), in the card's order. */
export function openAsks(draft: StrategyDraft, values: DraftValues): DraftAsk[] {
  return draft.asks.filter((a) => answerProblem(a.key, values[a.key]) !== null);
}

function operandFor(op: DraftOperand, ruleIndex: number, part: "left" | "right", values: DraftValues): Operand {
  const pick = (field: "period" | "multiplier" | "value", given: number | null | undefined): number | null => {
    const key = `rules.${ruleIndex}.${part}.${field}`;
    return key in values ? num(values[key]) : given ?? null;
  };
  // Fields the building block does not use keep the engine's own neutral values (they are never read for it).
  const out: Operand = {
    type: op.type, value: 0, indicator: op.indicator ?? "CLOSE", period: 14, multiplier: 3, timeframe: op.timeframe ?? null,
  };
  if (op.type === "value") out.value = pick("value", op.value) ?? 0;
  const period = pick("period", op.period);
  const multiplier = pick("multiplier", op.multiplier);
  if (period !== null) out.period = period;
  if (multiplier !== null) out.multiplier = multiplier;
  return out;
}

/**
 * The strategy the trader saves: the draft with his answers. null while any ask is open - the card's Save stays off,
 * so nothing is ever saved with a number he did not give.
 */
export function draftConfig(draft: StrategyDraft, values: DraftValues, name?: string): CustomStrategyConfig | null {
  if (openAsks(draft, values).length > 0) return null;
  const side = (values.side as DraftSide | undefined) ?? null;
  const longs: Condition[] = [];
  const shorts: Condition[] = [];
  draft.rules.forEach((r, i) => {
    const operator = (r.condition.operator ?? values[`rules.${i}.operator`]) as ConditionOperator;
    const condition: Condition = {
      left: operandFor(r.condition.left, i, "left", values), operator, right: operandFor(r.condition.right, i, "right", values),
    };
    const s = r.side ?? side;
    if (s === "long" || s === "both") longs.push(condition);
    if (s === "short" || s === "both") shorts.push(condition);
  });
  if (longs.length === 0 && shorts.length === 0) return null;
  const st = draft.settings;
  const stop = st.stop_loss_atr_mult ?? num(values.stop_loss_atr_mult);
  const atr = st.atr_period ?? num(values.atr_period);
  const target = st.target_rr ?? targetPair(values.target_rr);
  const minRr = "min_rr" in values && values.min_rr.trim() !== "" ? num(values.min_rr) : st.min_rr;
  const timeframe = draft.timeframe ?? values.timeframe;
  if (stop === null || atr === null || target === null || minRr === null || !timeframe) return null;
  return {
    name: (name ?? draft.name).trim() || draft.name, timeframe, long_conditions: longs, short_conditions: shorts,
    stop_loss_atr_mult: stop, atr_period: atr, target_rr: target, min_rr: minRr,
  };
}

/** What the card says under Save: what is still open, or that it is ready. */
export function saveLine(draft: StrategyDraft, values: DraftValues): string {
  const open = openAsks(draft, values).length;
  if (open === 0) return "Every value is set. Saving makes it your own strategy; nothing starts until you press Start on paper.";
  return open === 1 ? "One value is still yours to decide." : `${open} values are still yours to decide.`;
}
