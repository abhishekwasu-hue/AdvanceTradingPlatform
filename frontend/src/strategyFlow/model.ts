import type { StrategyDraft, DraftOperand } from "../copilot/strategyDraft";
import { INDICATOR_LABEL } from "../ui/strategyLabels";
import type {
  BacktestResult, Condition, ConditionOperator, ContractRules, CustomStrategyConfig, ExpiryRule, IndicatorName, OptionBacktestConfig,
  Operand, StrikeRule, Trade,
} from "../types";

/**
 * SB-UX SU1-SU6 (owner order ATP_ORDER_CP_SB_UX, behind `ui_v2`): the stepped strategy builder - Start, Market, Entry,
 * Exit & risk, Result, then "Start on paper". One primary action per step. Pure functions only, so every rule is unit-tested.
 *
 * Rules this file keeps:
 * - DEF-0: every number is the trader's own. A blank box is off (stop, target, minimum R:R); a value the engine needs
 *   (an indicator length, a level, the strikes, the expiry) stays empty until he gives it, and the step cannot go on.
 * - Nothing here places or starts anything: the last step hands the saved strategy to the Paper bot form, where he
 *   presses Start on paper himself (PAPER only, ADR-0006).
 * - Only blocks the engine can evaluate are selectable; the price-action blocks it cannot evaluate yet are listed as
 *   "coming soon" and never sent.
 */

export type FlowSide = "long" | "short" | "both";
export type StepKey = "start" | "market" | "entry" | "exit" | "result";
export const STEPS: { key: StepKey; label: string }[] = [
  { key: "start", label: "Start" }, { key: "market", label: "Market" }, { key: "entry", label: "Entry" },
  { key: "exit", label: "Exit & risk" }, { key: "result", label: "Result" },
];

// --- Step 1: the template cards -------------------------------------------------------------------------------------

export type Structure = "UNDERLYING" | "SINGLE_BUY" | "SINGLE_WRITE" | "BULL_PUT_SPREAD" | "BEAR_CALL_SPREAD" | "IRON_CONDOR";

export interface StartCard {
  key: string;
  title: string;
  line: string;
  /** "custom": the no-code engine (entry blocks you choose); "template": a ready strategy template (its own rules); "soon": not built yet. */
  engine: "custom" | "template" | "soon";
  structure?: Structure;
  side?: FlowSide;
  templateId?: string;
}

export const START_CARDS: StartCard[] = [
  { key: "bull-put", title: "Bull put spread", line: "Sell a put spread when your long entry fires.", engine: "custom", structure: "BULL_PUT_SPREAD", side: "long" },
  { key: "bear-call", title: "Bear call spread", line: "Sell a call spread when your short entry fires.", engine: "custom", structure: "BEAR_CALL_SPREAD", side: "short" },
  { key: "iron-condor", title: "Iron condor", line: "Sell both sides when your entry fires.", engine: "custom", structure: "IRON_CONDOR", side: "both" },
  { key: "pullback-area", title: "Pullback to a demand / supply area", line: "Waits for area blocks in the builder.", engine: "soon" },
  { key: "breakout-retest", title: "Breakout and retest", line: "A level breaks, the price comes back to it and holds.", engine: "template", templateId: "A5" },
  { key: "own", title: "My own rules", line: "Start empty and add your entry blocks.", engine: "custom" },
];

// --- Step 2: market -------------------------------------------------------------------------------------------------

export const SYMBOL_CHOICES = ["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"] as const;
export const SYMBOL_LABEL: Record<string, string> = { NIFTY: "NIFTY 50", BANKNIFTY: "NIFTY BANK", SENSEX: "SENSEX", FINNIFTY: "FINNIFTY" };
/** The timeframes a no-code strategy runs on; `soon` ones are shown but not selectable (no engine path yet). */
export const FLOW_TIMEFRAMES: { value: string; label: string; soon?: boolean }[] = [
  { value: "1min", label: "1m" }, { value: "5min", label: "5m" }, { value: "15min", label: "15m" }, { value: "30min", label: "30m" },
  { value: "60min", label: "1h" }, { value: "day", label: "1D" }, { value: "week", label: "1W", soon: true },
];
export const TIMEFRAME_WORDS: Record<string, string> = {
  "1min": "1-minute", "3min": "3-minute", "5min": "5-minute", "15min": "15-minute", "30min": "30-minute", "60min": "1-hour", day: "Daily",
  "1m": "1-minute", "5m": "5-minute", "15m": "15-minute", "30m": "30-minute", "1h": "1-hour", "1d": "Daily",
};

