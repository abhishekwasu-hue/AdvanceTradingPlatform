/**
 * U5 (screener design): the condition canvas as data. A scan is a list of stages joined by ALL - a funnel: the universe
 * enters at the top and every enabled stage narrows it. Each stage is one comparison ("RSI(14) on 15m above 60"). The
 * screen the server runs is the ScreenQL text built here; the server validates it and counts the survivors per stage.
 *
 * Pure functions only (no React, no network) so every rule is unit-tested.
 */

export type ArgType = "num" | "window" | "str" | "bool";

/** One registry entry as `GET /api/screener/registry` gives it. */
export interface RegistryEntry {
  kind: "factor" | "filter" | "classifier";
  returns: "num" | "bool" | "cat";
  unit: string | null;
  args: string[];
  types?: Record<string, ArgType>;
  defaults?: Record<string, number | string>;
  choices: Record<string, (number | string)[]>;
  values: string[];
  varargs: boolean;
  timeframed: boolean;
  cross_sectional?: boolean;
  field: boolean;
  doc: string;
}
export type Registry = Record<string, RegistryEntry>;

export const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w", "1M"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

/** A window argument the registry gives no default for (SMA's n, ORHigh's minutes): the builder starts here. */
export const DEFAULT_WINDOW = 20;
export const DEFAULT_OR_MINUTES = 15;
/** The fields a series argument (SMA's x) can take in the indicator block. */
export const SERIES_FIELDS = ["close", "open", "high", "low", "volume"] as const;

export type ParamValue = number | string;

/** How one argument is edited inline. */
export type ParamKind =
  | { kind: "window"; name: string }                         // a whole number of bars
  | { kind: "number"; name: string }                         // a literal number (Supertrend's k)
  | { kind: "select"; name: string; options: ParamValue[] }  // a fixed set (swing degree)
  | { kind: "series"; name: string; options: readonly string[] }; // a field (SMA of close)

export interface SeriesRef { fn: string; params: Record<string, ParamValue>; tf: Timeframe | null }
export type Operand = { kind: "value"; value: number } | { kind: "series"; ref: SeriesRef };
export const OPS = [">", ">=", "<", "<=", "crosses above", "crosses below"] as const;
export type Op = (typeof OPS)[number];

export interface Stage { id: string; kind: "indicator"; left: SeriesRef; op: Op; right: Operand; enabled: boolean }

/** A survivor count from `POST /api/screener/run` (`funnel`). */
export interface FunnelResult { universe: number; with_data: number; stages: { text: string; survivors: number; removed?: string[] }[] }

/** Can this entry be the subject of an indicator block? A number per symbol per bar, with arguments the builder can edit. */
export function isIndicator(e: RegistryEntry | undefined): boolean {
  if (!e || e.returns !== "num" || e.kind !== "factor" || e.varargs || e.cross_sectional || !e.timeframed) return false;
  return e.args.every((a) => {
    const t = e.types?.[a];
    return t === "window" || t === "num" || (t === "str" && (e.choices[a]?.length ?? 0) > 0);
  });
}

export function indicatorNames(reg: Registry): string[] {
  const names = Object.keys(reg).filter((n) => isIndicator(reg[n]));
  const fields = names.filter((n) => reg[n].field);
  return [...fields, ...names.filter((n) => !reg[n].field).sort((a, b) => a.localeCompare(b))];
}

export function paramKinds(e: RegistryEntry): ParamKind[] {
  return e.args.map((name) => {
    const t = e.types?.[name];
    const choices = e.choices[name] ?? [];
    if (choices.length) return { kind: "select", name, options: choices };
    if (t === "window") return { kind: "window", name };
    if (t === "num" && e.defaults?.[name] !== undefined) return { kind: "number", name };
    return { kind: "series", name, options: SERIES_FIELDS };
  });
}

export function defaultParams(e: RegistryEntry): Record<string, ParamValue> {
  const out: Record<string, ParamValue> = {};
  for (const p of paramKinds(e)) {
    const given = e.defaults?.[p.name];
    if (given !== undefined) out[p.name] = given;
    else if (p.kind === "select") out[p.name] = p.options[0];
    else if (p.kind === "series") out[p.name] = "close";
    else if (p.kind === "window") out[p.name] = p.name === "minutes" ? DEFAULT_OR_MINUTES : DEFAULT_WINDOW;
    else out[p.name] = 0;
  }
  return out;
}

export function seriesRef(reg: Registry, fn: string, tf: Timeframe | null = null): SeriesRef {
  const e = reg[fn];
  return { fn, params: e ? defaultParams(e) : {}, tf };
}

let seq = 0;
export function newStage(reg: Registry, fn = "RSI", right: Operand = { kind: "value", value: 60 }, op: Op = ">"): Stage {
  seq += 1;
  return { id: `s${Date.now().toString(36)}${seq}`, kind: "indicator", left: seriesRef(reg, fn), op, right, enabled: true };
}

function num(v: number): string {
  if (!Number.isFinite(v)) return "0";
  return Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(12)));
}

function argText(kind: ParamKind | undefined, v: ParamValue): string {
  if (kind?.kind === "series") return String(v);
  if (typeof v === "number") return num(v);
  return /^-?\d+(\.\d+)?$/.test(v) && kind?.kind !== "select" ? v : JSON.stringify(v);
}

