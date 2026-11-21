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
  /** P2-8 J2 / J3: the feelings offered in the check-in and the trades needed before any percentage. */
  feelings?: string[];
  min_closed_for_review?: number;
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
  /** P2-8 J1-1: "BROKER" = read from his own broker's trade book. */
  origin?: string | null;
  /** P2-8 J1-3: a structure's legs folded into this one row. */
  spread?: SpreadSummary;
  legs?: SpreadLegRow[];
}

/** P2-8 J1-3: one structure's combined numbers (from its own legs and the structure facts it was placed with). */
export interface SpreadSummary {
  group_id: string | null;
  structure: string | null;
  underlying: string | null;
  legs: number;
  closed: boolean;
  net_pnl: number | null;
  credit: number;
  credit_kind: "credit" | "debit" | null;
  planned_max_loss: number | null;
  actual_loss: number | null;
  charges: number;
}

export interface SpreadLegRow {
  id: number; symbol: string; direction: string; quantity: number; entry_price: number; exit_price: number | null; pnl: number | null;
}

/** P2-8 J1-1: what one "Sync from broker" did. */
export interface BrokerSyncResult {
  brokers: number; read: number; matched: number; added: number; closed: number; already: number; messages: string[]; entry_ids: number[];
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

/** M99 P7 (flag risk_options): the discipline numbers - counted only, nothing is blocked by them. */
export interface JournalRiskMetrics {
  closed: number;
  max_drawdown: number;
  longest_win_streak: number;
  longest_loss_streak: number;
  streak_now: { kind: "win" | "loss" | null; length: number };
  breaches: { loss_beyond_plan: number; no_named_setup: number; total: number };
  note: string;
}

export interface JournalStatsResponse {
  totals: StatsGroup;
  /** M99 P7: only with the `risk_options` flag on. */
  risk_metrics?: JournalRiskMetrics;
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
  /** M76 MP-5: the check-card lines shown when he pressed Place (absent on older answers). */
  accepted_warnings?: { id: string; label: string; at: string | null }[];
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
  /** M88: an outside-ATP entry also has its own chart (entry and exit marked) and that chart as an image. */
  charts: { entry: string; exit: string | null; trade?: string; trade_image?: string };
  /** M88: a manual entry that came from a file he imported. */
  imported?: boolean;
  /** P2-8 J1-1: "BROKER" = read from his own broker's trade book. */
  origin?: string | null;
  /** P2-8 J1-2: which chart pictures are stored (an ATP trade). */
  snapshots?: { entry: boolean; exit: boolean };
  /** P2-8 J1-3: the structure this leg belongs to, with every leg. */
  spread?: SpreadSummary & { leg_rows: SpreadLegRow[] };
  /** P2-8 J1-4: plan vs actual, one line per difference (empty = as planned). */
  plan_actual?: string[];
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
/** M76 MP-5: per check-card type he pressed Place with this week, the trades and how they ended (numbers only). */
export interface AcceptedWarningRow { id: string; label: string; trades: number; closed: number; wins: number; losses: number; net_pnl: number }

export type JournalWeekV2 = Omit<JournalWeek, "entries"> & { entries: (JournalEntry & Partial<JournalEntryFull>)[]; manual_entries?: JournalEntryFull[];
  accepted_warnings?: AcceptedWarningRow[] };

/** M88: one column of an import and the file's header it is read from (null: not in this file). */
export type ImportMapping = Record<string, string | null>;

/** M88: one round-trip trade an import would add. */
export interface ImportTrade {
  symbol: string; exchange: string | null; instrument_kind: string; direction: "LONG" | "SHORT"; quantity: number;
  entry_time: string; entry_price: number; exit_time: string | null; exit_price: number | null; charges: number;
  pnl: number | null; stop_loss: number | null; rows: number[]; date_only: boolean; duplicate: boolean;
}

/** M88: POST /journal/import/preview - what importing the file would add; nothing is stored. */
export interface ImportPreview {
  file: { name: string; size: number; hash: string };
  layout: { id: string; label: string };
  shape: "fills" | "trades";
  pairing: string | null;
  headers: string[];
  mapping: ImportMapping;
  fields: { key: string; label: string }[];
  counts: { rows: number; problems: number; trades: number; duplicates: number; to_import: number; open: number; date_only: number };
  problems: { row: number; reason: string }[];
  trades: ImportTrade[];
  already_imported: { at: string; file_name: string; trades_left: number } | null;
  limits: { max_bytes: number; max_rows: number; max_trades: number };
}

/** M88: POST /journal/import. */
export interface ImportResult { imported: number; duplicates: number; problems: number; import_id: number; already_imported: boolean; entry_ids: number[] }

/** M88: GET /journal/manual/{id}/trade-chart - the candles around his outside trade with its entry and exit marked. */
export interface TradeChartMark {
  kind: "entry" | "exit"; label: string; at: string; price: number; side: "BUY" | "SELL"; bar: string | null;
  /** Journal option chart: false when the chart is the contract's underlying - the price is the contract's premium, not a price on this chart. */
  on_chart?: boolean;
}
export interface TradeChart {
  symbol: string; exchange: string; direction: "LONG" | "SHORT"; timeframe: string; timeframe_label: string; note: string;
  candles: { timestamp: string; open: number; high: number; low: number; close: number; volume: number }[];
  marks: TradeChartMark[]; levels: { kind: string; label: string; price: number }[];
  /** Owner 19 Nov: the timeframe answered and the ones he may pick (absent from an older server: Daily). */
  tf?: string; timeframes?: string[];
  missing: boolean; missing_text: string | null; image: string;
}

// --- P2-8 J2: the 20-second check-in (his own answers; private) --------------------------------------------------------

export type PlanAnswer = "YES" | "PARTLY" | "NO";

export interface CheckinNote { at: string; text: string }

export interface Checkin {
  feeling_before: string | null;
  confidence: number | null;
  feeling_after: string | null;
  plan_followed: PlanAnswer | null;
  mistake: string | null;
  lesson: string | null;
  setup_name: string | null;
  setup_name_suggested?: string | null;
  notes: CheckinNote[];
  feelings: string[];
  mistakes: string[];
  plan_answers: PlanAnswer[];
  answered: boolean;
}

export type CheckinBody = Partial<Pick<Checkin, "feeling_before" | "confidence" | "feeling_after" | "plan_followed" | "mistake" | "lesson" | "setup_name">>;

export interface WaitingCheckin { kind: EntryKind; id: number; symbol: string; moment: "open" | "close"; mode: string | null; at: string }

// --- P2-8 J3: the reviews ------------------------------------------------------------------------------------------------

export interface ReviewGate { ready: boolean; closed_total: number; needed: number; note?: string }

export interface PlanCounts { yes: number; partly: number; no: number; answered: number; unanswered: number; rate: number | null }

export interface DailyCard {
  day: string; trades: number; closed: number; net_pnl: number | null; plan: PlanCounts;
  feelings: { feeling: string; count: number }[];
  confirmed_tags: { tag: string; label: string; count: number }[];
  done_well: string | null; lines: string[]; gate: ReviewGate;
  entries: { kind: EntryKind; id: number; symbol: string; pnl: number | null }[];
}

export interface ResultRow { value: string | number; trades: number; wins: number; losses: number; net_pnl: number; avg_pnl: number; win_rate: number | null; small: boolean }

export interface WeeklyExtras {
  week: string; gate: ReviewGate; plan: PlanCounts; feelings: ResultRow[]; feelings_after: ResultRow[]; confidence: ResultRow[];
  mistake: { value: string; label: string; count: number } | null; lines: string[];
  notable: { kind: EntryKind; id: number; symbol: string; pnl: number | null; chart: string | null }[];
  done_well: string | null; question: string; answer: string | null;
}

export interface MonthlyReview {
  month: string; gate: ReviewGate; trades: number; closed: number; net_pnl: number | null; plan: PlanCounts;
  weeks: { week: string; score: number | null; plan: PlanCounts; trades: number }[];
  setups: { setup: string; trades: number; net_pnl: number; win_rate: number | null; small: boolean }[];
  best_setup: { setup: string; net_pnl: number; trades: number; small: boolean } | null;
  worst_setup: { setup: string; net_pnl: number; trades: number; small: boolean } | null;
  lines: string[]; done_well: string | null; question: string; answer: string | null;
}
