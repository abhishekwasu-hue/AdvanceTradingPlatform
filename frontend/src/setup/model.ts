import { SETUP_ADDRESS } from "../shell/aliases";
import { formatINR } from "../ui/format";
import type { ContractRules, DeploymentCreateRequest } from "../types";

/**
 * UI2-7 (UI v2 proposal §11, §13 UI-8): the setup wizard's model - the four steps, the server's answer
 * (`GET /api/me/setup`: each step "done" worked out from the trader's own rows, next to the stored skips and the chip's
 * dismissal), the Setup chip's words (Settings; P3 moved the dashboard to the Get started card, src/setup/getStarted.ts),
 * the Get started card's marks, the risk step's defaults and sentence, and the strategy step's three
 * suggestions. Pure functions only, so every rule here is unit-tested.
 */

export type SetupStepKey = "broker" | "risk" | "strategy" | "alerts";
export const SETUP_STEPS: readonly SetupStepKey[] = ["broker", "risk", "strategy", "alerts"];

/** Where the wizard lives: Settings → Setup (a v2-only address). */
export const SETUP_PATH = SETUP_ADDRESS;

export const STEP_TEXT: Record<SetupStepKey, { title: string; next: string }> = {
  broker: { title: "Connect your broker", next: "connect your broker" },
  risk: { title: "Set your risk limits", next: "set your risk limits" },
  strategy: { title: "Pick your first strategy", next: "pick your first strategy" },
  alerts: { title: "Choose your alerts", next: "choose your alerts" },
};

/** The trader's own marks (backend `app/me/prefs.py` SetupPrefs). "Done" is never stored. */
export interface SetupStored {
  steps: Record<SetupStepKey, { skipped_at: string | null }>;
  opened_at: string | null;
  dismissed_at: string | null;
  /** P3: the Dashboard's Get started card was hidden for good (all done, or hidden by the trader). */
  get_started_hidden_at: string | null;
  /** P3: the card was brought back from Help with every step done (it shows its ticks until hidden again). */
  get_started_shown_at: string | null;
}

/** P3: the Get started card's three steps (backend `app/me/setup.py` GET_STARTED). */
export type GetStartedKey = "broker" | "paper_bot" | "telegram";
export const GET_STARTED_STEPS: readonly GetStartedKey[] = ["broker", "paper_bot", "telegram"];

export interface SetupStepState { key: SetupStepKey; done: boolean; skipped: boolean; skipped_at: string | null }

/** `GET /api/me/setup` (the fields this app reads). `getStarted`: each card step done (worked out by the server); null
 * when the answer has none (an older cache), and then no card is shown rather than a guess. */
export interface SetupView {
  steps: SetupStepState[];
  stored: SetupStored;
  version: number;
  getStarted: Record<GetStartedKey, boolean> | null;
  /** Settings redesign D: what in the setup needs an action now (backend app/me/setup_health.py); null while `ui_v2` is
   * off or the answer has none. */
  health?: SetupHealthItem[] | null;
}

/** Settings redesign D: one setup item that needs the trader (`GET /api/me/setup` -> `health`). */
export type SetupHealthItem =
  | { key: "analytics_token"; state: "expired" | "expiring" }
  | { key: "ai_key"; state: "no_credit" | "failing"; message: string }
  | { key: "telegram"; state: "not_connected" }
  | { key: "data_feed"; state: "delayed" };

const HEALTH_STATES: Record<SetupHealthItem["key"], readonly string[]> = {
  analytics_token: ["expired", "expiring"], ai_key: ["no_credit", "failing"], telegram: ["not_connected"], data_feed: ["delayed"],
};

/** The server's `health` list, keeping only the items this app knows; null when absent or not a list. */
export function cleanHealth(raw: unknown): SetupHealthItem[] | null {
  if (!Array.isArray(raw)) return null;
  const out: SetupHealthItem[] = [];
  for (const item of raw as unknown[]) {
    const v = (item && typeof item === "object" ? item : {}) as Record<string, unknown>;
    const key = v.key as SetupHealthItem["key"];
    const state = typeof v.state === "string" ? v.state : "";
    if (!Object.prototype.hasOwnProperty.call(HEALTH_STATES, key) || !HEALTH_STATES[key].includes(state)) continue;
    if (key === "ai_key") out.push({ key, state: state as "no_credit" | "failing", message: typeof v.message === "string" ? v.message : "" });
    else out.push({ key, state } as SetupHealthItem);
  }
  return out;
}

