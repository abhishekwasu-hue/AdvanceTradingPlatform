/**
 * U5 (screener design): the condition canvas as data. A scan is a list of stages joined by ALL - a funnel: the universe
 * enters at the top and every enabled stage narrows it. A stage is a block: an indicator comparison ("RSI(14) on 15m
 * above 60"), a filter ("Pattern doji"), a category ("Trend is UPTREND"), a rank across the universe ("Rank of
 * PctChange(close, 5) at or below 10"), or a group - ANY of its blocks, or NOT all of them (U5 D2). The screen the
 * server runs is the ScreenQL text built here; the server validates it and counts the survivors per top-level stage.
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
  needs?: string[];          // data beyond bars ("reference", "option_chain"); the run path does not load it yet
  field: boolean;
  doc: string;
}
export type Registry = Record<string, RegistryEntry>;

export const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w", "1M"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

/** A window argument the registry gives no default for (SMA's n, ORHigh's minutes): the builder starts here. */
export const DEFAULT_WINDOW = 20;
export const DEFAULT_OR_MINUTES = 15;
export const DEFAULT_TOLERANCE_PCT = 0.5;
/** The fields a series argument (SMA's x) can take in the indicator block. */
export const SERIES_FIELDS = ["close", "open", "high", "low", "volume"] as const;
/** The levels a `level` argument (ReversalAt, RealBreak) can take. */
export const LEVEL_FIELDS = ["PDH()", "PDL()", "PDC()", "DayOpen()", "close"] as const;
/** Literal-number arguments without a registry default (the rest of the untyped numbers are series). */
const NUMBER_ARGS: Record<string, number> = { tolerance_pct: DEFAULT_TOLERANCE_PCT };

export type ParamValue = number | string;

/** How one argument is edited inline. */
export type ParamKind =
  | { kind: "window"; name: string }                         // a whole number of bars
  | { kind: "number"; name: string }                         // a literal number (Supertrend's k)
  | { kind: "select"; name: string; options: ParamValue[] }  // a fixed set (swing degree)
  | { kind: "series"; name: string; options: readonly string[] } // a field (SMA of close) or a level (ReversalAt at PDH())
  | { kind: "text"; name: string };                          // free text (IndexMember's index name)

export interface SeriesRef { fn: string; params: Record<string, ParamValue>; tf: Timeframe | null }
export type Operand = { kind: "value"; value: number } | { kind: "series"; ref: SeriesRef };
export const OPS = [">", ">=", "<", "<=", "crosses above", "crosses below"] as const;
export type Op = (typeof OPS)[number];

export interface IndicatorStage { id: string; kind: "indicator"; left: SeriesRef; op: Op; right: Operand; enabled: boolean }
/** A yes / no function of the bar: `Pattern("doji")`, `NearSupport(0.5, 3)`, `IsFnO()`. */
export interface FilterStage { id: string; kind: "filter"; call: SeriesRef; enabled: boolean }
/** A classifier against one value (`==`) or several (`IN`); `negate` is "is not". */
export interface CategoryStage { id: string; kind: "category"; call: SeriesRef; values: string[]; negate: boolean; enabled: boolean }
export const RANK_FNS = ["Rank", "PercentileRank"] as const;
export const RANK_OPS = ["<=", ">="] as const;
/** A rank across the universe of an indicator: `Rank(PctChange(close, 5)) <= 10`. */
export interface RankStage { id: string; kind: "rank"; fn: (typeof RANK_FNS)[number]; of: SeriesRef; op: (typeof RANK_OPS)[number]; value: number; enabled: boolean }
/** A group drawn as a bracket: ANY of its blocks, or NOT (all of) them. */
export interface GroupStage { id: string; kind: "group"; op: "ANY" | "NOT"; children: Stage[]; enabled: boolean }
export type Stage = IndicatorStage | FilterStage | CategoryStage | RankStage | GroupStage;
export type StageKind = Stage["kind"];

/** A survivor count from `POST /api/screener/run` (`funnel`); `passes` is each symbol's own result per stage. */
export interface FunnelResult {
  universe: number; with_data: number; stages: { text: string; survivors: number; removed?: string[] }[];
  passes?: Record<string, boolean[]>;
}

