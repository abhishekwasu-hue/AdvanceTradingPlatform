/** N1 X1b: types and pure helpers for a scan rule's Then template - kept out of the components so they are unit-tested.
 * The server stays the authority (backend app/screener/then/templates.py); the bounds here mirror its GATE_BOUNDS. Nothing
 * here places an order: a Then template only decides how a confirmed setup reaches the person (a notification or a PAPER
 * proposal they approve). AUTO_LIVE is never offered. */
import type { ThenT } from "./i18n";

export const THEN_MODES = ["NOTIFY", "ONE_CLICK", "AUTO_PAPER"] as const;
export type ThenMode = (typeof THEN_MODES)[number];
export const CONFIRM_TFS = ["5m", "15m"] as const;
export type ConfirmTf = (typeof CONFIRM_TFS)[number];

export interface GateField { key: string; kind: "number" | "bool"; min?: number; max?: number; step?: number; whole?: boolean }

/** The gate settings a person may change, with the server's bounds. */
export const GATE_FIELDS: GateField[] = [
  { key: "min_rr", kind: "number", min: 1, max: 10, step: 0.1 },
  { key: "max_armed_bars", kind: "number", min: 2, max: 40, step: 1, whole: true },
  { key: "mr_n", kind: "number", min: 5, max: 100, step: 1, whole: true },
  { key: "approach_bars", kind: "number", min: 1, max: 10, step: 1, whole: true },
  { key: "approach_wide_mr", kind: "number", min: 0.5, max: 5, step: 0.1 },
  { key: "approach_close_frac", kind: "number", min: 0, max: 1, step: 0.05 },
  { key: "approach_min_wide", kind: "number", min: 1, max: 10, step: 1, whole: true },
  { key: "sl_buffer_mr", kind: "number", min: 0, max: 3, step: 0.05 },
  { key: "wait_next_candle", kind: "bool" },
  { key: "allow_parent_correction", kind: "bool" },
];

export type GateValue = number | boolean;
export type GateOverrides = Record<string, GateValue>;

export interface SymbolStatus { code: string; lines: string[]; trend?: string | null; bar?: string | null; tries?: number }

export interface ThenTemplate {
  rule_id: number; configured: boolean; enabled: boolean; mode: string; confirm_tf: string; gate: GateOverrides;
  gate_defaults: Record<string, GateValue>; modes: string[]; confirm_tfs: string[]; user_id?: number | null;
  last_bar_at?: string | null; last_problem?: string | null; status?: Record<string, SymbolStatus>; updated_at?: string | null;
  /** False: the last check is the template owner's and the organisation owner's only (the server sent none). */
  status_shown?: boolean;
  /** Review of X1b: true when the caller set this template (or none is set); false = another member did. */
  set_by_you?: boolean;
  /** The server's own check: the rule's creator or the organisation owner (others get a 403, so the panel hides Save). */
  may_change?: boolean;
}

export interface ThenIntent {
  id: number; symbol: string; direction: string; mode: string; status: string; route: string | null; reason: string | null;
  ai_action_id: number | null; notification_id: number | null; gate_status: string | null; entry: number | null;
  stop_loss: number | null; target: number | null; rr: number | null; grade: string | null; study: string | null;
  commit_line: string | null; confirm_ts: string | null; created_at: string | null;
}

export interface ThenBody { mode: ThenMode; confirm_tf: ConfirmTf; enabled: boolean; gate: GateOverrides; take_over?: boolean }

/** Review of X1b: saving would move another member's template (and its setups) to the caller - confirm first. */
export function needsTakeOver(t: ThenTemplate | null): boolean {
  return Boolean(t?.configured) && t?.set_by_you === false;
}

export function isThenTemplate(x: unknown): x is ThenTemplate {
  const t = x as ThenTemplate | null;
  return !!t && typeof t === "object" && typeof t.rule_id === "number" && typeof t.configured === "boolean" && typeof t.mode === "string"
    && Array.isArray(t.modes) && typeof t.gate_defaults === "object";
}