const isoOrNull = (v: unknown): string | null => (typeof v === "string" && v && !Number.isNaN(Date.parse(v)) ? v : null);

/** Any value (the server's, the cache's, a 409's `current`) as valid stored marks: what is missing or wrong is unset. */
export function cleanStored(raw: unknown): SetupStored {
  const v = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const steps = (v.steps && typeof v.steps === "object" ? v.steps : {}) as Record<string, unknown>;
  const mark = (k: SetupStepKey) => {
    const m = (steps[k] && typeof steps[k] === "object" ? steps[k] : {}) as Record<string, unknown>;
    return { skipped_at: isoOrNull(m.skipped_at) };
  };
  return {
    steps: { broker: mark("broker"), risk: mark("risk"), strategy: mark("strategy"), alerts: mark("alerts") },
    opened_at: isoOrNull(v.opened_at),
    dismissed_at: isoOrNull(v.dismissed_at),
    get_started_hidden_at: isoOrNull(v.get_started_hidden_at),
    get_started_shown_at: isoOrNull(v.get_started_shown_at),
  };
}

/** The server's `get_started.steps` as done per step, or null when absent or not a list. */
function cleanGetStarted(raw: unknown): Record<GetStartedKey, boolean> | null {
  const g = (raw && typeof raw === "object" ? raw : null) as Record<string, unknown> | null;
  if (!g || !Array.isArray(g.steps)) return null;
  const done: Record<GetStartedKey, boolean> = { broker: false, paper_bot: false, telegram: false };
  for (const s of g.steps as unknown[]) {
    const step = (s && typeof s === "object" ? s : {}) as Record<string, unknown>;
    if (GET_STARTED_STEPS.includes(step.key as GetStartedKey)) done[step.key as GetStartedKey] = step.done === true;
  }
  return done;
}

/** The steps from stored marks and "done" per step: a done step is never also skipped. */
export function stepsOf(stored: SetupStored, done: Partial<Record<SetupStepKey, boolean>>): SetupStepState[] {
  return SETUP_STEPS.map((key) => {
    const isDone = !!done[key];
    const skippedAt = stored.steps[key].skipped_at;
    return { key, done: isDone, skipped: !isDone && skippedAt !== null, skipped_at: skippedAt };
  });
}

/** The server's answer as a view, or null when it is not one (then nothing is shown rather than a guess). */
export function cleanView(raw: unknown): SetupView | null {
  if (!raw || typeof raw !== "object") return null;
  const v = raw as Record<string, unknown>;
  if (!Array.isArray(v.steps) || typeof v.version !== "number") return null;
  const done: Partial<Record<SetupStepKey, boolean>> = {};
  for (const s of v.steps as unknown[]) {
    const step = (s && typeof s === "object" ? s : {}) as Record<string, unknown>;
    if (SETUP_STEPS.includes(step.key as SetupStepKey)) done[step.key as SetupStepKey] = step.done === true;
  }
  const stored = cleanStored(v.stored);
  return { steps: stepsOf(stored, done), stored, version: v.version, getStarted: cleanGetStarted(v.get_started), health: cleanHealth(v.health) };
}

/** The same view after a local change to the marks (before the server answers). */
export function withStored(view: SetupView, stored: SetupStored): SetupView {
  const done = Object.fromEntries(view.steps.map((s) => [s.key, s.done]));
  return { ...view, stored, steps: stepsOf(stored, done) };
}

/**
 * A step counts towards "complete" when done; the broker step also when skipped ("I'll trade on paper first", §11 step
 * 1). Any other skip only changes the order: the step stays open, after the ones not skipped.
 */