/** Can this entry be the subject of an indicator block? A number per symbol per bar, with arguments the builder can edit. */
export function isIndicator(e: RegistryEntry | undefined): boolean {
  if (!e || e.needs?.length || e.returns !== "num" || e.kind !== "factor" || e.varargs || e.cross_sectional || !e.timeframed) return false;
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

function editableArgs(e: RegistryEntry): boolean {
  return e.args.every((a) => {
    const t = e.types?.[a];
    return t === "window" || t === "num" || t === "str";
  });
}

/** A filter block: a yes / no function of one symbol (not the two-series crosses - the indicator block has those). */
export function isFilter(e: RegistryEntry | undefined): boolean {
  return !!e && !e.needs?.length && e.kind === "filter" && e.returns === "bool" && !e.varargs && editableArgs(e)
    && !e.args.some((a) => (a === "a" || a === "b"));
}

/** A category block: a classifier compared with its values. */
export function isCategory(e: RegistryEntry | undefined): boolean {
  return !!e && !e.needs?.length && e.kind === "classifier" && e.returns === "cat" && !e.varargs && editableArgs(e);
}

export function namesWhere(reg: Registry, test: (e: RegistryEntry) => boolean): string[] {
  return Object.keys(reg).filter((n) => test(reg[n])).sort((a, b) => a.localeCompare(b));
}

export function paramKinds(e: RegistryEntry): ParamKind[] {
  return e.args.map((name) => {
    const t = e.types?.[name];
    const choices = e.choices[name] ?? [];
    if (choices.length) return { kind: "select", name, options: choices };
    if (t === "window") return { kind: "window", name };
    if (t === "str") return { kind: "text", name };
    if (t === "num" && (e.defaults?.[name] !== undefined || name in NUMBER_ARGS)) return { kind: "number", name };
    if (name === "level") return { kind: "series", name, options: LEVEL_FIELDS };
    return { kind: "series", name, options: SERIES_FIELDS };
  });
}

export function defaultParams(e: RegistryEntry): Record<string, ParamValue> {
  const out: Record<string, ParamValue> = {};
  for (const p of paramKinds(e)) {
    const given = e.defaults?.[p.name];
    if (given !== undefined) out[p.name] = given;
    else if (p.kind === "select") out[p.name] = p.options[0];
    else if (p.kind === "series") out[p.name] = p.options[0];
    else if (p.kind === "window") out[p.name] = p.name === "minutes" ? DEFAULT_OR_MINUTES : DEFAULT_WINDOW;
    else if (p.kind === "text") out[p.name] = "";
    else out[p.name] = NUMBER_ARGS[p.name] ?? 0;
  }
  return out;
}

export function seriesRef(reg: Registry, fn: string, tf: Timeframe | null = null): SeriesRef {
  const e = reg[fn];
  return { fn, params: e ? defaultParams(e) : {}, tf };
}

let seq = 0;
function nextId(): string {
  seq += 1;
  return `s${Date.now().toString(36)}${seq}`;
}

export function newStage(reg: Registry, fn = "RSI", right: Operand = { kind: "value", value: 60 }, op: Op = ">"): IndicatorStage {
  return { id: nextId(), kind: "indicator", left: seriesRef(reg, fn), op, right, enabled: true };
}

/** A new block of a kind, starting from the first suitable registry entry (or the one named). */
export function newBlock(reg: Registry, kind: StageKind, fn?: string): Stage {
  const id = nextId();
  if (kind === "indicator") return { ...newStage(reg, fn ?? "RSI"), id };
  if (kind === "filter") {
    const filters = namesWhere(reg, isFilter);
    const ready = filters.find((n) => !paramKinds(reg[n]).some((p) => p.kind === "text"));   // no blank text to fill first
    return { id, kind, call: seriesRef(reg, fn ?? ready ?? filters[0] ?? "IsFnO"), enabled: true };
  }
  if (kind === "category") {
    const name = fn ?? namesWhere(reg, isCategory).find((n) => reg[n].values.length) ?? namesWhere(reg, isCategory)[0] ?? "Trend";
    return { id, kind, call: seriesRef(reg, name), values: reg[name]?.values.filter(Boolean).slice(0, 1) ?? [], negate: false, enabled: true };
  }
  if (kind === "rank") return { id, kind, fn: "Rank", of: seriesRef(reg, fn ?? "RSI"), op: "<=", value: 10, enabled: true };
  return { id, kind, op: "ANY", children: [newStage(reg), newStage(reg, "close", { kind: "series", ref: seriesRef(reg, "EMA") })], enabled: true };
}

function num(v: number): string {
  if (!Number.isFinite(v)) return "0";
  return Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(12)));
}

function argText(kind: ParamKind | undefined, v: ParamValue): string {
  if (kind?.kind === "series") return String(v);
  if (kind?.kind === "text") return JSON.stringify(String(v));
  if (typeof v === "number") return num(v);
  return /^-?\d+(\.\d+)?$/.test(v) && kind?.kind !== "select" ? v : JSON.stringify(v);
}

