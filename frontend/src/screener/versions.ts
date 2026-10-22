/**
 * Handover 11j step 6 "versions" of a saved scan (flag `screener_copilot`, behind `ui_v2`). The server keeps one copy
 * of a saved scan (`PUT /screener/screens/{id}` updates it in place; immutable server versions are AMW-7 and need a
 * migration - OPEN_QUESTIONS LAB-18), so the history is kept ON THIS DEVICE: every save that changes the scan adds a
 * version (its blocks, timeframe and name), at most MAX_VERSIONS per scan. Opening a version puts its blocks back in the
 * builder; nothing is saved until the trader presses Save.
 *
 * Pure helpers (tested) plus a small storage wrapper that never throws: private windows and blocked storage just mean
 * no history.
 */
import type { Registry, Stage } from "./model";
import { isLive, stageSummary, stageText } from "./model";

export const VERSIONS_KEY = "atp_screener_versions:1";
export const MAX_VERSIONS = 20;
const MAX_SCANS = 50;

export interface ScanVersion {
  at: string;              // ISO time of the save
  name: string;
  base_tf: string;
  stages: Stage[];         // the builder blocks as saved
  texts: string[];         // each enabled block's ScreenQL (for compare)
  summaries: string[];     // each enabled block in plain words (for the change lines)
}

export type VersionStore = Record<string, ScanVersion[]>;

/** A version of the scan as saved. `reads` gives a block's plain words when the scan came from an idea (its text ->
 * what the part checks); other blocks use the builder's own summary. */
export function snapshot(reg: Registry, name: string, baseTf: string, stages: Stage[], at: Date, reads: Record<string, string> = {}): ScanVersion {
  const live = stages.filter(isLive);
  const texts = live.map((s) => stageText(reg, s));
  return { at: at.toISOString(), name, base_tf: baseTf, stages: JSON.parse(JSON.stringify(stages)) as Stage[],
    texts, summaries: live.map((s, i) => reads[texts[i]] ?? stageSummary(reg, s)) };
}

/** The plain words of each block a scan from an idea opened with: a part's condition, and each AND piece of it (the
 * builder splits "RSI() >= 35 AND RSI() <= 45" into two blocks), -> what the part checks. */
export function readsOf(parts: { condition: string; reads: string; ok: boolean }[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of parts) {
    if (!p.ok) continue;
    out[p.condition] = p.reads;
    if (!p.condition.includes(" OR ")) for (const piece of p.condition.split(" AND ")) out[piece.trim()] = p.reads;
  }
  return out;
}

function same(a: ScanVersion, b: ScanVersion): boolean {
  return a.base_tf === b.base_tf && a.texts.length === b.texts.length && a.texts.every((t, i) => t === b.texts[i]);
}

/** The store with `v` added to scan `id`'s history - unless the conditions and timeframe are the same as the latest
 * version (a rename alone renames the latest version). Oldest versions and scans fall off past the caps. */
export function addVersion(store: VersionStore, id: number, v: ScanVersion): VersionStore {
  const key = String(id);
  const list = store[key] ?? [];
  const last = list[list.length - 1];
  const next = last && same(last, v) ? [...list.slice(0, -1), { ...last, name: v.name }] : [...list, v].slice(-MAX_VERSIONS);
  const out: VersionStore = { ...store, [key]: next };
  const keys = Object.keys(out);
  if (keys.length > MAX_SCANS) {
    const latest = (k: string) => out[k][out[k].length - 1]?.at ?? "";
    const oldest = keys.sort((a, b) => latest(a).localeCompare(latest(b)))[0];
    delete out[oldest];
  }
  return out;
}

/** What changed from `prev` to `v`, in plain lines ("Added: RSI(14) above 60", "Removed: ...", "Timeframe: 1d to 1h"). */
export function changes(prev: ScanVersion | undefined, v: ScanVersion): string[] {
  if (!prev) return ["First saved version."];
  const out: string[] = [];
  const uniq = (xs: (string | null)[]) => [...new Set(xs.filter((x): x is string => !!x))];
  const added = uniq(v.texts.map((t, i) => (prev.texts.includes(t) ? null : v.summaries[i])));
  const removed = uniq(prev.texts.map((t, i) => (v.texts.includes(t) ? null : prev.summaries[i])));
  if (added.length) out.push(`Added: ${added.join("; ")}`);
  if (removed.length) out.push(`Removed: ${removed.join("; ")}`);
  if (prev.base_tf !== v.base_tf) out.push(`Timeframe: ${prev.base_tf} to ${v.base_tf}`);
  if (!out.length && prev.texts.join("\n") !== v.texts.join("\n")) out.push("The same blocks in a new order.");
  if (prev.name !== v.name) out.push(`Name: “${prev.name}” to “${v.name}”`);
  return out.length ? out : ["No change in the conditions."];
}

/** "22 Oct, 14:05" in India time. */
export function savedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" }).format(d);
}

function isVersion(v: unknown): v is ScanVersion {
  const x = v as ScanVersion;
  return !!x && typeof x.at === "string" && typeof x.name === "string" && typeof x.base_tf === "string" && Array.isArray(x.stages)
    && Array.isArray(x.texts) && Array.isArray(x.summaries);
}

export function readStore(storage: Pick<Storage, "getItem"> | null = safeStorage()): VersionStore {
  try {
    const raw = storage?.getItem(VERSIONS_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : {};
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: VersionStore = {};
    for (const [k, list] of Object.entries(parsed as Record<string, unknown>)) {
      if (Array.isArray(list)) out[k] = list.filter(isVersion);
    }
    return out;
  } catch {
    return {};
  }
}

export function writeStore(store: VersionStore, storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try {
    storage?.setItem(VERSIONS_KEY, JSON.stringify(store));
  } catch { /* storage full or blocked: no history, the save itself already happened */ }
}

function safeStorage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}