export const countsAsDone = (s: SetupStepState): boolean => s.done || (s.key === "broker" && s.skipped);

/** The next step: the first not done, preferring one not skipped; null once setup is complete. */
export function nextStep(view: SetupView): SetupStepKey | null {
  const open = view.steps.filter((s) => !countsAsDone(s));
  return (open.find((s) => !s.skipped) ?? open[0])?.key ?? null;
}

export const isComplete = (view: SetupView): boolean => nextStep(view) === null;

/**
 * The Setup chip (Settings, until setup is complete): "Setup 2 of 4" and "Next: set your risk limits".
 * The number is the step the trader is on (steps done, plus one). It stays until setup is complete (§11), or (unless
 * `ignoreDismissal`, Settings) until the trader closes it.
 */
export function setupChip(view: SetupView | null, opts: { ignoreDismissal?: boolean } = {}): { label: string; next: string; to: string } | null {
  if (!view) return null;
  const next = nextStep(view);
  if (!next) return null;
  if (view.stored.dismissed_at && !opts.ignoreDismissal) return null;
  const done = view.steps.filter(countsAsDone).length;
  return { label: `Setup ${done + 1} of ${SETUP_STEPS.length}`, next: `Next: ${STEP_TEXT[next].next}`, to: SETUP_PATH };
}

/** The step the wizard shows when it opens: the next step (past skipped ones), or null when setup is complete. */
export function openingStep(view: SetupView | null): SetupStepKey | null {
  return view ? nextStep(view) : SETUP_STEPS[0];
}

/** The step after `key` that is neither done nor skipped (wrapping round), or null (then the wizard shows its summary). */
export function stepAfter(view: SetupView, key: SetupStepKey): SetupStepKey | null {
  const at = SETUP_STEPS.indexOf(key);
  const fresh = (s: SetupStepState) => !countsAsDone(s) && !s.skipped;
  return view.steps.slice(at + 1).find(fresh)?.key ?? view.steps.slice(0, at).find(fresh)?.key ?? null;
}

export function skipStep(stored: SetupStored, key: SetupStepKey, nowIso: string): SetupStored {
  return { ...stored, steps: { ...stored.steps, [key]: { skipped_at: nowIso } } };
}

export const markOpened = (stored: SetupStored, nowIso: string): SetupStored => ({ ...stored, opened_at: stored.opened_at ?? nowIso });
export const dismissChip = (stored: SetupStored, nowIso: string): SetupStored => ({ ...stored, dismissed_at: nowIso });

// --- Step 2: risk limits ------------------------------------------------------------------------------------------------

/** TT-12 (owner decision, 24 Oct): no limit is filled in for the trader - every limit is off until he sets his own. Only
 * the paper account's starting capital starts from a value (his account's, else ₹1,00,000). */
export const RISK_DEFAULTS = { capital: 100_000 } as const;

/** The server's bounds (backend `risk_engine/by_default.py` BOUNDS); the paper capital's own (PAPER_MIN / MAX_CAPITAL). M68 PR-5
 * (TS1-049): only "a real number of the right sign" - a % above 0 and at most 100, a count or minimum finite; no ATP caps. */
export const RISK_BOUNDS = {
  capital: [10_000, 1_000_000_000], riskPerTradePct: [0, 100], dailyLossPct: [0, 100], maxTradesPerDay: [0, 1_000_000_000], minRr: [0, 1_000_000_000],
} as const;
/** The limits that must be above their bottom (a % of 0 is no limit, left empty instead). */
const ABOVE_LOW: ReadonlySet<string> = new Set(["riskPerTradePct", "dailyLossPct"]);

export type RiskField = keyof typeof RISK_BOUNDS;
export type RiskForm = Record<RiskField, string>;
/** The four limits (every field but the capital); an empty one is "Not set" = no limit. */
export const LIMIT_FIELDS = ["riskPerTradePct", "dailyLossPct", "maxTradesPerDay", "minRr"] as const satisfies readonly RiskField[];
export type LimitField = (typeof LIMIT_FIELDS)[number];
/** Each limit's key on the server (`PUT /paper/risk`). */
export const LIMIT_KEYS = {
  riskPerTradePct: "risk_per_trade_pct", dailyLossPct: "daily_loss_pct", maxTradesPerDay: "max_trades_per_day", minRr: "min_rr",
} as const satisfies Record<LimitField, string>;

