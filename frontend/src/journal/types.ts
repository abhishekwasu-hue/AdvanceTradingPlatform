/** Z6 / P1-e: the discipline engine's API shapes (`/api/journal/*`, backend `app/journal`). */

export type TagStatus = "SUGGESTED" | "CONFIRMED" | "DISMISSED";

export interface JournalTag {
  id: number;
  trade_id: number;
  tag: string;
  source: "AUTO" | "USER";
  status: TagStatus;
  evidence: Record<string, unknown>;
  detected_at: string | null;
  decided_at: string | null;
}

export interface JournalFill {
  leg: "ENTRY" | "EXIT";
  side: string;
  price: number;
  quantity: number;
  at: string | null;
  order_id?: string | null;
  slippage?: number | null;
  reason?: string | null;
}

export interface JournalEntry {
  trade_id: number;
  symbol: string;
  mode: string;
  direction: string;
  setup: string;
  source: string;
  instrument_kind: string;
  leg_group_id: string | null;
  entry_time: string | null;
  exit_time: string | null;
  plan: { entry?: number; entry_source?: string; stop?: number; target1?: number | null; target2?: number | null; rr?: number | null; setup?: string };
  execution: { entry_price: number; quantity: number; slippage: number | null; exit_price: number | null; exit_reason: string | null; stop_now: number };
  fills: JournalFill[];
  costs: { charges: number; source: string };
  pnl: number | null;
  r_multiple: number | null;
  risk_amount: number;
  holding_minutes: number | null;
  excursions: { mae: number | null; mfe: number | null; mae_r: number | null; mfe_r: number | null };
  stop_history: { at: string; from: number; to: number; source: string }[];
  chart_entry: { symbol: string; at: string } | null;
  chart_exit: { symbol: string; at: string } | null;
  greeks_entry: Record<string, number | string> | null;
  iv_entry: number | null;
  notes: string | null;
  tags: JournalTag[];
}

export interface ScoreComponent {
  name: string;
  tags: string[];
  weight: number;
  weight_share: number;
  decisions: number;
  flagged: number;
  clean_pct: number | null;
  points: number;
  max_points: number;
  by_tag: Record<string, number>;
}

export interface DisciplineScore {
  score: number | null;
  decisions: number;
  trades: number;
  components: ScoreComponent[];
  count_unconfirmed: boolean;
  /** AUD-V4: "n/a until a trade closes" (open trades only) or "(N open · M closed)"; null with no decisions. */
  note?: string | null;
  open?: number;
  closed?: number;
}

export interface JournalWeek {
  week: string;
  start: string;
  end: string;
  entries: JournalEntry[];
  score: DisciplineScore;
}

export interface ReviewStats {
  /** decisions: a multi-leg structure counts once (its legs summed) */
  trades: number;
  decisions: number;
  /** order legs (trade rows) behind those decisions */
  legs: number;
  closed: number;
  open: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  net_pnl: number;
  gross_pnl: number;
  charges: number;
  avg_r: number | null;
  expectancy: number | null;
  profit_factor: number | null;
  largest_win: number | null;
  largest_loss: number | null;
  avg_holding_minutes: number | null;
}

export interface WeeklyReview {
  week: string;
  start: string;
  end: string;
  totals: ReviewStats;
  by_setup: (ReviewStats & { setup: string })[];
  what_worked: (ReviewStats & { setup: string })[];
  what_lost: (ReviewStats & { setup: string })[];
  behaviours: { tag: string; label: string; trades: number; closed: number; net_pnl: number; avg_r: number | null }[];
  costliest: { tag: string; label: string; net_pnl: number; closed: number } | null;
  discipline: DisciplineScore;
  history: { week: string; score: number | null; trades: number; net_pnl: number; avg_r: number | null; win_rate: number | null }[];
  lines: string[];
  disclaimer: string;
  explanation: { source: "rules" | "ai"; text: string; note?: string };
}

export interface DisciplineSettings {
  revenge_window_minutes: number;
  revenge_size_multiple: number;
  fomo_chase_r: number;
  overtrading_daily_norm: number;
  overtrading_lookback_days: number;
  overtrading_multiple: number;
  overtrading_min_norm: number;
  event_kinds: string[];
  count_unconfirmed: boolean;
  weights: Record<string, number>;
  /** M62 JN-2: his own mistake / emotion tags, his rule checklist and the image size limit (MB). */
  custom_tags?: string[];
  checklist_rules?: string[];
  attachment_max_mb?: number;
}

export interface JournalSettingsResponse {
  settings: DisciplineSettings;
  defaults: DisciplineSettings;
  ranges: Record<string, [number, number]>;
  components: Record<string, string[]>;
  tags: string[];
}

