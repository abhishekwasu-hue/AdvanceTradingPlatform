import { request } from "../api/client";

/**
 * SFC P5 (owner APPROVED 19 Nov, docs/plans/strategy-first-chart-PLAN.md): the builder's model - an `atp.strategy/2`
 * strategy edited as plain data. Pure functions only, so every rule is unit-tested.
 *
 * Rules kept here:
 * - DEF-0: nothing is pre-filled. A new condition has no block, no comparison and no number; a blank setting is left out
 *   (the server refuses what a rule needs and never fills it in); a blank exit part is "off".
 * - The server is the judge: `check` returns the compiler's own problems; this file never invents a rule.
 * - Nothing here orders anything: a saved strategy runs nowhere until he deploys it himself.
 */
export type Cmp = "crosses_above" | "crosses_below" | "gt" | "lt" | "gte" | "lte" | "between" | "rising" | "falling" | "is_true";
export type ParamValue = number | string | boolean;
export type Operand =
  | { kind: "value"; value: number | null }
  | { kind: "price"; field: "open" | "high" | "low" | "close" | "volume" | "oi"; offset?: number }
  | { kind: "indicator" | "pa"; block: string; params: Record<string, ParamValue>; offset?: number };
export interface Cond {
  id: string; left: Operand | null; cmp: Cmp | null; right?: Operand | null; low?: Operand | null; high?: Operand | null;
  bars?: number | null; within_bars?: number | null; timeframe?: string | null;
}
export interface Group { op: "AND" | "OR"; items: (Cond | Group)[] }
export interface LevelRule { kind: "points" | "pct" | "atr" | "swing" | "candle" | "premium"; value?: number | null; atr_period?: number | null;
  swing_degree?: number | null; candle?: "entry" | "signal" | "previous" | null }
export interface Trailing { kind: "points" | "pct" | "atr" | "swing"; value?: number | null; atr_period?: number | null; swing_degree?: number | null }
export interface OptionLeg { action: "buy" | "sell" | null; right: "CE" | "PE" | null; ratio: number | null; strike_offset: number | null }
export interface Draft {
  schema: "atp.strategy/2"; version: number; name: string; notes?: string | null; timeframe: string | null;
  entry: { sides: "long" | "short" | "both"; long: Group | null; short: Group | null; window: { from: string; to: string } | null;
    max_entries_per_day: number | null };
  exit: { stop: LevelRule | null; target: LevelRule | null; trailing: Trailing | null; time_exit: { after_bars: number | null; at: string | null } | null;
    long_conditions: Group | null; short_conditions: Group | null };
  options: { legs: OptionLeg[]; strike: { rule: "atm" | "itm" | "otm" | "delta" | "premium"; steps?: number | null; delta?: number | null; premium?: number | null } | null;
    expiry: { rule: "current_week" | "next_week" | "current_month" | "next_month" } | null } | null;
}

export interface BlockArg { name: string; type: string; required: boolean; choices: string[]; bounds: [number, number] | null;
  /** The input is a number series (close, high ...), not a setting. */
  series?: boolean }
export interface Block { block: string; kind: string; returns: string; unit: string | null; doc: string; timeframed: boolean; args: BlockArg[];
  kwargs: BlockArg[]; values: string[] }

export const TIMEFRAMES: { value: string; label: string }[] = [
  { value: "1m", label: "1m" }, { value: "3m", label: "3m" }, { value: "5m", label: "5m" }, { value: "15m", label: "15m" },
  { value: "30m", label: "30m" }, { value: "1h", label: "1h" }, { value: "1d", label: "1D" },
];
export const CMP_LABEL: Record<Cmp, string> = {
  crosses_above: "crosses above", crosses_below: "crosses below", gt: "is above", lt: "is below", gte: "is at or above",
  lte: "is at or below", between: "is between", rising: "is rising for", falling: "is falling for", is_true: "is true",
};
export const TWO_SIDED: readonly Cmp[] = ["crosses_above", "crosses_below", "gt", "lt", "gte", "lte"];
export const PRICE_FIELDS = ["open", "high", "low", "close", "volume", "oi"] as const;
export const MAX_DEPTH = 4;

export interface SavedStrategy { id: string; record_id: number; name: string; strategy: Draft }
export const builderApi = {
  blocks: () => request<{ blocks: Block[] }>("/strategies/v2/blocks"),
  check: (strategy: unknown) => request<{ ok: boolean; problems: string[] }>("/strategies/v2/check", { method: "POST", body: JSON.stringify({ strategy }) }),
  create: (strategy: unknown) => request<SavedStrategy>("/strategies/v2", { method: "POST", body: JSON.stringify({ strategy }) }),
  read: (recordId: number) => request<SavedStrategy>(`/strategies/v2/${recordId}`),
  update: (recordId: number, strategy: unknown) => request<SavedStrategy>(`/strategies/v2/${recordId}`, { method: "PUT", body: JSON.stringify({ strategy }) }),
  remove: (recordId: number) => request<void>(`/strategies/v2/${recordId}`, { method: "DELETE" }),
};

