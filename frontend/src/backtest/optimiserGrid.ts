/**
 * SB-UX SU7 (owner order 30 Oct): the optimiser's search, as fields - per setting From / To / Step - in place of the
 * "Grid as JSON" box. Pure functions only (unit-tested). DEF-0: nothing is pre-filled; a row the trader adds names a
 * setting and stays empty until he types his own range. The server's limit is the same 60 combinations
 * (backend/app/backtest/optimizer.py MAX_COMBOS); this says so before the call, in words.
 */
import { paramLabel } from "../ui/strategyLabels";

export const MAX_COMBINATIONS = 60;
/** One setting can take at most this many values (a long range with a small step is a typing slip). */
export const MAX_VALUES_PER_SETTING = MAX_COMBINATIONS;

export interface GridRow { key: string; from: string; to: string; step: string }

export interface Optimisable { key: string; label: string; current: number; whole: boolean }

/** The strategy's number settings the optimiser can vary (lists, words and switches are left out). */
export function optimisableParams(params: Record<string, unknown> | null | undefined): Optimisable[] {
  if (!params) return [];
  return Object.entries(params)
    .filter(([, v]) => typeof v === "number" && Number.isFinite(v))
    .map(([key, v]) => ({ key, label: paramLabel(key), current: v as number, whole: Number.isInteger(v) }));
}

/** A new row: the first setting not yet in the grid, every number left for the trader. */
export function newRow(params: Optimisable[], rows: GridRow[]): GridRow | null {
  const free = params.find((p) => !rows.some((r) => r.key === p.key));
  return free ? { key: free.key, from: "", to: "", step: "" } : null;
}

function num(raw: string): number | null {
  const t = raw.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** From, From + Step, ... up to To (To itself when the step lands on it). Float steps are rounded, never drift. */
export function rangeValues(from: number, to: number, step: number): number[] {
  const out: number[] = [];
  const decimals = Math.max(...[from, step].map((n) => (String(n).split(".")[1] ?? "").length));
  const scale = 10 ** Math.min(decimals, 6);
  for (let i = 0; out.length <= MAX_VALUES_PER_SETTING; i += 1) {
    const v = Math.round((from + i * step) * scale) / scale;
    if (v > to + 1e-9) break;
    out.push(v);
  }
  return out;
}

export interface GridCheck {
  /** The grid the server receives; null while a problem stands. */
  grid: Record<string, number[]> | null;
  /** Problems in words, one per line, each naming the setting. */
  problems: string[];
  combinations: number;
}

export function checkGrid(rows: GridRow[], params: Optimisable[]): GridCheck {
  const problems: string[] = [];
  const grid: Record<string, number[]> = {};
  if (rows.length === 0) return { grid: null, problems: ["Add at least one setting to vary."], combinations: 0 };
  const seen = new Set<string>();
  let combinations = 1;
  for (const row of rows) {
    const p = params.find((x) => x.key === row.key);
    const name = p?.label ?? paramLabel(row.key);
    if (!p) { problems.push(`${name}: this strategy has no such setting.`); continue; }
    if (seen.has(row.key)) { problems.push(`${name} is in the list twice.`); continue; }
    seen.add(row.key);
    const from = num(row.from), to = num(row.to), step = num(row.step);
    if (from === null || to === null || step === null) { problems.push(`${name}: fill From, To and Step.`); continue; }
    if (step <= 0) { problems.push(`${name}: Step must be above 0.`); continue; }
    if (from > to) { problems.push(`${name}: From must not be above To.`); continue; }
    if (p.whole && ![from, to, step].every(Number.isInteger)) { problems.push(`${name}: use whole numbers.`); continue; }
    if (p.whole && from < 1) { problems.push(`${name}: From must be 1 or more.`); continue; }
    if ((to - from) / step + 1 > MAX_VALUES_PER_SETTING) { problems.push(`${name}: more than ${MAX_VALUES_PER_SETTING} values; use a bigger step.`); continue; }
    const values = rangeValues(from, to, step);
    grid[row.key] = values;
    combinations *= values.length;
  }
  if (problems.length) return { grid: null, problems, combinations: 0 };
  if (combinations > MAX_COMBINATIONS) {
    return { grid: null, problems: [`${combinations} combinations: the limit is ${MAX_COMBINATIONS}. Use a bigger step or fewer settings.`], combinations };
  }
  return { grid, problems: [], combinations };
}