/** The modes to offer: the client's own list, so a server that ever sent AUTO_LIVE would still not show it. */
export function visibleModes(): ThenMode[] {
  return [...THEN_MODES];
}

export function asMode(mode: string): ThenMode {
  return (THEN_MODES as readonly string[]).includes(mode) ? (mode as ThenMode) : "NOTIFY";
}

export function asTf(tf: string): ConfirmTf {
  return (CONFIRM_TFS as readonly string[]).includes(tf) ? (tf as ConfirmTf) : "5m";
}

/** The chip's key: off (no template), paused (disabled) or the mode. */
export function chipKey(t: ThenTemplate | null): string {
  if (!t || !t.configured) return "chip.off";
  if (!t.enabled) return "chip.paused";
  return `chip.${asMode(t.mode)}`;
}

/** The form's starting body from a template (a new one: Notify on 5m, no overrides). */
export function bodyOf(t: ThenTemplate | null): ThenBody {
  if (!t || !t.configured) return { mode: "NOTIFY", confirm_tf: "5m", enabled: true, gate: {} };
  return { mode: asMode(t.mode), confirm_tf: asTf(t.confirm_tf), enabled: t.enabled, gate: { ...t.gate } };
}

/** Problems with the overrides, in words (empty = fine); the server checks again. */
export function gateErrors(gate: GateOverrides, t: ThenT): string[] {
  const out: string[] = [];
  for (const f of GATE_FIELDS) {
    if (!(f.key in gate) || f.kind === "bool") continue;
    const v = gate[f.key];
    const label = t(`gate.${f.key}`);
    if (typeof v !== "number" || !Number.isFinite(v)) { out.push(t("errors.number", { label })); continue; }
    if (f.whole && !Number.isInteger(v)) { out.push(t("errors.whole", { label })); continue; }
    if (v < (f.min ?? -Infinity) || v > (f.max ?? Infinity)) out.push(t("errors.range", { label, lo: f.min, hi: f.max }));
  }
  return out;
}

/** Sets or clears (empty input) one override. */
export function withOverride(gate: GateOverrides, key: string, raw: string | boolean | null): GateOverrides {
  const next = { ...gate };
  if (raw === null || raw === "") delete next[key];
  else next[key] = typeof raw === "boolean" ? raw : Number(raw);
  return next;
}

/** A status code in words; an unknown code is shown as it is. */
export function codeLabel(code: string, t: ThenT): string {
  const key = `codes.${code}`;
  const text = t(key);
  return text === key ? code : text;
}

/** What happened to an intent, as an i18n key and its values. */
export function outcomeOf(i: ThenIntent): { key: string; vars: Record<string, string | number> } {
  if (i.status === "DONE" && i.route === "PROPOSE") return { key: "intents.outcomes.proposed", vars: { id: i.ai_action_id ?? "?" } };
  if (i.status === "DONE") return { key: "intents.outcomes.notified", vars: {} };
  if (i.status === "REFUSED") return { key: "intents.outcomes.refused", vars: { reason: i.reason ?? "" } };
  if (i.status === "EXPIRED") return { key: "intents.outcomes.expired", vars: {} };
  if (i.status === "FAILED") return { key: "intents.outcomes.failed", vars: { reason: i.reason ?? "" } };
  if (i.status === "CLAIMED") return { key: "intents.outcomes.sending", vars: {} };
  return { key: "intents.outcomes.queued", vars: {} };
}

const fmt = (v: number | null): string => (v === null || v === undefined ? "-" : v.toLocaleString("en-IN", { maximumFractionDigits: 2 }));

export function levelsVars(i: ThenIntent): Record<string, string> {
  return { entry: fmt(i.entry), sl: fmt(i.stop_loss), target: fmt(i.target), rr: fmt(i.rr) };
}