/** `RSI(14)@15m`, `SMA(close, 20)`, `close@1d`, `IsFnO()`. */
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

/** Is the block in the scan? Enabled, and for a group, with at least one block in the scan. */
export function isLive(s: Stage): boolean {
  return s.enabled && (s.kind !== "group" || s.children.some(isLive));
}

function live(stages: Stage[]): Stage[] {
  return stages.filter(isLive);
}

export function stageText(reg: Registry, s: Stage): string {
  switch (s.kind) {
    case "indicator": {
      const l = seriesText(reg, s.left);
      const r = operandText(reg, s.right);
      if (s.op === "crosses above") return `CrossAbove(${l}, ${r})`;
      if (s.op === "crosses below") return `CrossBelow(${l}, ${r})`;
      return `${l} ${s.op} ${r}`;
    }
    case "filter":
      return seriesText(reg, s.call);
    case "category": {
      const call = seriesText(reg, s.call);
      const values = s.values.length ? s.values : [""];
      if (values.length === 1) return `${call} ${s.negate ? "!=" : "=="} ${JSON.stringify(values[0])}`;
      const inText = `${call} IN (${values.map((v) => JSON.stringify(v)).join(", ")})`;
      return s.negate ? `NOT (${inText})` : inText;
    }
    case "rank":
      return `${s.fn}(${seriesText(reg, s.of)}) ${s.op} ${num(s.value)}`;
    case "group": {
      const parts = live(s.children).map((c) => stageText(reg, c));
      return s.op === "ANY" ? `(${parts.join(" OR ")})` : `NOT (${parts.join(" AND ")})`;
    }
  }
}

/** The ScreenQL the server runs: the enabled stages joined by AND ("" when there is none). */
export function screenText(reg: Registry, stages: Stage[]): string {
  return live(stages).map((s) => stageText(reg, s)).join(" AND ");
}

/** Where each enabled block sits in `screenText`, groups' children included (so a validation problem at a position
 * lands on the innermost block it points at). */
export function stageSpans(reg: Registry, stages: Stage[]): { id: string; start: number; end: number }[] {
  const spans: { id: string; start: number; end: number }[] = [];
  const walk = (list: Stage[], at: number, sep: string): number => {
    live(list).forEach((s, i) => {
      if (i > 0) at += sep.length;
      const t = stageText(reg, s);
      spans.push({ id: s.id, start: at, end: at + t.length });
      if (s.kind === "group") walk(s.children, at + (s.op === "ANY" ? 1 : "NOT (".length), s.op === "ANY" ? " OR " : " AND ");
      at += t.length;
    });
    return at;
  };
  walk(stages, 0, " AND ");
  return spans;
}

export function stageAt(reg: Registry, stages: Stage[], pos: number | null | undefined): string | null {
  if (pos == null) return null;
  const hits = stageSpans(reg, stages).filter((sp) => pos >= sp.start && pos <= sp.end);
  hits.sort((a, b) => (a.end - a.start) - (b.end - b.start));            // the innermost block
  return hits[0]?.id ?? null;
}

const OP_WORDS: Record<Op, string> = {
  ">": "above", ">=": "at or above", "<": "below", "<=": "at or below", "crosses above": "crosses above", "crosses below": "crosses below",
};

function seriesWords(reg: Registry, s: SeriesRef): string {
  const e = reg[s.fn];
  const base = !e || e.field ? s.fn : seriesText(reg, { ...s, tf: null });
  return s.tf ? `${base} on ${s.tf}` : base;
}

/** The plain-English line on a stage: "RSI(14) on 15m above 60", "Trend(3) is UPTREND", "any of: ...". */
export function stageSummary(reg: Registry, s: Stage): string {
  switch (s.kind) {
    case "indicator": {
      const right = s.right.kind === "value" ? num(s.right.value) : seriesWords(reg, s.right.ref);
      return `${seriesWords(reg, s.left)} ${OP_WORDS[s.op]} ${right}`;
    }
    case "filter":
      return seriesWords(reg, s.call);
    case "category":
      return `${seriesWords(reg, s.call)} ${s.negate ? "is not" : "is"} ${s.values.length > 1 ? `one of ${s.values.join(", ")}` : s.values[0] ?? "(pick a value)"}`;
    case "rank":
      return `${s.fn === "Rank" ? "rank" : "percentile rank"} of ${seriesWords(reg, s.of)} ${OP_WORDS[s.op]} ${num(s.value)}`;
    case "group": {
      const parts = live(s.children).map((c) => stageSummary(reg, c));
      return s.op === "ANY" ? `any of: ${parts.join("; ")}` : `none of: ${parts.join("; ")}`;
    }
  }
}