/** A new, empty strategy (DEF-0: no name, no timeframe, no condition chosen for him). */
export function emptyDraft(): Draft {
  return {
    schema: "atp.strategy/2", version: 1, name: "", timeframe: null,
    entry: { sides: "long", long: { op: "AND", items: [] }, short: null, window: null, max_entries_per_day: null },
    exit: { stop: null, target: null, trailing: null, time_exit: null, long_conditions: null, short_conditions: null },
    options: null,
  };
}

/** A saved strategy as a draft: every part the editor shows is present (absent = off). */
export function toDraft(raw: Partial<Draft> & Record<string, unknown>): Draft {
  const e = emptyDraft();
  const entry = (raw.entry ?? {}) as Partial<Draft["entry"]>;
  const exit = (raw.exit ?? {}) as Partial<Draft["exit"]>;
  return {
    ...e, name: String(raw.name ?? ""), notes: (raw.notes as string | null | undefined) ?? null, timeframe: (raw.timeframe as string | null) ?? null,
    version: Number(raw.version ?? 1) || 1,
    entry: { sides: entry.sides ?? "long", long: entry.long ?? null, short: entry.short ?? null, window: entry.window ?? null,
      max_entries_per_day: entry.max_entries_per_day ?? null },
    exit: { stop: exit.stop ?? null, target: exit.target ?? null, trailing: exit.trailing ?? null, time_exit: exit.time_exit ?? null,
      long_conditions: exit.long_conditions ?? null, short_conditions: exit.short_conditions ?? null },
    options: (raw.options as Draft["options"]) ?? null,
  };
}

const isGroup = (x: Cond | Group): x is Group => (x as Group).op !== undefined && Array.isArray((x as Group).items);

/** Every condition id in the strategy (ids must be unique across its trees). */
export function conditionIds(d: Draft): string[] {
  const out: string[] = [];
  const walk = (g: Group | null) => g?.items.forEach((x) => (isGroup(x) ? walk(x) : out.push(x.id)));
  [d.entry.long, d.entry.short, d.exit.long_conditions, d.exit.short_conditions].forEach(walk);
  return out;
}

/** The next free condition id ("c1", "c2" ...). */
export function nextId(d: Draft): string {
  const used = new Set(conditionIds(d));
  let k = 1;
  while (used.has(`c${k}`)) k += 1;
  return `c${k}`;
}

export const newCondition = (id: string): Cond => ({ id, left: null, cmp: null });

/** A group at a path of item indexes (empty path = the group itself). */
export function groupAt(g: Group, path: readonly number[]): Group {
  return path.reduce<Group>((cur, i) => cur.items[i] as Group, g);
}

/** A copy of `g` with the group at `path` replaced by `fn(group)`. */
export function updateGroup(g: Group, path: readonly number[], fn: (inner: Group) => Group): Group {
  if (!path.length) return fn(g);
  const [i, ...rest] = path;
  return { ...g, items: g.items.map((x, k) => (k === i ? updateGroup(x as Group, rest, fn) : x)) };
}

export const depthOf = (path: readonly number[]) => path.length + 1;

/** A blank number field is `null` (off / not given), never 0. */
export function numberOrNull(text: string): number | null {
  if (text.trim() === "") return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}

/** The operand kind a block takes in a condition: a number series is an indicator, a true / false one a price-action block. */
export function operandKind(block: Block): "indicator" | "pa" {
  return block.returns === "bool" ? "pa" : "indicator";
}

/**
 * DEF-0 on the client: every setting of every block he chose must be given by him - a blank one would quietly take the
 * engine's own value (e.g. RSI 14), which he never stated. The list of what is still blank, in plain words.
 */
export function blankSettings(d: Draft, blocks: readonly Block[]): string[] {
  const out: string[] = [];
  const byName = new Map(blocks.map((b) => [b.block, b]));
  const operand = (o: Operand | null | undefined, where: string) => {
    if (!o) { out.push(`${where}: choose what it reads`); return; }
    if (o.kind === "value") { if (o.value === null) out.push(`${where}: enter the number`); return; }
    if (o.kind === "price") return;
    const spec = byName.get(o.block);
    for (const a of spec ? [...spec.args, ...spec.kwargs] : []) {
      const v = o.params[a.name];
      if (v === undefined || v === "") out.push(`${where}: ${o.block} needs its '${a.name}' setting`);
    }
  };
  const cond = (c: Cond) => {
    operand(c.left, c.id);
    if (!c.cmp) { out.push(`${c.id}: choose the comparison`); return; }
    if (TWO_SIDED.includes(c.cmp)) operand(c.right, c.id);
    if (c.cmp === "between") { operand(c.low, c.id); operand(c.high, c.id); }
    if ((c.cmp === "rising" || c.cmp === "falling") && !c.bars) out.push(`${c.id}: enter the number of bars`);
  };
  const walk = (g: Group | null) => g?.items.forEach((x) => (isGroup(x) ? walk(x) : cond(x)));
  if (d.entry.sides !== "short") walk(d.entry.long);
  if (d.entry.sides !== "long") walk(d.entry.short);
  walk(d.exit.long_conditions);
  walk(d.exit.short_conditions);
  return out;
}