export const RISK_LABELS: Record<RiskField, string> = {
  capital: "Starting capital", riskPerTradePct: "Risk per trade", dailyLossPct: "Daily loss limit", maxTradesPerDay: "Trades a day",
  minRr: "Lowest reward to risk",
};

/** What each limit does, in plain words (shown under its box). */
export const RISK_HELP: Record<LimitField, string> = {
  riskPerTradePct: "The most one trade may lose at its stop, as a % of your capital.",
  dailyLossPct: "New trades stop for the day once your day's loss reaches this % of your capital.",
  maxTradesPerDay: "New trades allowed in one day.",
  minRr: "A trade whose target pays less than this for each rupee at risk is refused.",
};

const rupees = (v: number) => formatINR(v);   // the money rule: whole rupees (src/ui/format.ts)

/** "With these limits the most you can lose today is ₹3,000." - or null while a figure is not set or not a valid number. */
export function riskSentence(capital: number | null, dailyLossPct: number | null): string | null {
  if (capital === null || dailyLossPct === null || !Number.isFinite(capital) || !Number.isFinite(dailyLossPct) || capital <= 0 || dailyLossPct <= 0) return null;
  return `With these limits the most you can lose today is ${rupees((capital * dailyLossPct) / 100)}.`;
}

/** The most one trade may lose: "At most ₹1,000 on one trade." */
export function perTradeText(capital: number | null, riskPct: number | null): string | null {
  if (capital === null || riskPct === null || !Number.isFinite(capital) || !Number.isFinite(riskPct) || capital <= 0 || riskPct <= 0) return null;
  return `At most ${rupees((capital * riskPct) / 100)} on one trade.`;
}

/** What is wrong with each field, in plain words (empty = all good). A limit may be left empty (not set = no limit); the
 * capital may not. `capital: false` = the capital field is not shown. */
export function riskErrors(form: RiskForm, opts: { capital?: boolean } = {}): Partial<Record<RiskField, string>> {
  const out: Partial<Record<RiskField, string>> = {};
  for (const field of Object.keys(RISK_BOUNDS) as RiskField[]) {
    if (field === "capital" && opts.capital === false) continue;
    const [low, high] = RISK_BOUNDS[field];
    const raw = form[field].trim().replace(/,/g, "");
    if (!raw && field !== "capital") continue;
    const v = Number(raw);
    const whole = field === "maxTradesPerDay" || field === "capital";
    const show = (n: number) => (field === "capital" ? rupees(n) : String(n));
    if (!raw || !Number.isFinite(v)) out[field] = `Enter a number for ${RISK_LABELS[field].toLowerCase()}.`;
    else if (whole && !Number.isInteger(v)) out[field] = `Enter a whole number for ${RISK_LABELS[field].toLowerCase()}.`;
    else if (ABOVE_LOW.has(field) && (v <= low || v > high)) out[field] = `${RISK_LABELS[field]} must be more than ${show(low)} and at most ${show(high)}.`;
    else if (v < low || v > high) {
      out[field] = high >= 1_000_000_000 && field !== "capital" ? `${RISK_LABELS[field]} must be at least ${show(low)}.`
        : `${RISK_LABELS[field]} must be between ${show(low)} and ${show(high)}.`;
    }
  }
  return out;
}

/** Each field as a number, or null when it is empty (a limit not set). */
export const riskNumbers = (form: RiskForm): Record<RiskField, number | null> =>
  Object.fromEntries((Object.keys(form) as RiskField[]).map((k) => {
    const raw = form[k].trim().replace(/,/g, "");
    return [k, raw ? Number(raw) : null];
  })) as Record<RiskField, number | null>;

/** The body of `PUT /paper/risk`: every limit, null for one left empty (not set = off; clearing one he set waits for
 * the next day on the server). */