/** The Paper bot form's name for a symbol (its index list uses the exchange names). */
export function paperSymbol(symbol: string): string {
  const s = symbol.trim().toUpperCase();
  return ({ NIFTY: "NIFTY 50", BANKNIFTY: "NIFTY BANK" } as Record<string, string>)[s] ?? s;
}

// --- Step 3: entry blocks --------------------------------------------------------------------------------------------

export interface FlowOperand {
  type: "value" | "indicator";
  value: number | null;
  indicator: IndicatorName;
  period: number | null;
  multiplier: number | null;
  timeframe: string | null;
}
export interface FlowBlock { side: "long" | "short"; left: FlowOperand; operator: ConditionOperator | null; right: FlowOperand }

export const NEEDS_PERIOD: ReadonlySet<string> = new Set(["EMA", "SMA", "RSI", "ADX", "PLUS_DI", "MINUS_DI", "ATR", "SUPERTREND",
  "BB_UPPER", "BB_MID", "BB_LOWER", "VOLUME_SMA", "OR_HIGH", "OR_LOW"]);
export const NEEDS_MULTIPLIER: ReadonlySet<string> = new Set(["SUPERTREND", "BB_UPPER", "BB_MID", "BB_LOWER"]);

/** Plain names for the engine's indicators (no raw ids on the page): the one shared map (src/ui/strategyLabels.ts). */
export { INDICATOR_LABEL };
export const INDICATORS = Object.keys(INDICATOR_LABEL) as IndicatorName[];

export const OPERATOR_WORDS: Record<ConditionOperator, string> = {
  GT: "is above", LT: "is below", GTE: "is at or above", LTE: "is at or below", CROSSES_ABOVE: "crosses above", CROSSES_BELOW: "crosses below",
};
export const OPERATORS = Object.keys(OPERATOR_WORDS) as ConditionOperator[];

const ind = (indicator: IndicatorName): FlowOperand => ({ type: "indicator", value: null, indicator, period: null, multiplier: null, timeframe: null });
const num = (): FlowOperand => ({ type: "value", value: null, indicator: "CLOSE", period: null, multiplier: null, timeframe: null });

/** The blocks the engine evaluates today, as starting points: every number is left empty for the trader. */
export interface BlockPreset { key: string; label: string; make: (side: "long" | "short") => FlowBlock }
const up = (side: "long" | "short", a: ConditionOperator, b: ConditionOperator) => (side === "long" ? a : b);
export const BLOCK_PRESETS: BlockPreset[] = [
  { key: "price-ma", label: "Price vs moving average", make: (side) => ({ side, left: ind("CLOSE"), operator: up(side, "GT", "LT"), right: ind("EMA") }) },
  { key: "ma-cross", label: "Moving averages cross", make: (side) => ({ side, left: ind("EMA"), operator: up(side, "CROSSES_ABOVE", "CROSSES_BELOW"), right: ind("EMA") }) },
  { key: "rsi-level", label: "RSI crosses a level", make: (side) => ({ side, left: ind("RSI"), operator: up(side, "CROSSES_ABOVE", "CROSSES_BELOW"), right: num() }) },
  { key: "supertrend", label: "Price vs Supertrend", make: (side) => ({ side, left: ind("CLOSE"), operator: up(side, "GT", "LT"), right: ind("SUPERTREND") }) },
  { key: "vwap", label: "Price vs VWAP", make: (side) => ({ side, left: ind("CLOSE"), operator: up(side, "GT", "LT"), right: ind("VWAP") }) },
  { key: "gap", label: "Opening gap", make: (side) => ({ side, left: ind("DAY_OPEN"), operator: up(side, "GT", "LT"), right: ind(side === "long" ? "PDH" : "PDL") }) },
  { key: "any", label: "Any indicator", make: (side) => ({ side, left: ind("CLOSE"), operator: null, right: num() }) },
];