/** What the server receives: empty groups and blank parts left out (they are "off"), nothing else changed or filled. */
export function serialize(d: Draft): Record<string, unknown> {
  const clean = (g: Group | null): Record<string, unknown> | null => {
    if (!g) return null;
    const items = g.items.map((x) => (isGroup(x) ? clean(x) : cleanCond(x))).filter((x): x is Record<string, unknown> => x !== null);
    return items.length ? { op: g.op, items } : null;
  };
  const cleanCond = (c: Cond): Record<string, unknown> => {
    const out: Record<string, unknown> = { id: c.id, left: c.left ? cleanOperand(c.left) : null, cmp: c.cmp };
    if (c.cmp && TWO_SIDED.includes(c.cmp)) out.right = c.right ? cleanOperand(c.right) : null;
    if (c.cmp === "between") { out.low = c.low ? cleanOperand(c.low) : null; out.high = c.high ? cleanOperand(c.high) : null; }
    if (c.cmp === "rising" || c.cmp === "falling") out.bars = c.bars ?? null;
    if (c.within_bars) out.within_bars = c.within_bars;
    if (c.timeframe) out.timeframe = c.timeframe;
    return out;
  };
  const cleanOperand = (o: Operand): Record<string, unknown> => {
    if (o.kind === "value") return { kind: "value", value: o.value };
    if (o.kind === "price") return { kind: "price", field: o.field, ...(o.offset ? { offset: o.offset } : {}) };
    const params = Object.fromEntries(Object.entries(o.params).filter(([, v]) => v !== "" && v !== null && v !== undefined));
    return { kind: o.kind, block: o.block, params, ...(o.offset ? { offset: o.offset } : {}) };
  };
  const rule = <T extends object>(r: T | null) => (r ? Object.fromEntries(Object.entries(r).filter(([, v]) => v !== null && v !== undefined && v !== "")) : null);
  const long = d.entry.sides === "short" ? null : clean(d.entry.long);
  const short = d.entry.sides === "long" ? null : clean(d.entry.short);
  const timeExit = d.exit.time_exit && (d.exit.time_exit.after_bars || d.exit.time_exit.at) ? rule(d.exit.time_exit) : null;
  const exit = { stop: rule(d.exit.stop), target: rule(d.exit.target), trailing: rule(d.exit.trailing), time_exit: timeExit,
    long_conditions: clean(d.exit.long_conditions), short_conditions: clean(d.exit.short_conditions) };
  const anyExit = Object.values(exit).some((v) => v !== null);
  const out: Record<string, unknown> = {
    schema: d.schema, version: d.version, name: d.name.trim(), timeframe: d.timeframe,
    entry: { sides: d.entry.sides, ...(long ? { long } : {}), ...(short ? { short } : {}),
      ...(d.entry.window ? { window: d.entry.window } : {}), ...(d.entry.max_entries_per_day ? { max_entries_per_day: d.entry.max_entries_per_day } : {}) },
  };
  if (d.notes && d.notes.trim()) out.notes = d.notes.trim();
  if (anyExit) out.exit = Object.fromEntries(Object.entries(exit).filter(([, v]) => v !== null));
  if (d.options && d.options.legs.length) {
    out.options = { legs: d.options.legs.map((l) => rule(l)), strike: rule(d.options.strike), expiry: d.options.expiry };
  }
  return out;
}

/** Import: a pasted / loaded JSON text as a draft, or the reason it cannot be one (the server checks the rest). */
export function parseImport(text: string): { draft: Draft } | { error: string } {
  let raw: unknown;
  try { raw = JSON.parse(text); } catch { return { error: "This is not a strategy file (it is not JSON)." }; }
  if (!raw || typeof raw !== "object" || (raw as Record<string, unknown>).schema !== "atp.strategy/2") {
    return { error: "This is not an ATP strategy file (atp.strategy/2)." };
  }
  return { draft: toDraft(raw as Record<string, unknown>) };
}

/** Export: the strategy as a file name and its JSON text. */
export function exportFile(d: Draft): { name: string; text: string } {
  const base = (d.name.trim() || "strategy").replace(/[^A-Za-z0-9 _-]/g, "").trim().replace(/\s+/g, "-").toLowerCase() || "strategy";
  return { name: `${base}.atp-strategy.json`, text: JSON.stringify(serialize(d), null, 2) };
}
