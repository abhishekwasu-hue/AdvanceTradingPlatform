// M76 MP-2 (owner order M76 + amendment 1-5; docs/design/M76_REVIEW.md sections 8-9): the lines of the ONE check card.
//
// - Every line is one sentence of facts with numbers, compared only with a plain fact or a value he saved himself.
// - Sources: the server's checks (POST /api/paper/fno/order/checks), the preview's own warnings (`warning_ids`) and the
//   figures already on the screen (margin vs his balance, his events calendar, his own liquidity limits, his risk limits).
// - His switches (Settings › Trade settings › Warnings) decide which types exist; a type he accepted today for this
//   contract, or a fact he already pressed Place on in this tab session, is not shown again.
// - Worst first: pure mistakes, then facts. Three lines visible, the rest behind "N more". A line never disables Place;
//   with "ask me" ON for its type, Place first asks him to accept that line (his own choice of an extra tap).
// No state, no network here (tested in checkCard.test.ts).

import { request } from "../api/client";
import { effectiveValue, saveTradeSettings, type TradeSettingsView } from "../settings/tradeSettings";
import type { RiskLine } from "./insights";
import { limitWords, type LegLiquidity } from "./riskParts";
import type { LiquidityLimits } from "../settings/liquidityLimits";

export type WarningId =
  | "duplicate_order" | "limit_far_from_ltp" | "margin_short" | "market_state" | "expiry_day" | "mis_cutoff" | "fo_ban"
  | "event" | "price_source" | "liquidity_limits" | "own_limits"
  // M76 MP-3: structure lines from the workspace's reading, one timeframe per line (after the facts: worst first)
  | "counter_trend" | "trend_conflict" | "area_conflict" | "strike_in_area" | "room";
export const WARNING_IDS: readonly WarningId[] = ["duplicate_order", "limit_far_from_ltp", "margin_short", "market_state", "expiry_day",
  "mis_cutoff", "fo_ban", "event", "price_source", "liquidity_limits", "own_limits",
  "counter_trend", "trend_conflict", "area_conflict", "strike_in_area", "room"];
export const MISTAKES: readonly WarningId[] = ["duplicate_order", "limit_far_from_ltp", "margin_short"];
export const VISIBLE_LINES = 3;
export const BUDGET_MS = 300;

export interface CheckLine { id: WarningId; severity: "mistake" | "fact"; text: string; ask: boolean }
export interface Switch { on: boolean; ask: boolean }
export type Switches = Record<WarningId, Switch>;

export interface ServerFinding { id: string; severity: "mistake" | "fact"; text: string }
export interface ChecksAnswer {
  findings: ServerFinding[]; pending: string[]; accepted: string[]; ms: number; instrument: string; budget_ms: number;
  switches: Record<string, Switch>;
}

const isId = (v: string): v is WarningId => (WARNING_IDS as readonly string[]).includes(v);

/** His switches from his Trade settings (his saved value, else the owner's start state). Unknown: every type ON, no ask. */
export function switchesOf(view: TradeSettingsView | null): Switches {
  const fields = new Map((view?.sections ?? []).flatMap((s) => s.fields).map((f) => [f.key, f]));
  const val = (key: string, fallback: boolean) => {
    const f = fields.get(key);
    const v = f ? effectiveValue(f) : null;
    return typeof v === "boolean" ? v : fallback;
  };
  return Object.fromEntries(WARNING_IDS.map((id) => [id, { on: val(`warnings.${id}`, true), ask: val(`warnings.${id}_ask`, false) }])) as Switches;
}

/** The screen's own figures as card lines: each warn-tone risk part under its type. */
export interface ScreenLines { margin?: RiskLine | null; event?: RiskLine | null; risk?: RiskLine | null; liquidity?: CheckLine | null }

const warnText = (l: RiskLine | null | undefined) => (l && l.tone === "warn" ? l.text : null);

/** His own liquidity limits only (amendment 1, M76Q-3 default): a line when a leg is outside them; never ATP's comparison. */
export function liquidityCheck(items: readonly LegLiquidity[], limits: LiquidityLimits | null): CheckLine | null {
  const own = limits && limits.on && (limits.max_spread_pct !== null || limits.min_oi !== null) ? limits : null;
  if (!own || !items.length) return null;
  const w = limitWords(items, own);
  return w.outside ? { id: "liquidity_limits", severity: "fact", text: w.text, ask: false } : null;
}

export interface Assemble {
  previewWarnings?: readonly string[]; previewIds?: readonly string[] | null;
  screen?: ScreenLines; server?: readonly ServerFinding[];
  switches: Switches; accepted?: readonly string[]; seen?: ReadonlySet<string>; hidden?: ReadonlySet<string>;
}

export const factKey = (l: Pick<CheckLine, "id" | "text">) => `${l.id}|${l.text}`;