/** The price-action blocks the no-code engine cannot evaluate yet: shown, never selectable, never sent. */
export const SOON_BLOCKS: string[] = [
  "Area (demand / supply; fresh, tested or flipped)", "Pullback into an area", "Candle pattern at an area (engulfing, pin, tweezer, star)",
  "Lower-timeframe change of character", "RSI divergence (regular)", "Trendline touch",
];

function fmt(n: number | null): string {
  return n === null ? "?" : String(n);
}

/** One operand in words: "EMA (20)", "Supertrend (10, 3)", "60", "EMA (?)" while a value is missing. */
export function operandWords(op: FlowOperand): string {
  if (op.type === "value") return fmt(op.value);
  const parts: string[] = [];
  if (NEEDS_PERIOD.has(op.indicator)) parts.push(op.indicator === "OR_HIGH" || op.indicator === "OR_LOW" ? `${fmt(op.period)} min` : fmt(op.period));
  if (NEEDS_MULTIPLIER.has(op.indicator)) parts.push(fmt(op.multiplier));
  const tf = op.timeframe ? ` on ${TIMEFRAME_WORDS[op.timeframe] ?? op.timeframe}` : "";
  return `${INDICATOR_LABEL[op.indicator]}${parts.length ? ` (${parts.join(", ")})` : ""}${tf}`;
}

/** The chip's text: "Close is above EMA (20)". */
export function blockWords(b: FlowBlock): string {
  return `${operandWords(b.left)} ${b.operator ? OPERATOR_WORDS[b.operator] : "? (pick the comparison)"} ${operandWords(b.right)}`;
}

function operandMissing(op: FlowOperand): boolean {
  if (op.type === "value") return op.value === null || !Number.isFinite(op.value);
  if (NEEDS_PERIOD.has(op.indicator) && !(op.period !== null && Number.isInteger(op.period) && op.period > 0 && op.period <= 500)) return true;
  return NEEDS_MULTIPLIER.has(op.indicator) && !(op.multiplier !== null && op.multiplier > 0);
}

/** True while the block still needs a value from the trader. */
export function blockOpen(b: FlowBlock): boolean {
  return b.operator === null || operandMissing(b.left) || operandMissing(b.right);
}

/** The draft's operand (null = a value he did not state) as a block operand. */
export function fromDraftOperand(op: DraftOperand): FlowOperand {
  return {
    type: op.type, value: op.type === "value" ? op.value ?? null : null, indicator: op.indicator ?? "CLOSE", period: op.period ?? null,
    multiplier: op.multiplier ?? null, timeframe: op.timeframe ?? null,
  };
}

/** The engine's operand. Fields the block does not use keep the engine's neutral values (never read for it). */
function toOperand(op: FlowOperand): Operand {
  return { type: op.type, value: op.type === "value" ? op.value ?? 0 : 0, indicator: op.indicator, period: op.period ?? 14, multiplier: op.multiplier ?? 3, timeframe: op.timeframe };
}

// --- Step 4: exit & risk ---------------------------------------------------------------------------------------------

export interface FlowOptions {
  structure: Structure | null;
  strikeRule: StrikeRule | null;
  strikeOffset: string;
  width: string;
  expiry: ExpiryRule | null;
  lots: string;
}
export const EMPTY_OPTIONS: FlowOptions = { structure: null, strikeRule: null, strikeOffset: "", width: "", expiry: null, lots: "" };