export interface TrailRow { id: string; survivors: number | null; removed: number | null; kills: boolean }

/** What a run was made of: each enabled stage's ScreenQL, the scan timeframe and the symbols, in order. */
export interface RunStamp { texts: string[]; scanTf: string; symbols: string[] }

export function runStamp(reg: Registry, stages: Stage[], scanTf: string, symbols: string[]): RunStamp {
  return { texts: live(stages).map((s) => stageText(reg, s)), scanTf, symbols: symbols.slice() };
}

/** Is `now` the scan that ran? A changed stage, timeframe or symbol list makes every count and result of that run stale. */
export function sameRun(ran: RunStamp | null, now: RunStamp): boolean {
  if (!ran) return false;
  const eq = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i]);
  return ran.scanTf === now.scanTf && eq(ran.texts, now.texts) && eq(ran.symbols, now.symbols);
}

/**
 * The survivor trail beside each stage from a run's funnel: the count after the stage, how many it removed, and whether
 * it is the stage that removed the last symbols (its trail turns to the warning colour). Disabled stages - and every
 * stage once the scan changed since the run (a stage, the timeframe or the symbols: `fresh` false) - have no count
 * (null): an old number beside a changed scan would mislead.
 */
export function trail(stages: Stage[], funnel: FunnelResult | null, fresh: boolean): TrailRow[] {
  const enabled = live(stages);
  const usable = fresh && !!funnel && funnel.stages.length === enabled.length;
  let before = funnel?.with_data ?? null;
  let killed = false;
  return stages.map((s) => {
    if (!enabled.includes(s) || !usable || !funnel) return { id: s.id, survivors: null, removed: null, kills: false };
    const i = enabled.indexOf(s);
    const after = funnel.stages[i].survivors;
    const removed = before == null ? null : before - after;
    const kills = !killed && after === 0 && (before ?? 0) > 0;
    if (kills) killed = true;
    before = after;
    return { id: s.id, survivors: after, removed, kills };
  });
}

/**
 * What must be filled in before a block can run (U5 D2 review): a category with no value would run as `== ""` (which
 * means "no event" for StructureEvent and matches nothing elsewhere), and a blank text argument would run as `""`.
 * Keyed by block id, groups included; the page treats these like the server's problems (no Run, no preview).
 */
export function blockProblems(reg: Registry, stages: Stage[]): Record<string, string> {
  const out: Record<string, string> = {};
  const blankText = (ref: SeriesRef): string | null => {
    const e = reg[ref.fn];
    if (!e) return null;
    const blank = paramKinds(e).find((p) => p.kind === "text" && !String(ref.params[p.name] ?? "").trim());
    return blank ? `Type the ${blank.name} for ${ref.fn}.` : null;
  };
  const walk = (list: Stage[]) => {
    for (const s of list) {
      if (!s.enabled) continue;
      let problem: string | null = null;
      if (s.kind === "category") problem = s.values.length ? blankText(s.call) : `Pick at least one value for ${s.call.fn}.`;
      else if (s.kind === "filter") problem = blankText(s.call);
      else if (s.kind === "group") walk(s.children);
      if (problem) out[s.id] = problem;
    }
  };
  walk(stages);
  return out;
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

function withNewIds(s: Stage): Stage {
  seq += 1;
  const copy: Stage = { ...s, id: `${s.id}c${seq}` };
  return copy.kind === "group" ? { ...copy, children: copy.children.map(withNewIds) } : copy;
}

export function duplicateStage(stages: Stage[], id: string): Stage[] {
  const i = stages.findIndex((s) => s.id === id);
  if (i < 0) return stages;
  const copy = withNewIds(JSON.parse(JSON.stringify(stages[i])) as Stage);
  return [...stages.slice(0, i + 1), copy, ...stages.slice(i + 1)];
}

/** Which stages each symbol passed on its own ("why it matched"): one entry per enabled top-level stage. */
export function whyChips(reg: Registry, stages: Stage[], funnel: FunnelResult | null, symbol: string): { id: string; summary: string; passed: boolean }[] {
  const row = funnel?.passes?.[symbol];
  const enabled = live(stages);
  if (!row || row.length !== enabled.length) return [];
  return enabled.map((s, i) => ({ id: s.id, summary: stageSummary(reg, s), passed: row[i] }));
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
