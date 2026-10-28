/**
 * DEF-0 D9 (owner, 28 Oct): "My limits" - every limit the trader may set, each blank = "Not set (no limit)". Nothing is
 * pre-filled: the platform has no default for any of them. The numbers are the risk settings (PUT /api/risk-settings);
 * the two switches are the `my_limits` preferences the server reads (experience levels, a stop on a sold option).
 */
import { request } from "../api/client";
import type { RiskConfig } from "../types";

export type LimitKey = "capital" | "risk_per_trade_pct" | "max_daily_loss_pct" | "max_trades_per_day" | "max_open_positions"
  | "max_consecutive_losses" | "max_portfolio_risk_pct" | "stop_cooldown_minutes" | "dd_level_1_pct" | "dd_level_2_pct" | "event_size_cut_pct";

export interface LimitField { key: LimitKey; label: string; unit: string; help: string; whole?: boolean }

export const LIMIT_FIELDS: readonly LimitField[] = [
  { key: "capital", label: "My capital", unit: "₹", whole: true,
    help: "What the % limits below are counted on. Not set: your PAPER balance is used." },
  { key: "risk_per_trade_pct", label: "Risk per trade", unit: "% of capital",
    help: "The most one trade may lose at its stop. Set: the size is worked out from it." },
  { key: "max_daily_loss_pct", label: "Daily loss limit", unit: "% of capital",
    help: "Once today's loss reaches it, no new entry today. Exits always work." },
  { key: "max_trades_per_day", label: "Trades a day", unit: "trades", whole: true, help: "No new entry after this many today." },
  { key: "max_open_positions", label: "Open positions", unit: "positions", whole: true, help: "No new entry while you hold this many." },
  { key: "max_consecutive_losses", label: "Losses in a row", unit: "losses", whole: true, help: "After this many losses in a row, new entries pause." },
  { key: "max_portfolio_risk_pct", label: "Open risk, all positions", unit: "% of capital",
    help: "The loss if every open stop is hit, plus the new trade, stays under this." },
  { key: "stop_cooldown_minutes", label: "Pause after a stop-out", unit: "minutes", whole: true,
    help: "No new entry in the same symbol for this long after a stop." },
  { key: "dd_level_1_pct", label: "Halve size at drawdown", unit: "% below peak", help: "This far below your equity peak, the risk per trade is halved." },
  { key: "dd_level_2_pct", label: "Pause at drawdown", unit: "% below peak", help: "This far below your equity peak, new entries pause." },
  { key: "event_size_cut_pct", label: "Event-day size cut", unit: "%", help: "On a market event marked as a size cut, the risk per trade is cut by this." },
];

export const NOT_SET = "Not set (no limit)";

export interface MyLimitSwitches { limit_by_experience: boolean; stop_required_on_sold: boolean }
export const SWITCHES_OFF: MyLimitSwitches = { limit_by_experience: false, stop_required_on_sold: false };

export const SWITCH_FIELDS: readonly { key: keyof MyLimitSwitches; label: string; help: string }[] = [
  { key: "stop_required_on_sold", label: "A sold option or a future needs a stop-loss",
    help: "On: the order ticket asks for a stop-loss on a sold option or a future. Off: the stop is your choice." },
  { key: "limit_by_experience", label: "Limit me by my experience level",
    help: "On: futures and naked short options open as your paper record grows. Off: nothing is held back by level." },
];

/** The text field values ("" = not set) of a saved config. */
export type LimitsText = Record<LimitKey, string>;

export function textOf(config: Partial<RiskConfig> | null | undefined): LimitsText {
  const out = {} as LimitsText;
  for (const f of LIMIT_FIELDS) {
    const v = config?.[f.key];
    out[f.key] = typeof v === "number" && Number.isFinite(v) ? String(v) : "";
  }
  return out;
}

/** The config to save from the text fields (blank = null = not set), or the first problem in plain words. */
export function configOf(text: LimitsText, base: Partial<RiskConfig> | null | undefined): { config: RiskConfig | null; problem: string | null } {
  const config = { min_risk_reward: 0, lot_size: 1, ...(base ?? {}) } as RiskConfig;
  for (const f of LIMIT_FIELDS) {
    const raw = (text[f.key] ?? "").replace(/,/g, "").trim();
    if (raw === "") { config[f.key] = null; continue; }
    const v = Number(raw);
    if (!Number.isFinite(v) || v <= 0) return { config: null, problem: `${f.label}: enter a number above 0, or leave it blank for no limit.` };
    if (f.whole && !Number.isInteger(v)) return { config: null, problem: `${f.label}: enter a whole number.` };
    config[f.key] = v;
  }
  return { config, problem: null };
}

export function countSet(config: Partial<RiskConfig> | null | undefined): number {
  return LIMIT_FIELDS.filter((f) => typeof config?.[f.key] === "number").length;
}

interface PrefsRead { prefs?: Record<string, unknown>; versions?: Record<string, number> }

export function readSwitches(raw: unknown): MyLimitSwitches {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  return { limit_by_experience: r.limit_by_experience === true, stop_required_on_sold: r.stop_required_on_sold === true };
}

export async function loadSwitches(fetcher: typeof request = request): Promise<{ value: MyLimitSwitches; version: number }> {
  const r = await fetcher<PrefsRead>("/me/prefs");
  return { value: readSwitches(r.prefs?.my_limits), version: r.versions?.my_limits ?? 0 };
}

export async function saveSwitches(value: MyLimitSwitches, version: number | null, fetcher: typeof request = request):
  Promise<{ value: MyLimitSwitches; version: number }> {
  const q = version === null ? "" : `?version=${version}`;
  // Off is stored as not set (null): the server reads only an explicit true.
  const body = { limit_by_experience: value.limit_by_experience || null, stop_required_on_sold: value.stop_required_on_sold || null };
  const r = await fetcher<{ value: unknown; version: number }>(`/me/prefs/my_limits${q}`, { method: "PUT", body: JSON.stringify(body) });
  return { value: readSwitches(r.value), version: r.version };
}