export const STRUCTURES: { key: Structure; label: string; hint: string }[] = [
  { key: "UNDERLYING", label: "The index or stock itself", hint: "No options" },
  { key: "SINGLE_BUY", label: "Buy an option", hint: "Call on long, put on short" },
  { key: "SINGLE_WRITE", label: "Sell an option", hint: "Put on long, call on short" },
  { key: "BULL_PUT_SPREAD", label: "Bull put spread", hint: "On a long entry" },
  { key: "BEAR_CALL_SPREAD", label: "Bear call spread", hint: "On a short entry" },
  { key: "IRON_CONDOR", label: "Iron condor", hint: "Both sides" },
];
export const EXPIRIES: { key: ExpiryRule; label: string; hint: string }[] = [
  { key: "NEAREST", label: "Nearest", hint: "The first one listed" },
  { key: "ROLL", label: "Next weekly on expiry day", hint: "Nearest; on expiry day the next one" },
  { key: "NEXT", label: "Next", hint: "The one after the nearest" },
  { key: "MONTHLY", label: "Monthly", hint: "The month's last expiry" },
];
export const STRIKES: { key: StrikeRule; label: string; hint: string }[] = [
  { key: "ATM", label: "At the money", hint: "Nearest the price" },
  { key: "OTM", label: "Out of the money", hint: "Steps away from the price" },
  { key: "ITM", label: "In the money", hint: "Steps into the price" },
];

export const isOption = (s: Structure | null): boolean => s !== null && s !== "UNDERLYING";
export const isSpread = (s: Structure | null): boolean => s === "BULL_PUT_SPREAD" || s === "BEAR_CALL_SPREAD" || s === "IRON_CONDOR";

/** The trader's own number in a box: blank = null (off); anything not a number = NaN (a problem to show). */
export function ownValue(raw: string): number | null {
  const t = raw.trim().replace(/,/g, "");
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : Number.NaN;
}
const whole = (raw: string, max: number) => {
  const n = ownValue(raw);
  return n !== null && Number.isInteger(n) && n >= 1 && n <= max ? n : null;
};

export interface FlowExits { stop: string; atrPeriod: string; target1: string; target2: string; minRr: string }
export const EMPTY_EXITS: FlowExits = { stop: "", atrPeriod: "", target1: "", target2: "", minRr: "" };

/** What still blocks the Exit & risk step, in plain words (empty = it can go on). A blank exit box is fine: it is off. */
export function exitProblems(exits: FlowExits, options: FlowOptions, engine: "custom" | "template"): string[] {
  const out: string[] = [];
  if (engine === "custom") {
    const stop = ownValue(exits.stop);
    const atr = ownValue(exits.atrPeriod);
    const t1 = ownValue(exits.target1);
    const t2 = ownValue(exits.target2);
    const minRr = ownValue(exits.minRr);
    for (const [v, what] of [[stop, "The stop"], [t1, "Target 1"], [t2, "Target 2"], [minRr, "The minimum R:R"]] as [number | null, string][]) {
      if (v !== null && !(v > 0)) out.push(`${what} must be a number above zero, or blank (off).`);
    }
    if (stop !== null && whole(exits.atrPeriod, 500) === null) out.push("Your stop is in ATRs: write the ATR length (a whole number) under Advanced.");
    if (stop === null && atr !== null) out.push("The ATR length is only used with a stop: set a stop, or leave both blank.");
    if ((t1 !== null || t2 !== null) && stop === null) out.push("A target in R needs your stop-loss: set a stop, or leave the target blank.");
    if (t1 !== null && t2 !== null && t2 < t1) out.push("Target 2 cannot be below target 1.");
    if (minRr !== null && t1 === null && t2 === null) out.push("A minimum R:R needs your target: set a target, or leave the minimum blank.");
  }
  if (options.structure === null) out.push("Pick what to trade.");
  if (isOption(options.structure)) {
    if (options.strikeRule === null) out.push("Pick your strikes.");
    if (options.strikeRule && options.strikeRule !== "ATM" && whole(options.strikeOffset, 10) === null) out.push("Write how many strikes away (1 to 10).");
    if (isSpread(options.structure) && whole(options.width, 20) === null) out.push("Write the spread width in strikes (1 to 20).");
    if (options.expiry === null) out.push("Pick the expiry.");
  }
  if (options.lots.trim() !== "" && whole(options.lots, 500) === null) out.push("Lots must be a whole number from 1, or blank.");
  return out;
}

// --- the whole flow -------------------------------------------------------------------------------------------------

export interface FlowState {
  card: string | null;
  engine: "custom" | "template";
  templateId: string | null;
  name: string;
  symbol: string;
  timeframe: string | null;
  side: FlowSide | null;
  blocks: FlowBlock[];
  exits: FlowExits;
  options: FlowOptions;
}