export function limitsBody(n: Record<RiskField, number | null>): Record<(typeof LIMIT_KEYS)[LimitField], number | null> {
  return Object.fromEntries(LIMIT_FIELDS.map((f) => [LIMIT_KEYS[f], n[f]])) as Record<(typeof LIMIT_KEYS)[LimitField], number | null>;
}

/** A limit as Settings shows it: "Not set" unless the trader set it himself (TT-12). */
export function limitText(field: LimitField, set: readonly string[] | undefined, value: number | null | undefined): string {
  if (!set?.includes(LIMIT_KEYS[field]) || value === null || value === undefined || !Number.isFinite(value)) return "Off";
  if (field === "maxTradesPerDay") return value === 0 ? "No limit" : `${value} a day`;
  const shown = String(Math.round(value * 100) / 100);                 // "1.5", never "1.50"
  if (field === "minRr") return value === 0 ? "No minimum" : `${shown} : 1`;
  return `${shown}% of capital`;
}

// --- Step 3: three suggested Library strategies, PAPER only ------------------------------------------------------------

export interface StrategySuggestion {
  style: "Intraday" | "Swing" | "Options";
  strategyId: string;
  symbol: string;
  /** The base candles the Autopilot reads ("day" = daily, held overnight). */
  timeframe: string;
  line: string;
  /** Traded as an option of the symbol instead of the symbol itself. */
  option?: true;
}

/** One per style (§11). Ids are the Library's built-in strategies; one missing from the Library is not shown. */
export const SUGGESTIONS: readonly StrategySuggestion[] = [
  { style: "Intraday", strategyId: "vwap_supertrend_5m", symbol: "NIFTY 50", timeframe: "1min",
    line: "Trades NIFTY 50 during the day and closes before the market does." },
  { style: "Swing", strategyId: "swing_ema_pullback_d", symbol: "RELIANCE", timeframe: "day",
    line: "Looks for a pullback inside an uptrend on RELIANCE and holds for days." },
  { style: "Options", strategyId: "orb_15m_5m", symbol: "NIFTY 50", timeframe: "1min", option: true,
    line: "Looks for a break of the first 15 minutes' range on NIFTY 50; you choose the option on the Trading bot form." },
];

/** REV-1 M2 (decision D3): Setup's "Start a trading bot" opens the Trading bot form with nothing pre-chosen. */
export const START_BOT_PATH = "/deployments";

/** The deployment "Start on Paper Autopilot" created before REV-1 M2 (D3). Kept as a helper; the Setup step no longer
 * starts a bot in one tap. Always PAPER: this screen never offers LIVE. M68 PR-4 (TS1-040): the pre-chosen contract (a
 * bought ATM option at the nearest expiry) is gone - the option's side, expiry and strike are his own (the server takes
 * his Trade settings › Options and asks for one that is Not set). */
export function paperDeployment(s: StrategySuggestion): DeploymentCreateRequest & { mode: "PAPER" } {
  const contract: ContractRules = s.option ? { instrument_kind: "OPTION" } : { instrument_kind: "UNDERLYING" };
  return {
    strategy_id: s.strategyId, symbol: s.symbol, exchange: "NSE", timeframe: s.timeframe, mode: "PAPER",
    holding: s.timeframe === "day" ? "SWING" : "INTRADAY", ...contract,
  };
}

/** A quick test's result in one line: "12 trades · 58% won · +₹4,250 on 30 days of NIFTY 50 prices". */
export function testSummary(r: { total_trades: number; win_rate: number; net_pnl: number }, days: number, symbol: string): string {
  if (!r.total_trades) return `No trades on the last ${days} days of ${symbol} prices.`;
  const sign = r.net_pnl > 0 ? "+" : r.net_pnl < 0 ? "−" : "";
  const won = Math.round(r.win_rate);                  // the engine's win rate is a percentage
  return `${r.total_trades} trade${r.total_trades === 1 ? "" : "s"} · ${won}% won · ${sign}${rupees(Math.abs(r.net_pnl))} on the last ${days} days of ${symbol} prices.`;
}