/** Every line of the card, worst first, after his switches, today's accepted types and this session's seen facts. */
export function assembleLines(a: Assemble): CheckLine[] {
  const raw: { id: WarningId; text: string }[] = [];
  const ids = a.previewIds && a.previewIds.length === (a.previewWarnings ?? []).length ? a.previewIds : [];
  (a.previewWarnings ?? []).forEach((text, i) => {
    const id = ids[i];
    if (id && isId(id)) raw.push({ id, text });
  });
  const s = a.screen ?? {};
  const add = (id: WarningId, text: string | null | undefined) => { if (text) raw.push({ id, text }); };
  add("margin_short", warnText(s.margin));
  add("event", warnText(s.event));
  add("own_limits", warnText(s.risk));
  if (s.liquidity) raw.push({ id: s.liquidity.id, text: s.liquidity.text });
  for (const f of a.server ?? []) if (isId(f.id)) raw.push({ id: f.id, text: f.text });
  const out: CheckLine[] = [];
  const keys = new Set<string>();
  for (const r of raw) {
    const sw = a.switches[r.id];
    if (!sw?.on || a.accepted?.includes(r.id) || a.hidden?.has(r.id)) continue;
    const key = factKey(r);
    if (keys.has(key) || a.seen?.has(key)) continue;
    keys.add(key);
    out.push({ id: r.id, severity: MISTAKES.includes(r.id) ? "mistake" : "fact", text: r.text, ask: sw.ask });
  }
  return out.sort((x, y) => (x.severity === y.severity ? WARNING_IDS.indexOf(x.id) - WARNING_IDS.indexOf(y.id) : x.severity === "mistake" ? -1 : 1));
}

/** The preview's lines that are not warnings (a waiting order's own status line): shown as plain status, not in the card. */
export function statusLines(previewWarnings: readonly string[], previewIds: readonly string[] | null | undefined): string[] {
  // A server without `warning_ids` (an older release mid-deploy): every text stays a plain line, as before - never dropped.
  if (!previewIds || previewIds.length !== previewWarnings.length) return [...previewWarnings];
  return previewWarnings.filter((_, i) => previewIds[i] === "status" || !isId(previewIds[i]));
}

/** Lines whose type has "ask me" ON and that he has not accepted on this ticket yet: Place asks first. */
export function askFirst(lines: readonly CheckLine[], acceptedHere: ReadonlySet<string>): CheckLine[] {
  return lines.filter((l) => l.ask && !acceptedHere.has(factKey(l)));
}

/** The field "Edit" goes back to (data-ticket name), or null: the fields as they are. */
export function editField(id: WarningId): string | null {
  switch (id) {
    case "limit_far_from_ltp": return "price";
    case "mis_cutoff": return "product";
    case "margin_short": case "own_limits": case "duplicate_order": return "lots";
    default: return null;
  }
}

// --- tab session: facts he already pressed Place on (amendment 5: no repeated alert for the same fact in one session) ---
const SEEN = new Set<string>();
/** M76 MP-5: the order body with the types of the lines shown when he pressed Place (each once; none: the body as before).
 *  The server journals them on the fill only; no check, price or size reads them. */
export function withAccepted<T extends object>(body: T, lines: readonly CheckLine[]): T & { accepted_warnings?: string[] } {
  const ids = [...new Set(lines.map((l) => l.id))];
  return ids.length ? { ...body, accepted_warnings: ids } : body;
}

export const sessionSeen = (): ReadonlySet<string> => SEEN;
export function markSeen(lines: readonly CheckLine[]): void { lines.forEach((l) => SEEN.add(factKey(l))); }
export function resetSessionSeen(): void { SEEN.clear(); }

// --- calls (never awaited by Place) ------------------------------------------------------------------------------------
export interface CheckDraft {
  underlying: string; expiry: string; strike: number | null; right: string; side: string; lots: number;
  order_type: string | null; product: string | null; price: number | null; ltp: number | null;
  /** M76 MP-3 (M76Q-7): the workspace's open chart when the ticket opens beside it; absent: his signal timeframe + daily. */
  chart_instrument?: string; chart_tf?: string;
}

export const checksApi = {
  run: (draft: CheckDraft, only?: string[]) =>
    request<ChecksAnswer>("/paper/fno/order/checks", { method: "POST", body: JSON.stringify({ ...draft, ...(only ? { only } : {}) }) }),
  accept: (draft: Pick<CheckDraft, "underlying" | "expiry" | "strike" | "right">, types: string[]) =>
    request<void>("/paper/fno/order/checks/accept", { method: "POST", body: JSON.stringify({ ...draft, types }), keepalive: true }),
  dontShow: (id: WarningId) => saveTradeSettings({ [`warnings.${id}`]: false }),
};
export type ChecksApi = typeof checksApi;