export function emptyFlow(): FlowState {
  return { card: null, engine: "custom", templateId: null, name: "", symbol: "", timeframe: null, side: null, blocks: [], exits: { ...EMPTY_EXITS },
    options: { ...EMPTY_OPTIONS } };
}

/** A card picked on Start: its structure and side are what the card names; nothing else is filled in. */
export function withCard(flow: FlowState, card: StartCard): FlowState {
  if (card.engine === "soon") return flow;
  return {
    ...flow, card: card.key, engine: card.engine, templateId: card.templateId ?? null, name: flow.name || card.title,
    side: card.side ?? flow.side, options: card.structure ? { ...flow.options, structure: card.structure } : flow.options,
  };
}

/** The plain-words draft (the Copilot's parser) loaded into the flow: what the words state, and nothing more. */
export function withDraft(flow: FlowState, draft: StrategyDraft): FlowState {
  const sides = draft.sides;
  const side: FlowSide | null = sides.length === 2 ? "both" : sides.length === 1 ? sides[0] : flow.side;
  const blocks: FlowBlock[] = [];
  for (const r of draft.rules) {
    const make = (s: "long" | "short"): FlowBlock => ({ side: s, left: fromDraftOperand(r.condition.left), operator: r.condition.operator, right: fromDraftOperand(r.condition.right) });
    const s = r.side ?? (side === "both" ? null : side);
    if (s) blocks.push(make(s));
    else { blocks.push(make("long")); blocks.push(make("short")); }
  }
  const st = draft.settings;
  const sym = (draft.symbol ?? "").toUpperCase();
  const timeframe = draft.timeframe && FLOW_TIMEFRAMES.some((t) => t.value === draft.timeframe && !t.soon) ? draft.timeframe : flow.timeframe;
  return {
    ...flow, card: "words", engine: "custom", templateId: null, name: draft.name || flow.name, symbol: sym || flow.symbol, timeframe,
    side: side ?? (blocks.length ? null : flow.side), blocks,
    exits: {
      stop: st.stop_loss_atr_mult === null ? "" : String(st.stop_loss_atr_mult), atrPeriod: st.atr_period === null ? "" : String(st.atr_period),
      target1: st.target_rr ? String(st.target_rr[0]) : "", target2: st.target_rr && st.target_rr[1] !== st.target_rr[0] ? String(st.target_rr[1]) : "",
      minRr: st.min_rr === null ? "" : String(st.min_rr),
    },
    options: draft.lots ? { ...flow.options, lots: String(draft.lots) } : flow.options,
  };
}

export const sidesOf = (side: FlowSide | null): ("long" | "short")[] => (side === "both" ? ["long", "short"] : side ? [side] : []);

/** What still blocks the Market step. */
export function marketProblems(flow: FlowState): string[] {
  const out: string[] = [];
  if (!flow.symbol.trim()) out.push("Pick a symbol.");
  if (!flow.timeframe) out.push("Pick a timeframe.");
  if (!flow.side) out.push("Pick the side.");
  return out;
}

/** What still blocks the Entry step (template strategies bring their own entry). */
export function entryProblems(flow: FlowState): string[] {
  if (flow.engine === "template") return [];
  const out: string[] = [];
  for (const s of sidesOf(flow.side)) {
    if (!flow.blocks.some((b) => b.side === s)) out.push(`Add at least one ${s} entry block.`);
  }
  if (flow.blocks.some((b) => sidesOf(flow.side).includes(b.side) && blockOpen(b))) out.push("A block still needs your value: tap it to fill it in.");
  const allowed = higherTimeframes(flow.timeframe);
  const wrongTf = (op: FlowOperand) => op.type === "indicator" && op.timeframe !== null && !allowed.includes(op.timeframe);
  if (flow.blocks.some((b) => sidesOf(flow.side).includes(b.side) && (wrongTf(b.left) || wrongTf(b.right)))) {
    out.push("A block reads a timeframe that is not above the strategy's own: change it under the block's Advanced.");
  }
  return out;
}