/** `RSI(14)@15m`, `SMA(close, 20)`, `close@1d`. */
export function seriesText(reg: Registry, s: SeriesRef): string {
  const e = reg[s.fn];
  const suffix = s.tf ? `@${s.tf}` : "";
  if (!e || e.field) return `${s.fn}${suffix}`;
  const kinds = paramKinds(e);
  const args = e.args.map((a, i) => argText(kinds[i], s.params[a] ?? defaultParams(e)[a]));
  return `${s.fn}(${args.join(", ")})${suffix}`;
}

export function operandText(reg: Registry, o: Operand): string {
  return o.kind === "value" ? num(o.value) : seriesText(reg, o.ref);
}

export function stageText(reg: Registry, s: Stage): string {
  const l = seriesText(reg, s.left);
  const r = operandText(reg, s.right);
  if (s.op === "crosses above") return `CrossAbove(${l}, ${r})`;
  if (s.op === "crosses below") return `CrossBelow(${l}, ${r})`;
  return `${l} ${s.op} ${r}`;
}

/** The ScreenQL the server runs: the enabled stages joined by AND ("" when there is none). */
export function screenText(reg: Registry, stages: Stage[]): string {
  return stages.filter((s) => s.enabled).map((s) => stageText(reg, s)).join(" AND ");
}

/** Where each enabled stage sits in `screenText` (so a validation problem at a position lands on its stage). */
export function stageSpans(reg: Registry, stages: Stage[]): { id: string; start: number; end: number }[] {
  const spans: { id: string; start: number; end: number }[] = [];
  let at = 0;
  for (const s of stages.filter((x) => x.enabled)) {
    if (spans.length) at += " AND ".length;
    const t = stageText(reg, s);
    spans.push({ id: s.id, start: at, end: at + t.length });
    at += t.length;
  }
  return spans;
}

export function stageAt(reg: Registry, stages: Stage[], pos: number | null | undefined): string | null {
  if (pos == null) return null;
  return stageSpans(reg, stages).find((sp) => pos >= sp.start && pos <= sp.end)?.id ?? null;
}

const OP_WORDS: Record<Op, string> = {
  ">": "above", ">=": "at or above", "<": "below", "<=": "at or below", "crosses above": "crosses above", "crosses below": "crosses below",
};

function seriesWords(reg: Registry, s: SeriesRef): string {
  const e = reg[s.fn];
  const base = !e || e.field ? s.fn : seriesText(reg, { ...s, tf: null });
  return s.tf ? `${base} on ${s.tf}` : base;
}

/** The plain-English line on a stage: "RSI(14) on 15m above 60", "close crosses above EMA(close, 20)". */
export function stageSummary(reg: Registry, s: Stage): string {
  const right = s.right.kind === "value" ? num(s.right.value) : seriesWords(reg, s.right.ref);
  return `${seriesWords(reg, s.left)} ${OP_WORDS[s.op]} ${right}`;
}

export interface TrailRow { id: string; survivors: number | null; removed: number | null; kills: boolean }

/**
 * The survivor trail beside each stage from a run's funnel: the count after the stage, how many it removed, and whether
 * it is the stage that removed the last symbols (its trail turns to the warning colour). Disabled stages and stages
 * edited since the run have no count (null) - an old number beside a changed stage would mislead.
 */
export function trail(stages: Stage[], funnel: FunnelResult | null, ranTexts: string[] | null, reg: Registry): TrailRow[] {
  const enabled = stages.filter((s) => s.enabled);
  const fresh = !!funnel && !!ranTexts && ranTexts.length === enabled.length
    && enabled.every((s, i) => ranTexts[i] === stageText(reg, s)) && funnel.stages.length === enabled.length;
  let before = funnel?.with_data ?? null;
  let killed = false;
  return stages.map((s) => {
    if (!s.enabled || !fresh || !funnel) return { id: s.id, survivors: null, removed: null, kills: false };
    const i = enabled.indexOf(s);
    const after = funnel.stages[i].survivors;
    const removed = before == null ? null : before - after;
    const kills = !killed && after === 0 && (before ?? 0) > 0;
    if (kills) killed = true;
    before = after;
    return { id: s.id, survivors: after, removed, kills };
  });
}

/** Moves a stage up or down by one (keyboard reordering; drag uses the same rule). */
export function moveStage(stages: Stage[], id: string, delta: -1 | 1): Stage[] {
  const i = stages.findIndex((s) => s.id === id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= stages.length) return stages;
  const out = stages.slice();
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

export function duplicateStage(stages: Stage[], id: string): Stage[] {
  const i = stages.findIndex((s) => s.id === id);
  if (i < 0) return stages;
  seq += 1;
  const copy: Stage = JSON.parse(JSON.stringify(stages[i]));
  copy.id = `${stages[i].id}c${seq}`;
  return [...stages.slice(0, i + 1), copy, ...stages.slice(i + 1)];
}

/** Symbols typed by the trader: upper case, no blanks, no repeats, at most `max` (the run endpoint's limit). */
export function parseSymbols(text: string, max = 50): string[] {
  const seen = new Set<string>();
  for (const raw of text.split(/[\s,;]+/)) {
    const s = raw.trim().toUpperCase();
    if (s && !seen.has(s)) seen.add(s);
    if (seen.size >= max) break;
  }
  return [...seen];
}