// --- M62 JN-3: the journal list, the entry editor, the manual form and the statistics ---------------------------------

export type EntryKind = "trade" | "manual";
export type Outcome = "win" | "loss" | "flat" | "open";

/** One row of GET /journal/entries: an ATP trade row or a manual (outside-ATP) entry. */
export interface JournalListRow {
  kind: EntryKind;
  id: number;
  unit: string;
  source: string;
  source_label: string | null;
  outside_atp: boolean;
  mode: string | null;
  symbol: string;
  direction: string;
  setup: string | null;
  timeframe: string | null;
  entry_time: string | null;
  exit_time: string | null;
  entry_price: number;
  exit_price: number | null;
  quantity: number;
  charges: number | null;
  pnl: number | null;
  r_multiple: number | null;
  outcome: Outcome;
  exit_reason: string | null;
  tags: { id: number; tag: string; status: TagStatus; source: string }[];
  user_tags: string[];
  notes: string | null;
  attachments: number;
  chart: string;
  mae_r: number | null;
  mfe_r: number | null;
  leg_group_id: string | null;
}

export interface JournalFilters {
  setup?: string;
  tag?: string;
  outcome?: Outcome | "";
  date_from?: string;
  date_to?: string;
  include_manual?: boolean;
}

export interface JournalList {
  entries: JournalListRow[];
  total: number;
  truncated: boolean;
  setups: string[];
  tags: string[];
}

export interface StatsGroup {
  trades: number;
  closed: number;
  open: number;
  wins: number;
  losses: number;
  flat: number;
  outside_atp: number;
  win_rate: number | null;
  net_pnl: number;
  gross_win: number;
  gross_loss: number;
  profit_factor: number | null;
  expectancy: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  avg_r: number | null;
  r_trades: number;
  avg_mae_r: number | null;
  avg_mfe_r: number | null;
  avg_mae: number | null;
  avg_mfe: number | null;
  excursion_trades: number;
}

export interface JournalStatsResponse {
  totals: StatsGroup;
  by_setup: (StatsGroup & { setup: string })[];
  by_timeframe: (StatsGroup & { timeframe: string })[];
  by_weekday: (StatsGroup & { weekday: string })[];
  truncated: boolean;
  setups: string[];
  tags: string[];
}

export interface ChecklistItem { rule: string; followed: boolean | null; retired: boolean }

export interface JournalAttachment {
  id: number; trade_id: number | null; manual_trade_id: number | null; size_bytes: number; width: number; height: number; created_at: string | null;
}

export interface PlanView {
  setup: string | null;
  timeframe: string | null;
  area: string[] | null;
  invalidation: number | null;
  target: number | null;
  entry: number | null;
  entry_source: string | null;
}

/** GET /journal/trades/{id} or /journal/manual/{id}: one entry with the trader's own layer (M62 JN-2). */
export interface JournalEntryFull {
  kind: EntryKind;
  id: number;
  manual: boolean;
  source: string;
  source_label?: string;
  symbol: string;
  mode: string | null;
  direction: string;
  setup: string | null;
  entry_time: string | null;
  exit_time: string | null;
  plan_view: PlanView;
  execution: { entry_price: number; quantity: number; slippage: number | null; exit_price: number | null; exit_reason: string | null };
  fills: JournalFill[];
  costs: { charges: number | null; source: string };
  pnl: number | null;
  r_multiple: number | null;
  risk_amount: number;
  holding_minutes: number | null;
  excursions?: { mae: number | null; mfe: number | null; mae_r: number | null; mfe_r: number | null };
  notes: string | null;
  tags: JournalTag[];
  user_tags: string[];
  checklist: ChecklistItem[];
  attachments: JournalAttachment[];
  charts: { entry: string; exit: string | null };
}

/** POST /journal/manual: a trade done outside ATP, as the trader records it. */
export interface ManualTradeBody {
  symbol: string;
  direction: "LONG" | "SHORT";
  quantity: number;
  entry_time: string;
  entry_price: number;
  stop_loss?: number | null;
  target?: number | null;
  exit_time?: string | null;
  exit_price?: number | null;
  exit_reason?: string | null;
  charges?: number | null;
  setup?: string | null;
  timeframe?: string | null;
  area?: string | null;
  notes?: string | null;
}

/** The week view with M62's manual entries and the per-entry layer. */
export type JournalWeekV2 = Omit<JournalWeek, "entries"> & { entries: (JournalEntry & Partial<JournalEntryFull>)[]; manual_entries?: JournalEntryFull[] };