/** SB-UX SU7: the higher timeframes an operand may be read on (completed bars only) - those above the strategy's own;
 * none for a Daily strategy (the engine resamples intraday bars only). */
const HTF_MINUTES: [string, number][] = [["5min", 5], ["15min", 15], ["30min", 30], ["60min", 60]];
const BASE_MINUTES: Record<string, number> = { "1min": 1, "3min": 3, "5min": 5, "15min": 15, "30min": 30, "60min": 60 };
/** The ATR length a stop needs is still missing: the exits' Advanced fold opens by itself. */
export function atrLengthMissing(exits: FlowExits): boolean {
  return ownValue(exits.stop) !== null && whole(exits.atrPeriod, 500) === null;
}

export function higherTimeframes(base: string | null): string[] {
  const m = base ? BASE_MINUTES[base] : undefined;
  if (m === undefined) return [];
  return HTF_MINUTES.filter(([, n]) => n > m).map(([tf]) => tf);
}

/** The no-code strategy the trader saves, or null while any step is open. Blank exits stay off (null). */
export function flowConfig(flow: FlowState): CustomStrategyConfig | null {
  if (flow.engine !== "custom" || marketProblems(flow).length || entryProblems(flow).length || exitProblems(flow.exits, flow.options, "custom").length) return null;
  const wanted = sidesOf(flow.side);
  const cond = (b: FlowBlock): Condition => ({ left: toOperand(b.left), operator: b.operator as ConditionOperator, right: toOperand(b.right) });
  const stop = ownValue(flow.exits.stop);
  const t1 = ownValue(flow.exits.target1);
  const t2 = ownValue(flow.exits.target2);
  const atr = ownValue(flow.exits.atrPeriod);
  return {
    name: flow.name.trim() || "My strategy",
    timeframe: flow.timeframe as string,
    long_conditions: wanted.includes("long") ? flow.blocks.filter((b) => b.side === "long").map(cond) : [],
    short_conditions: wanted.includes("short") ? flow.blocks.filter((b) => b.side === "short").map(cond) : [],
    stop_loss_atr_mult: stop,
    // used only with a stop; the server's own length is never shown as his value
    atr_period: atr ?? 14,
    target_rr: t1 === null && t2 === null ? null : [t1 ?? (t2 as number), t2 ?? (t1 as number)],
    min_rr: ownValue(flow.exits.minRr),
  };
}

function lotsOf(options: FlowOptions): number | null {
  const n = ownValue(options.lots);
  return n !== null && Number.isInteger(n) && n >= 1 ? n : null;
}

/** The option backtest settings (Phase W) for the structure he picked, or null when he trades the underlying. */
export function optionBacktest(options: FlowOptions, timeframe: string | null): OptionBacktestConfig | null {
  const s = options.structure;
  if (!isOption(s) || !options.expiry || !options.strikeRule) return null;
  const offset = options.strikeRule === "ATM" ? 0 : Number(options.strikeOffset);
  const cfg: OptionBacktestConfig = {
    option_strategy: s === "SINGLE_BUY" || s === "SINGLE_WRITE" ? "SINGLE" : (s as OptionBacktestConfig["option_strategy"]),
    expiry_rule: options.expiry, strike_rule: options.strikeRule, strike_offset: offset, pricing: "synthetic",
    intraday: timeframe !== "day", allow_synthetic_fallback: true,
  };
  if (s === "SINGLE_BUY") cfg.option_position = "BUY";
  if (s === "SINGLE_WRITE") cfg.option_position = "WRITE";
  if (isSpread(s)) cfg.spread_width = Number(options.width);
  const lots = lotsOf(options);
  if (lots !== null) cfg.max_lots = lots;
  return cfg;
}

/** The same choices as the Paper bot's contract rules (the bot trades exactly what was tested). */
export function paperContract(options: FlowOptions): ContractRules | null {
  const s = options.structure;
  if (s === null) return null;
  if (s === "UNDERLYING") return { instrument_kind: "UNDERLYING" };
  const bt = optionBacktest(options, null);
  if (!bt) return null;
  const out: ContractRules = {
    instrument_kind: "OPTION", option_strategy: bt.option_strategy, expiry_rule: bt.expiry_rule, strike_rule: bt.strike_rule, strike_offset: bt.strike_offset,
  };
  if (bt.option_position) out.option_position = bt.option_position;
  if (bt.spread_width !== undefined) out.spread_width = bt.spread_width;
  const lots = lotsOf(options);
  if (lots !== null) out.max_lots = lots;
  return out;
}

// --- Step 5: the result card ----------------------------------------------------------------------------------------

export interface ResultSummary {
  netPnl: number;
  winRate: number;
  maxDd: number;
  maxDdPct: number | null;
  trades: number;
  /** The average result per trade in R (its own risk: entry to stop); null when no trade had a stop. */
  avgR: number | null;
  spark: number[];
}

/** The deepest fall of the equity from a peak, as a percent of that peak (null without a positive starting equity). */
export function drawdownPct(equity: readonly number[]): number | null {
  if (!equity.length || !(equity[0] > 0)) return null;
  let peak = equity[0];
  let worst = 0;
  for (const e of equity) {
    peak = Math.max(peak, e);
    if (peak > 0) worst = Math.max(worst, (peak - e) / peak);
  }
  return Math.round(worst * 10000) / 100;
}

/** One trade in R: its P&L over its own risk (entry to stop, times the quantity); null without a stop. */
export function tradeR(t: Trade): number | null {
  if (t.pnl === null || !t.exit_time) return null;
  const risk = Math.abs(t.entry_price - t.stop_loss) * t.quantity;
  return t.stop_loss > 0 && risk > 0 ? t.pnl / risk : null;
}

/** At most `max` points of the equity curve, the first and the last kept (a sparkline, not a chart). */
export function thin(values: readonly number[], max = 60): number[] {
  if (values.length <= max) return [...values];
  const out: number[] = [];
  for (let i = 0; i < max; i += 1) out.push(values[Math.round((i * (values.length - 1)) / (max - 1))]);
  return out;
}

export function resultSummary(r: BacktestResult): ResultSummary {
  const rs = r.trades.map(tradeR).filter((x): x is number => x !== null);
  return {
    netPnl: r.net_pnl, winRate: r.win_rate, maxDd: r.max_drawdown, maxDdPct: drawdownPct(r.equity_curve), trades: r.total_trades,
    avgR: rs.length ? Math.round((rs.reduce((a, b) => a + b, 0) / rs.length) * 100) / 100 : null, spark: thin(r.equity_curve),
  };
}

// --- Step 6: the hand-over to the Paper bot ------------------------------------------------------------------------

/** What the Paper bot form receives in the navigation state (never in the address): the saved strategy, the symbol and
 * the contract he tested. The form only picks them; he presses Start on paper himself. */
export interface FlowHandoff { flowStrategy: { strategyId: string; symbol: string; contract: ContractRules | null; name: string } }

export function handoffFromState(state: unknown): FlowHandoff["flowStrategy"] | null {
  const f = (state as Partial<FlowHandoff> | null)?.flowStrategy;
  if (!f || typeof f !== "object" || typeof f.strategyId !== "string" || !f.strategyId || typeof f.symbol !== "string") return null;
  const c = f.contract;
  const contract = c && typeof c === "object" && (c.instrument_kind === "OPTION" || c.instrument_kind === "UNDERLYING") ? c : null;
  return { strategyId: f.strategyId, symbol: f.symbol, contract, name: typeof f.name === "string" ? f.name : "" };
}

/** The Paper bot request with the contract he tested in place of the form's own (PAPER stays; underlying drops option fields). */
const OPTION_FIELDS = ["option_position", "expiry_rule", "strike_rule", "strike_offset", "spread_width", "option_strategy", "max_lots"] as const;

export function withTestedContract<T extends ContractRules>(req: T, contract: ContractRules | null | undefined): T {
  if (!contract) return req;
  if (contract.instrument_kind === "UNDERLYING") {
    const out: ContractRules = { ...req, instrument_kind: "UNDERLYING" };
    for (const k of OPTION_FIELDS) delete out[k];
    return out as T;
  }
  return { ...req, ...contract };
}
