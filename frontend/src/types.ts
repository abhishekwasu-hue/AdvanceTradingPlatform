export interface TokenResponse {
  access_token: string;
  token_type: string;
  refresh_token?: string | null;
  expires_in?: number | null;
  mfa_required?: boolean;
  mfa_token?: string | null;
}

export interface MfaStatus {
  enabled: boolean;
  enabled_at: string | null;
  pending_enrolment: boolean;
  backup_codes_remaining: number;
  session_verified: boolean;
  required_for_live: boolean;
}

export interface LoginEvent {
  id: number;
  success: boolean;
  reason: string;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
}

export interface SessionInfo {
  id: number;
  current: boolean;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
  last_used_at: string;
  expires_at: string;
}

export interface UserResponse {
  id: number;
  email: string;
  tenant_id: number;
  role: string;
  mfa_enabled?: boolean;
  email_verified?: boolean;
  scopes?: string[];
}

export interface TradeRecord {
  id: number;
  regime_at_entry?: string | null;
  notes?: string | null;
  tags?: string[];
  mode: string;
  symbol: string;
  strategy_id: string;
  direction: string;
  entry_time: string;
  entry_price: number;
  quantity: number;
  stop_loss: number;
  target1: number | null;
  target2: number | null;
  exit_time: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  pnl: number | null;
  charges: number;
  instrument_kind?: InstrumentKind;
  exchange?: string | null;
  lot_size?: number | null;
  expiry?: string | null;
  option_position?: OptionPosition | null;
  premium_stop_pct?: number | null;
  underlying_symbol?: string | null;
  underlying_direction?: string | null;
  underlying_stop_loss?: number | null;
  underlying_target1?: number | null;
  underlying_target2?: number | null;
  expected_price?: number | null;
  slippage?: number | null;
  entry_latency_ms?: number | null;
  charges_source?: "ESTIMATED" | "CONTRACT_NOTE" | string;
  broker_order_id?: string | null;
  exit_order_id?: string | null;
  leg_group_id?: string | null;
  leg_role?: string | null;
  option_strategy?: string | null;
  group_meta?: StructureMetrics & { lots?: number; quantity?: number } | null;
}

export interface ContractNoteSummary {
  id: number;
  broker_name: string;
  filename: string;
  sha256: string;
  note_date: string | null;
  line_count: number;
  matched_lines: number;
  trades_updated: number;
  total_charges: number;
  uploaded_at: string;
}

export interface ContractNoteIngest {
  note_id: number | null;
  filename: string;
  sha256: string;
  lines: number;
  matched: number;
  applied: boolean;
  total_charges: number;
  trades_updated: { trade_id: number; symbol: string; old_charges: number; new_charges: number; old_pnl: number | null; new_pnl: number | null; legs: number }[];
  unmatched: { row: number; symbol: string; side: string; quantity: number; price: number; order_id: string | null; date: string | null; charges: number }[];
  warnings: string[];
}

export interface OHLCVBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export type SignalDirection = "LONG" | "SHORT" | "NO_TRADE";
export type SignalGrade = "A1" | "High Quality" | "Valid" | "Weak" | "No Trade";
export type StrategyCategory = "multi_timeframe" | "indicator_based";

export interface StrategyInfo {
  id: string;
  name: string;
  description: string;
  category: StrategyCategory;
  timeframes: string[];
  default_params: Record<string, unknown>;
}

export interface Signal {
  symbol: string;
  strategy_id: string;
  strategy_name: string;
  direction: SignalDirection;
  timestamp: string;
  entry: number | null;
  stop_loss: number | null;
  target1: number | null;
  target2: number | null;
  risk_reward: number | null;
  score: number;
  grade: SignalGrade;
  reasons: string[];
  timeframe_combo: string;
}

export interface ScoreComponent {
  pct: number;
  weight: number;
  contribution: number;
  note: string;
}

export interface EnrichedSignal {
  signal: Signal;
  composite_score: number;
  grade: SignalGrade;
  breakdown: Record<string, ScoreComponent>;
  confirmations: string[];
}

export interface Trade {
  symbol: string;
  strategy_id: string;
  direction: SignalDirection;
  entry_time: string;
  entry_price: number;
  quantity: number;
  stop_loss: number;
  target1: number;
  target2: number | null;
  exit_time: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  pnl: number | null;
  charges: number;
}

/** Phase AO: a strategy walked over the candles a chart is showing (POST /strategies/{id}/chart-run). */
export interface ChartRunResponse {
  strategy_id: string;
  strategy_name: string;
  strategy_timeframes: string[];
  compatible: boolean;
  reason: string | null;
  bars_used: number;
  trades: Trade[];
  total_trades: number;
  win_rate: number;
  net_pnl: number;
  profit_factor: number | null;
  last_signal: Signal | null;
}

export interface BacktestResult {
  strategy_id: string;
  symbol: string;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  net_pnl: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number | null;
  max_drawdown: number;
  avg_win: number;
  avg_loss: number;
  expectancy: number;
  trades: Trade[];
  equity_curve: number[];
  analytics?: BacktestAnalytics | null;
  exit_rules?: string | null;
  run_id?: number | null;
  // Phase W: present on an option backtest.
  options?: OptionBacktestSummary | null;
}

// Phase W: historical option backtests.
export type OptionPricingModel = "synthetic" | "snapshots" | "uploaded";

export interface OptionBacktestConfig {
  option_strategy: OptionStrategy;
  option_position?: OptionPosition;
  expiry_rule?: ExpiryRule;
  strike_rule?: StrikeRule;
  strike_offset?: number;
  spread_width?: number;
  target_credit_pct?: number | null;
  stop_credit_pct?: number | null;
  premium_stop_pct?: number | null;
  custom_legs?: CustomLeg[] | null;
  max_lots?: number | null;
  lot_size?: number | null;
  strike_step?: number | null;
  expiry_weekday?: number | null;
  weekly_expiry?: boolean | null;
  pricing?: OptionPricingModel;
  implied_volatility?: number | null;
  realised_vol_window?: number;
  risk_free_rate?: number;
  snapshot_max_age_minutes?: number;
  allow_synthetic_fallback?: boolean;
  option_chain?: OptionChainSnapshotRow[] | null;
  intraday?: boolean;
}

export interface OptionChainSnapshotRow {
  timestamp: string; expiry: string; strike: number; right: "CE" | "PE"; ltp: number;
  iv?: number | null; oi?: number | null; underlying_ltp?: number | null;
}

export interface OptionBacktestLeg {
  right: string; role: "SHORT" | "LONG"; strike: number; expiry: string; ratio: number; quantity: number;
  entry_price: number; exit_price: number | null;
}

export interface OptionBacktestStructure {
  label: string; entry_time: string; exit_time: string; expiry: string; lots: number; direction: string;
  entry_unit: number; exit_unit: number; exit_reason: string; pnl: number; charges: number;
  net_credit: number | null; max_loss: number | null; max_profit: number | null; breakevens: number[] | null;
  legs: OptionBacktestLeg[];
}

export interface OptionBacktestSummary {
  engine_version: string; pricing_model: OptionPricingModel; pricing: string; underlying: string; structure: OptionStrategy;
  position: OptionPosition | null; lot_size: number; strike_step: number; expiry_calendar: string; intraday: boolean;
  structures_opened: number; expiry_settlements: number; signals_skipped: Record<string, number>;
  structures: OptionBacktestStructure[]; disclaimer: string;
  snapshot_hits?: number; synthetic_fallbacks?: number; snapshot_contracts?: number;
}

export interface OptionChainCoverage { underlying: string; from: string | null; to: string | null; rows: number; expiries: number }

export interface ExitRules {
  trailing_stop_pct?: number | null;
  break_even_at_r?: number | null;
  time_exit_minutes?: number | null;
  time_exit_at?: string | null;
}

export interface AnalyticsBucket { key: string; trades: number; pnl: number; win_rate: number; avg_pnl: number }

export interface BacktestAnalytics {
  monthly: AnalyticsBucket[];
  day_of_week: AnalyticsBucket[];
  hour_of_day: AnalyticsBucket[];
  exit_reasons: AnalyticsBucket[];
  direction: AnalyticsBucket[];
  holding_minutes: { avg: number | null; max: number | null; min: number | null };
  slippage: { avg_per_unit: number | null; trades_with_data: number };
  costs: { total_charges: number; gross_pnl: number; charges_pct_of_gross: number | null };
  streaks: { max_consecutive_wins: number; max_consecutive_losses: number };
  ratios: { cagr_pct: number | null; sharpe: number | null; sortino: number | null; calmar: number | null; period_days?: number };
  drawdown_curve: number[];
}

export interface BacktestRunSummary {
  id: number; strategy_id: string; symbol: string; base_timeframe: string; params: Record<string, unknown> | null;
  exit_rules: ExitRules | null; data_source: string; bars: number; data_from: string | null; data_to: string | null;
  engine_version: string; created_at: string | null; total_trades: number | null; net_pnl: number | null;
  win_rate: number | null; max_drawdown: number | null; profit_factor: number | null;
}

export interface MonteCarloResult {
  runs: number; trades: number; note?: string;
  final_pnl?: { p5: number; p25: number; p50: number; p75: number; p95: number; mean: number };
  max_drawdown?: { p50: number; p95: number; worst: number; original: number };
  probability_of_loss_pct?: number; probability_dd_exceeds_original_pct?: number; risk_of_ruin_pct?: number;
}

export interface WalkForwardResult {
  folds: number; note?: string; profitable_windows?: number; consistency_pct?: number; mean_window_pnl?: number; window_pnl_range?: number;
  windows: { window: number; from: string; to: string; bars: number; trades: number; net_pnl: number; win_rate: number; profit_factor: number | null; max_drawdown: number; expectancy: number }[];
}

export type Moneyness = "ITM" | "ATM" | "OTM";
export type OIActivity = "CALL_WRITING" | "CALL_UNWINDING" | "PUT_WRITING" | "PUT_UNWINDING" | "FLAT";
export type OptionChainBias = "BULLISH" | "BEARISH" | "NEUTRAL" | "CONFLICTING";

export interface OptionChainRow {
  strike: number;
  call_oi?: number | null;
  call_change_oi?: number | null;
  call_volume?: number | null;
  call_ltp?: number | null;
  call_iv?: number | null;
  put_oi?: number | null;
  put_change_oi?: number | null;
  put_volume?: number | null;
  put_ltp?: number | null;
  put_iv?: number | null;
}

export interface OptionChain {
  underlying: string;
  expiry: string;
  underlying_ltp?: number | null;
  rows: OptionChainRow[];
}

export interface StrikeAnalysis {
  strike: number;
  call_oi: number | null;
  call_change_oi: number | null;
  call_activity: OIActivity;
  call_moneyness: Moneyness;
  put_oi: number | null;
  put_change_oi: number | null;
  put_activity: OIActivity;
  put_moneyness: Moneyness;
}

export interface OptionChainAnalysis {
  underlying: string;
  expiry: string;
  underlying_ltp: number | null;
  atm_strike: number | null;
  max_pain: number | null;
  pcr: number | null;
  total_call_oi: number;
  total_put_oi: number;
  total_call_oi_change: number | null;
  total_put_oi_change: number | null;
  bias: OptionChainBias;
  bias_reasons: string[];
  call_resistance_strikes: number[];
  put_support_strikes: number[];
  strikes: StrikeAnalysis[];
}

export type TrendState = "UPTREND" | "DOWNTREND" | "RANGE";

export interface SwingPoint {
  timestamp: string;
  price: number;
  kind: "HIGH" | "LOW";
  label: "HH" | "HL" | "LH" | "LL" | null;
}

export interface StructureEvent {
  timestamp: string;
  event: "BOS" | "CHoCH";
  direction: "BULLISH" | "BEARISH";
  level: number;
  note: string;
}

export interface MarketStructureResult {
  trend: TrendState;
  swings: SwingPoint[];
  events: StructureEvent[];
}

export interface SRZone {
  kind: "SUPPORT" | "RESISTANCE";
  lower: number;
  upper: number;
  mid: number;
  strength_score: number;
  touches: number;
  volume_confirmation: boolean;
  rejection_count: number;
  timeframe: string;
  source: string;
}

export interface RiskConfig {
  capital: number;
  risk_per_trade_pct: number;
  max_daily_loss_pct: number;
  max_trades_per_day: number;
  max_open_positions: number;
  max_consecutive_losses: number;
  min_risk_reward: number;
  lot_size: number;
  // Phase V1: Risk Guardian rules
  max_portfolio_risk_pct: number;
  stop_cooldown_minutes: number;
  dd_level_1_pct: number;
  dd_level_2_pct: number;
  event_size_cut_pct: number;
}

export type RiskCeilings = Record<string, number>;

export interface MarketEvent {
  id: number;
  tenant_id: number | null;
  global: boolean;
  underlying: string | null;
  event_date: string;
  start_time: string | null;
  end_time: string | null;
  kind: string;
  action: "BLOCK" | "SIZE_CUT";
  size_cut_pct: number | null;
  description: string;
  created_at: string | null;
}

export interface MarketEventRequest {
  event_date: string;
  underlying?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  kind: string;
  action: "BLOCK" | "SIZE_CUT";
  size_cut_pct?: number | null;
  description?: string;
  global_event?: boolean;
}

export interface GuardianModeStatus {
  equity: number;
  peak: number;
  drawdown_pct: number;
  closed_trades: number;
  state: "normal" | "reduced" | "paused";
  size_multiplier: number;
  open_risk_by_bucket: Record<string, number>;
  open_risk_total: number;
  open_risk_pct: number;
  portfolio_cap: number;
  cooldowns: { underlying: string; exit_reason: string | null; minutes_left: number }[];
}

export interface GuardianStatus {
  as_of: string;
  settings: RiskConfig;
  modes: Record<"PAPER" | "LIVE", GuardianModeStatus>;
  events_today: MarketEvent[];
  ceilings: RiskCeilings;
}

export interface GroupStats {
  key: string;
  trades: number;
  wins: number;
  win_rate: number;
  net_pnl: number;
}

export interface AnalyticsSummary {
  total_trades: number;
  closed_trades: number;
  open_trades: number;
  win_rate: number;
  net_pnl: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number | null;
  avg_win: number;
  avg_loss: number;
  by_strategy: GroupStats[];
  by_symbol: GroupStats[];
}

export interface AuditLogEntry {
  id: number;
  event: string;
  detail: string;
  created_at: string;
}

export type NotificationEventType =
  | "ENTRY"
  | "EXIT"
  | "REJECTION"
  | "BROKER_DISCONNECT"
  | "TOKEN_EXPIRED"
  | "RISK_REJECTION"
  | "DAILY_LOSS_LIMIT"
  | "EMERGENCY_EXIT"
  | "SYSTEM_FAILURE"
  | "SECURITY"
  | "AI_PROPOSAL"
  | "MARKETPLACE"
  | "EOD_SUMMARY"
  | "NEWS_ALERT"
  | "THESIS_REPORT";

export type NotificationSeverity = "INFO" | "WARNING" | "CRITICAL" | "EMERGENCY";

export interface WebhookTokenResponse {
  // null once the token exists only as a hash on the server (P0.3): rotate to get a new URL, shown once.
  webhook_token: string | null;
  webhook_url: string | null;
  configured?: boolean;
}

export interface NotificationEntry {
  id: number;
  event_type: NotificationEventType;
  severity: NotificationSeverity;
  title: string;
  message: string;
  related_trade_id: number | null;
  related_order_id: number | null;
  read: boolean;
  created_at: string;
}

export interface SignalHistoryEntry {
  id: number;
  strategy_id: string;
  symbol: string;
  direction: string;
  signal_time: string;
  entry: number | null;
  stop_loss: number | null;
  target1: number | null;
  target2: number | null;
  risk_reward: number | null;
  score: number;
  grade: string;
  reasons: string[];
  timeframe_combo: string;
  created_at: string;
}

export interface MarkPriceResponse {
  closed: boolean;
  exit_reason: string | null;
  exit_price: number | null;
  pnl: number | null;
}

export interface StoredBrokerInfo {
  broker_name: string;
  updated_at: string;
  account_label?: string;
}

// Phase AJ: read-only broker smoke test.
export interface SmokeStep { name: string; status: "ok" | "fail" | "skip"; detail: string; ms: number }
export interface SmokeReport { broker: string; account_label: string; ok: boolean; summary: string; started_at: string; steps: SmokeStep[]; read_only: boolean }

// Phase I2: broker accounts
export interface BrokerAccount {
  id: number;
  broker_name: string;
  account_label: string;
  broker_account_identifier: string | null;
  display_name: string | null;
  status: "ACTIVE" | "DISABLED";
  is_default: boolean;
  available_balance: number | null;
  used_margin: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  last_sync_at: string | null;
  last_sync_error: string | null;
  token_status: string | null;
  created_at: string | null;
}

export interface BrokerCredentialsInput {
  api_key?: string;
  api_secret?: string;
  access_token?: string;
  request_token?: string;
  client_id?: string;
  pin?: string;
  totp_secret?: string;
  redirect_uri?: string;
}

// --- Custom strategy / Strategy Builder ---

export type ConditionOperator = "GT" | "LT" | "GTE" | "LTE" | "CROSSES_ABOVE" | "CROSSES_BELOW";
export type IndicatorName = "EMA" | "SMA" | "RSI" | "ADX" | "PLUS_DI" | "MINUS_DI" | "ATR" | "SUPERTREND" | "CLOSE" | "OPEN" | "HIGH" | "LOW"
  // Phase AW: session levels, bands and volume.
  | "VWAP" | "DAY_OPEN" | "OR_HIGH" | "OR_LOW" | "PDH" | "PDL" | "PDC" | "BB_UPPER" | "BB_MID" | "BB_LOWER" | "VOLUME" | "VOLUME_SMA";

export interface Operand {
  type: "value" | "indicator";
  value: number;
  indicator: IndicatorName;
  period: number;
  multiplier: number;
  /** Phase AW: evaluate on a higher timeframe (completed bars only); null/undefined = the strategy's own. */
  timeframe?: string | null;
}

export interface Condition {
  left: Operand;
  operator: ConditionOperator;
  right: Operand;
}

export interface CustomStrategyConfig {
  name: string;
  timeframe: string;
  long_conditions: Condition[];
  short_conditions: Condition[];
  stop_loss_atr_mult: number;
  atr_period: number;
  target_rr: [number, number];
  min_rr: number;
}

export interface CustomStrategyResponse {
  id: number;
  strategy_id: string;
  config: CustomStrategyConfig;
  created_at: string;
  updated_at: string;
}

export function defaultOperand(): Operand {
  return { type: "indicator", value: 50, indicator: "RSI", period: 14, multiplier: 3.0 };
}

export function defaultCondition(): Condition {
  return { left: defaultOperand(), operator: "GT", right: { ...defaultOperand(), type: "value" } };
}

export function defaultCustomStrategyConfig(): CustomStrategyConfig {
  return {
    name: "My Strategy",
    timeframe: "5min",
    long_conditions: [defaultCondition()],
    short_conditions: [],
    stop_loss_atr_mult: 1.0,
    atr_period: 14,
    target_rr: [1.5, 2.0],
    min_rr: 1.2,
  };
}

// --- Conversational (rule-based) Strategy Builder ---

export interface ParseStrategyResult {
  config: CustomStrategyConfig;
  interpreted: string[];
  warnings: string[];
}

// --- Market Scanner ---

export type StructureFilterType =
  | "TREND_UPTREND"
  | "TREND_DOWNTREND"
  | "TREND_RANGE"
  | "BOS_BULLISH"
  | "BOS_BEARISH"
  | "CHOCH_BULLISH"
  | "CHOCH_BEARISH"
  | "PATTERN_BULLISH"
  | "PATTERN_BEARISH"
  | "NEAR_SUPPORT"
  | "NEAR_RESISTANCE";

export interface StructureFilter {
  filter_type: StructureFilterType;
  tolerance_pct: number;
}

export type OptionFilterType = "PCR" | "BIAS_BULLISH" | "BIAS_BEARISH" | "NEAR_MAX_PAIN";

export interface OptionFilter {
  filter_type: OptionFilterType;
  operator: ConditionOperator | null;
  value: number | null;
  tolerance_pct: number;
}

export interface ScannerSymbolInput {
  symbol: string;
  timeframe: string;
  candles: OHLCVBar[];
  option_chain: OptionChain | null;
}

export interface ScannerRequest {
  symbols: ScannerSymbolInput[];
  indicator_conditions: Condition[];
  structure_filters: StructureFilter[];
  option_filters: OptionFilter[];
  swing_window: number;
}

export interface ScannerMatch {
  symbol: string;
  close: number;
  matched_indicator_labels: string[];
  matched_structure_labels: string[];
  matched_option_labels: string[];
}

export interface ScannerResult {
  scanned_count: number;
  matched_count: number;
  matches: ScannerMatch[];
}

// Phase Y: the AI scanner.
export interface ScanPlan {
  timeframe: string; symbols: string[]; indicator_conditions: Condition[]; structure_filters: StructureFilter[]; option_filters: OptionFilter[];
  explanation: string; warnings: string[]; provider: string; model: string; prompt_version: string;
}

export interface RankedSymbol { symbol: string; score: number | null; thesis: string; risks: string; next_step: string; regime: string | null }

export interface ScanRead {
  summary: string; ranked: RankedSymbol[]; warnings: string[]; provider: string; model: string; prompt_version: string; disclaimer: string;
}

export const STRUCTURE_FILTER_LABELS: Record<StructureFilterType, string> = {
  TREND_UPTREND: "Trend: Uptrend",
  TREND_DOWNTREND: "Trend: Downtrend",
  TREND_RANGE: "Trend: Range",
  BOS_BULLISH: "Break of Structure (Bullish)",
  BOS_BEARISH: "Break of Structure (Bearish)",
  CHOCH_BULLISH: "Change of Character (Bullish)",
  CHOCH_BEARISH: "Change of Character (Bearish)",
  PATTERN_BULLISH: "Candlestick pattern (Bullish)",
  PATTERN_BEARISH: "Candlestick pattern (Bearish)",
  NEAR_SUPPORT: "Near support zone",
  NEAR_RESISTANCE: "Near resistance zone",
};

export const OPTION_FILTER_LABELS: Record<OptionFilterType, string> = {
  PCR: "PCR threshold",
  BIAS_BULLISH: "Option chain bias: Bullish",
  BIAS_BEARISH: "Option chain bias: Bearish",
  NEAR_MAX_PAIN: "Near Max Pain",
};

export function defaultStructureFilter(): StructureFilter {
  return { filter_type: "TREND_UPTREND", tolerance_pct: 0.5 };
}

export function defaultOptionFilter(): OptionFilter {
  return { filter_type: "PCR", operator: "GT", value: 1.2, tolerance_pct: 1.0 };
}

// --- News & Event engine ---

export type NewsEventCategory =
  | "RBI_POLICY"
  | "UNION_BUDGET"
  | "GOVT_POLICY"
  | "CORPORATE"
  | "GLOBAL_MACRO"
  | "SECTOR"
  | "OTHER";

export type NewsSentiment = "Bullish" | "Neutral" | "Bearish";

export interface SourceCitation {
  source: string;
  source_url: string | null;
  publication_date: string | null;
  // Omit on write - the backend defaults it to today's date; it is always present on read.
  retrieved_date?: string;
  confidence: number;
}

export interface NewsEvent {
  category: NewsEventCategory;
  headline: string;
  description: string | null;
  event_date: string;
  affected_symbols: string[];
  sentiment: NewsSentiment;
  source: SourceCitation;
}

export interface NewsClassification {
  type: string;
  scope: string[];
  symbols?: string[];
  direction: "BULLISH" | "BEARISH" | "NEUTRAL";
  severity: number;
  horizon: string;
  confidence: number;
  one_line_mr: string;
  one_line_en: string;
  method: "keyword" | "ai";
}

export interface NewsEventResponse extends NewsEvent {
  id: number;
  created_by: number | null;
  created_at: string;
  /** Phase BB: MANUAL = a person's cited entry; FEED = public feed item, never verified by the platform. */
  origin: "MANUAL" | "FEED";
  verified: boolean;
  source_url: string | null;
  feed_id: string | null;
  published_at: string | null;
  classification: NewsClassification | null;
}

export interface NewsFeedSource {
  id: string;
  name: string;
  publisher: string;
  url: string;
  category: NewsEventCategory;
  official: boolean;
  default_on: boolean;
  on: boolean;
  terms: string;
}

export interface NewsFeedStatus {
  enabled: boolean;
  flag: string;
  sources: NewsFeedSource[];
  last_run: { at?: string; fetched?: number; new?: number; duplicates?: number; errors?: string[]; alerts?: number; proposals?: number };
  cadence_seconds: number;
  note: string;
}

/** Phase BD-2: a member's verdict on a feed item and the organisation's news trust. */
export type NewsVerdict = "useful" | "noise" | "wrong_direction";
export interface NewsTrust { ratings: number; trust: number; applied: boolean; useful: number; noise: number; wrong_direction: number; note: string | null }
export interface NewsFeedbackSummary {
  window_days: number; trust: NewsTrust;
  by_source: { key: string; useful: number; noise: number; wrong_direction: number; total: number; useful_share: number | null }[];
  by_category: { key: string; useful: number; noise: number; wrong_direction: number; total: number; useful_share: number | null }[];
}
export interface ThesisWeeklyReport {
  scored: number; hits?: number; misses?: number; flat?: number; hit_rate?: number; avg_shadow?: number; pending?: number; unknown?: number;
  title: string | null; lines: string[];
}

export interface NewsFeedItem {
  id: number;
  headline: string;
  source: string;
  source_url: string | null;
  published_at: string;
  category: NewsEventCategory;
  symbols: string[];
  verified: boolean;
  origin: "FEED";
  classification: NewsClassification;
  keyword: NewsClassification;
  ai: NewsClassification | null;
}

export const NEWS_EVENT_CATEGORY_LABELS: Record<NewsEventCategory, string> = {
  RBI_POLICY: "RBI Policy",
  UNION_BUDGET: "Union Budget",
  GOVT_POLICY: "Government Policy",
  CORPORATE: "Corporate",
  GLOBAL_MACRO: "Global Macro",
  SECTOR: "Sector",
  OTHER: "Other",
};

export function defaultNewsEvent(): NewsEvent {
  return {
    category: "RBI_POLICY",
    headline: "",
    description: null,
    event_date: new Date().toISOString().slice(0, 10),
    affected_symbols: [],
    sentiment: "Neutral",
    source: { source: "", source_url: null, publication_date: null, confidence: 80 },
  };
}

// --- Multi-asset-class instrument registry (MCX / Crypto) ---

export type AssetClass = "EQUITY" | "INDEX_OPTION" | "COMMODITY" | "CRYPTO";

export interface ContractSpec {
  symbol: string;
  exchange: string;
  asset_class: AssetClass;
  description: string;
  lot_size: number;
  tick_size: number;
  fractional: boolean;
}

export const ASSET_CLASS_LABELS: Record<AssetClass, string> = {
  EQUITY: "Equity",
  INDEX_OPTION: "Index Option",
  COMMODITY: "Commodity (MCX)",
  CRYPTO: "Crypto",
};

// --- Autonomous trading core: deployments, broker token health, worker heartbeat ---

export type DeploymentStatus = "ACTIVE" | "PAUSED" | "STOPPED";
export type ExecutionMode = "PAPER" | "LIVE";

export interface Deployment {
  id: number;
  regime_filter?: string[] | null;
  holding?: string;
  order_style?: string;
  market_protection_pct?: number | null;
  strategy_id: string;
  symbol: string;
  exchange: string;
  timeframe: string;
  mode: ExecutionMode;
  broker_name: string | null;
  status: DeploymentStatus;
  pause_reason: string | null;
  last_evaluated_at: string | null;
  last_signal_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  open_positions: number;
  created_by: number | null;
  created_at: string;
  updated_at: string;
  instrument_kind: InstrumentKind;
  option_position: OptionPosition | null;
  expiry_rule: ExpiryRule | null;
  strike_rule: StrikeRule | null;
  strike_offset: number;
  premium_stop_pct: number | null;
  max_lots: number | null;
  contract_rules: string;
  strike_filters?: StrikeFilters | null;
  option_strategy?: OptionStrategy;
  spread_width?: number;
  target_credit_pct?: number | null;
  stop_credit_pct?: number | null;
  custom_legs?: CustomLeg[] | null;
  exit_rules?: ExitRules | null;
  broker_account_id?: number | null;
  routing_policy?: RoutingPolicy | null;
  route_across_brokers?: boolean;
  last_route?: string | null;
}

export type InstrumentKind = "UNDERLYING" | "OPTION" | "FUTURE";
export type OptionPosition = "BUY" | "WRITE";
export type ExpiryRule = "NEAREST" | "NEXT" | "MONTHLY";
export type StrikeRule = "ATM" | "ITM" | "OTM";

export type OptionStrategy =
  | "SINGLE" | "BULL_PUT_SPREAD" | "BEAR_CALL_SPREAD" | "IRON_CONDOR"
  | "IRON_BUTTERFLY" | "SHORT_STRADDLE" | "SHORT_STRANGLE" | "LONG_STRADDLE" | "LONG_STRANGLE" | "CALENDAR_SPREAD"
  | "CALL_RATIO_SPREAD" | "PUT_RATIO_SPREAD" | "LONG_BUTTERFLY" | "CUSTOM";
export const DEBIT_STRUCTURES: OptionStrategy[] = ["LONG_STRADDLE", "LONG_STRANGLE", "CALENDAR_SPREAD", "LONG_BUTTERFLY"];
export const WINGED_STRUCTURES: OptionStrategy[] = ["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "IRON_CONDOR", "IRON_BUTTERFLY"];
// Phase U: economics from the expiry payoff, legs with ratios, exits on P&L per unit.
export const PAYOFF_STRUCTURES: OptionStrategy[] = ["CALL_RATIO_SPREAD", "PUT_RATIO_SPREAD", "LONG_BUTTERFLY", "CUSTOM"];
export const WIDTH_STRUCTURES: OptionStrategy[] = [...WINGED_STRUCTURES, "CALL_RATIO_SPREAD", "PUT_RATIO_SPREAD", "LONG_BUTTERFLY"];

export interface CustomLeg {
  right: "CE" | "PE";
  role: "SHORT" | "LONG";
  strike_rule: StrikeRule;
  strike_offset: number;
  ratio: number;
}
export const MAX_CUSTOM_LEGS = 6;

export interface StrikeFilters {
  min_oi?: number | null;
  min_volume?: number | null;
  max_spread_pct?: number | null;
  min_iv_pct?: number | null;
  max_iv_pct?: number | null;
  target_delta?: number | null;
  delta_tolerance?: number;
  min_premium?: number | null;
  max_premium?: number | null;
  search_steps?: number;
}

export interface ContractRules {
  instrument_kind: InstrumentKind;
  option_position?: OptionPosition | null;
  expiry_rule?: ExpiryRule | null;
  strike_rule?: StrikeRule | null;
  strike_offset?: number;
  premium_stop_pct?: number | null;
  max_lots?: number | null;
  strike_filters?: StrikeFilters | null;
  option_strategy?: OptionStrategy;
  spread_width?: number;
  target_credit_pct?: number | null;
  stop_credit_pct?: number | null;
  custom_legs?: CustomLeg[] | null;
}

export interface StrikeCandidate {
  strike: number;
  ltp: number | null;
  oi: number | null;
  volume: number | null;
  spread_pct: number | null;
  iv_pct: number | null;
  delta: number | null;
  passes: boolean;
  reasons: string[];
}

export interface StructureMetrics {
  net_credit: number;
  max_profit: number | null;
  max_loss: number | null;
  breakevens: number[];
  target_value: number;
  stop_value: number;
  short_strikes: Record<string, number>;
  legs: { role: string; side: string; tradingsymbol: string; strike: number | null; right: string | null; premium: number; ratio?: number }[];
  debit?: boolean;
  defined_risk?: boolean;
  risk_per_unit?: number;
  underlying_exits?: Record<string, number>;
  pnl_target?: number;
  pnl_stop?: number;
}

export interface StructurePreview {
  strategy: OptionStrategy;
  underlying_symbol: string;
  lot_size: number;
  expiry: string;
  width_points: number;
  notes: string[];
  legs: (ResolvedContract & { role: string; side: string; ratio?: number })[];
  metrics?: StructureMetrics;
  metrics_error?: string;
}

export interface PositionGreeks {
  as_of: string;
  spot: Record<string, number>;
  legs: {
    trade_id: number; symbol: string; leg_group_id: string | null; leg_role: string | null; option_strategy: string | null;
    quantity: number; premium: number; implied_volatility: number; delta: number; gamma: number; theta: number; vega: number;
    position_delta: number; position_gamma: number; position_theta: number; position_vega: number;
  }[];
  groups: { leg_group_id: string | null; legs: number; net_delta: number; net_gamma: number; net_theta: number; net_vega: number }[];
  net: { net_delta: number; net_gamma: number; net_theta: number; net_vega: number };
  skipped: { trade_id: number; reason: string }[];
}

export interface DeploymentCreateRequest extends ContractRules {
  regime_filter?: string[] | null;
  /** Phase AS: SWING = daily candles, held overnight. */
  holding?: "INTRADAY" | "SWING";
  /** P0.5 / T5: LIVE entry style. PROTECTED_LIMIT sends a marketable limit `market_protection_pct` past the signal price. */
  order_style?: "MARKET" | "PROTECTED_LIMIT";
  market_protection_pct?: number | null;
  strategy_id: string;
  symbol: string;
  exchange: string;
  timeframe: string;
  mode: ExecutionMode;
  broker_name?: string | null;
  broker_account_id?: number | null;
  routing_policy?: RoutingPolicy | null;
  route_across_brokers?: boolean;
  exit_rules?: ExitRules | null;
}

export type RoutingPolicy = "EXPLICIT" | "MOST_MARGIN" | "LEAST_UTILISED" | "FEWEST_POSITIONS";
export const ROUTING_POLICIES: { value: RoutingPolicy; label: string; help: string }[] = [
  { value: "EXPLICIT", label: "Explicit", help: "The chosen account, or the broker's default." },
  { value: "MOST_MARGIN", label: "Most margin", help: "The active account with the largest synced available margin." },
  { value: "LEAST_UTILISED", label: "Least utilised", help: "The active account with the lowest margin utilisation." },
  { value: "FEWEST_POSITIONS", label: "Fewest positions", help: "The active account carrying the fewest open LIVE positions." },
];

export interface ResolvedContract {
  kind: InstrumentKind;
  underlying: string;
  underlying_symbol: string;
  tradingsymbol: string;
  exchange: string;
  instrument_key: string;
  lot_size: number;
  tick_size: number;
  expiry: string;
  strike: number | null;
  right: "CE" | "PE" | null;
  entry_side: "BUY" | "SELL";
  trade_direction: "LONG" | "SHORT";
  position: OptionPosition | null;
  selection_notes?: string[];
  selection?: { strike: number; rule_strike: number; notes: string[]; candidates: StrikeCandidate[] } | null;
}

export interface ContractPreview {
  symbol: string;
  kind: InstrumentKind;
  rules?: string;
  note?: string;
  spot?: number | null;
  spot_source?: "supplied" | "broker" | null;
  contracts?: Record<"LONG" | "SHORT", ResolvedContract | { error: string }>;
  structures?: Record<"LONG" | "SHORT", StructurePreview | { error: string }>;
}

export type BrokerTokenStatus = "UNKNOWN" | "VALID" | "EXPIRED" | "MISSING";

export interface BrokerTokenInfo {
  broker_name: string;
  token_status: BrokerTokenStatus;
  token_expires_at: string | null;
  last_verified_at: string | null;
  needs_login: boolean;
  oauth_supported: boolean;
  oauth_callback_url: string | null;
  // Fyers / Kite: the platform opens the broker's login page and takes the pasted one-time code.
  login_url_supported?: boolean;
  code_param?: string | null;
  account_label?: string;
}

export interface BrokerLoginUrl {
  broker_name: string;
  authorization_url: string;
  code_param: string;
  instructions: string;
}

export interface ReconciliationStatus {
  broker_uncertain: boolean;
  broker_uncertain_since: string | null;
  broker_uncertain_reason: string | null;
  last_reconciled_at: string | null;
  open_live_trades: number;
}

export interface ReconciliationItem {
  symbol: string;
  internal_net_quantity: number | null;
  broker_net_quantity: number | null;
  status: "MATCHED" | "QUANTITY_MISMATCH" | "MISSING_AT_BROKER" | "UNTRACKED_AT_BROKER";
  internal_trade_ids: number[];
  detail: string;
  account_label?: string | null;
}

export interface ReconciliationReport {
  broker_name: string;
  checked_at: string;
  items: ReconciliationItem[];
  mismatched_count: number;
  account_label?: string | null;
  accounts?: string[];
}

export interface WorkerStatus {
  worker_name: string;
  running: boolean;
  healthy: boolean;
  last_seen_at: string | null;
  seconds_since_heartbeat: number | null;
  cycle_count: number;
  last_cycle_ms: number | null;
  last_error: string | null;
  cycle_seconds: number;
  market_open: boolean;
  market_status: string;
  next_market_open: string | null;
}

export const BASE_TIMEFRAMES = ["1min", "3min", "5min", "15min", "30min", "60min"] as const;

// --- Out-of-app alert delivery (Telegram / email) ---

export type AlertChannelType = "TELEGRAM" | "EMAIL" | "WEBHOOK" | "PUSH" | "SMS";

export interface AlertChannel {
  channel_type: AlertChannelType;
  enabled: boolean;
  min_severity: NotificationSeverity;
  config: Record<string, unknown>;
  last_delivered_at: string | null;
  last_error: string | null;
  updated_at: string;
}

export interface AlertChannelUpsert {
  enabled: boolean;
  min_severity: NotificationSeverity;
  config: Record<string, unknown>;
}

/** Phase BE: Telegram inbound settings (commands + PAPER approval buttons). */
export interface TelegramApprover { telegram_user_id: string; user_id?: number; email: string }
export interface TelegramInboundStatus {
  configured: boolean;
  inbound_enabled: boolean;
  allowed_chat_ids: string[];
  /** P0.8 / A6: who may press Approve/Reject (Telegram user id -> team member). Empty = private chat with the owner only. */
  approvers?: TelegramApprover[];
  has_secret: boolean;
  webhook_url: string;
  telegram_actions: string[];
  note: string;
  flag_enabled?: boolean;
}

export interface AlertDelivery {
  id: number;
  channel_type: AlertChannelType;
  notification_id: number;
  title: string;
  severity: NotificationSeverity;
  status: "PENDING" | "SENT" | "FAILED";
  attempts: number;
  last_error: string | null;
  created_at: string;
  sent_at: string | null;
}

// --- Team (multi-user tenants) ---

export type TenantRole = "OWNER" | "USER" | "STRATEGY_CREATOR" | "VIEWER" | "SUPPORT" | "SUPER_ADMIN";

export interface TeamMember {
  id: number;
  email: string;
  role: TenantRole;
  is_active: boolean;
  created_at: string;
  trading_disabled_reason?: string | null;
}

export interface TeamInvite {
  id: number;
  email: string;
  role: TenantRole;
  expires_at: string;
  accepted_at: string | null;
  invited_by: number | null;
  invite_url?: string | null;
}

export interface TenantInfo {
  id: number;
  name: string;
  plan: string;
  plan_name: string;
  plan_description: string;
  status: string;
  members: number;
  limits: { active_deployments: number; live_trading: boolean; custom_strategies: number; members: number; alert_channels: number };
  usage: { active_deployments: number; custom_strategies: number; members: number; alert_channels: number };
  require_mfa_for_live?: boolean;
  algo_id?: string | null;
  default_routing_policy?: RoutingPolicy;
  base_currency?: string;
}

export interface InviteInfo {
  email: string;
  role: TenantRole;
  tenant_name: string;
  expires_at: string;
  valid: boolean;
  reason: string | null;
}

export const ROLE_LABELS: Record<TenantRole, string> = {
  OWNER: "Owner",
  USER: "Trader",
  STRATEGY_CREATOR: "Strategy creator",
  VIEWER: "Viewer (read-only)",
  SUPPORT: "Support (read-only)",
  SUPER_ADMIN: "Platform admin",
};

export const TRADING_ROLES: TenantRole[] = ["OWNER", "USER", "STRATEGY_CREATOR", "SUPER_ADMIN"];

// --- Platform admin console (SUPER_ADMIN) ---

export interface AdminPlan {
  id: string;
  name: string;
  description: string;
  limits: Record<string, number | boolean>;
}

export interface AdminTenantSummary {
  id: number;
  name: string;
  plan: string;
  status: string;
  created_at: string;
  owners: string[];
  members: number;
  active_deployments: number;
  live_deployments: number;
  open_positions: number;
}

export interface AdminTenantDetail extends AdminTenantSummary {
  limits: Record<string, number | boolean>;
  usage: Record<string, number>;
  users: { id: number; email: string; role: string; is_active: boolean; created_at: string }[];
  deployments: { id: number; strategy_id: string; symbol: string; mode: string; status: string; broker_name: string | null; last_evaluated_at: string | null; last_error: string | null }[];
  brokers: { broker_name: string; token_status: string; token_expires_at: string | null }[];
  tenant_kill_switch_engaged: boolean;
}

export interface AdminOverview {
  tenants_total: number;
  tenants_by_status: Record<string, number>;
  tenants_by_plan: Record<string, number>;
  users_total: number;
  active_deployments: number;
  live_deployments: number;
  open_positions: number;
  open_live_positions: number;
  global_kill_switch_engaged: boolean;
  global_kill_switch_reason: string;
  worker_running: boolean;
  worker_last_seen_at: string | null;
  worker_last_error: string | null;
}

export interface PlatformAuditLog {
  id: number;
  tenant_id: number | null;
  user_id: number | null;
  user_email: string | null;
  event: string;
  detail: string;
  created_at: string;
}

// Phase I1: risk hierarchy
export type RiskScope = "GLOBAL" | "TENANT" | "USER" | "ACCOUNT" | "PORTFOLIO" | "STRATEGY" | "DEPLOYMENT" | "INSTRUMENT";
export type RiskLimitType =
  | "MAX_DAILY_LOSS" | "MAX_STRATEGY_LOSS" | "MAX_LOSS_PER_TRADE" | "MAX_ORDER_VALUE"
  | "MAX_POSITION_QUANTITY" | "MAX_OPEN_POSITIONS" | "MAX_TRADES_PER_DAY" | "MAX_CAPITAL_ALLOCATION_PCT"
  | "MAX_GROSS_EXPOSURE" | "MAX_SYMBOL_CONCENTRATION_PCT";

export interface RiskLimit {
  id: number;
  tenant_id: number | null;
  scope: RiskScope;
  scope_id: string;
  limit_type: RiskLimitType;
  limit_value: number;
  enabled: boolean;
  note: string | null;
  created_by: number | null;
  updated_at: string | null;
}

export interface RiskLimitRequest {
  scope: RiskScope;
  scope_id?: string;
  limit_type: RiskLimitType;
  limit_value: number;
  enabled?: boolean;
  note?: string | null;
}

export interface RiskEvent {
  id: number;
  created_at: string | null;
  strategy_id: string | null;
  symbol: string | null;
  account_id: number | null;
  rule_type: RiskLimitType;
  scope: RiskScope;
  current_value: number;
  limit_value: number;
  severity: string;
  action: string;
  status: "PASS" | "WARN" | "BLOCK";
  reason: string;
  order_id: number | null;
}

// ---- Phase K: billing, marketplace, public API ------------------------------------------------

export interface PlanCatalogueEntry {
  id: string;
  name: string;
  description: string;
  price_monthly: number;
  price_yearly: number;
  currency: string;
  trial_days: number;
  [limit: string]: unknown;
}

export interface Subscription {
  plan_id: string;
  plan_name: string;
  price_monthly: number;
  price_yearly: number;
  currency: string;
  trial_days: number;
  provider: string;
  status: "NONE" | "TRIALING" | "ACTIVE" | "PAST_DUE" | "CANCELLED";
  billing_cycle: "MONTHLY" | "YEARLY" | null;
  current_period_start?: string | null;
  current_period_end: string | null;
  trial_end?: string | null;
  grace_until: string | null;
  cancel_at_period_end: boolean;
  cancelled_at?: string | null;
  checkout_url?: string | null;
  subscription_provider?: string | null;
}

export interface BillingTransaction {
  id: number;
  kind: string;
  amount: number;
  currency: string;
  status: string;
  description: string;
  provider_ref: string | null;
  created_at: string;
}

export interface BillingOverview {
  subscription: Subscription;
  tenant_status: string;
  status_reason: string | null;
  limits: Record<string, unknown>;
  metered_30d: Record<string, number>;
  usage: Record<string, unknown>;
}

export interface ApiKey {
  id: number;
  name: string;
  key_prefix: string;
  scopes: string[];
  rate_limit_per_minute: number;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  created_at: string;
  key?: string;
  note?: string;
}

export interface MarketplacePerformance {
  backtest_run_id: number;
  symbol: string;
  base_timeframe: string;
  bars: number;
  data_from: string | null;
  data_to: string | null;
  data_source: string;
  engine_version: string;
  total_trades: number | null;
  win_rate: number | null;
  net_pnl: number | null;
  profit_factor: number | null;
  max_drawdown: number | null;
  expectancy: number | null;
}

export interface MarketplaceListing {
  id: number;
  title: string;
  description: string;
  methodology: string | null;
  status: "DRAFT" | "PENDING_REVIEW" | "PUBLISHED" | "REJECTED" | "UNLISTED";
  review_note: string | null;
  version_number: number;
  subscriber_count: number;
  published_at: string | null;
  created_at: string | null;
  performance: MarketplacePerformance | null;
  disclaimer: string;
  custom_strategy_id: number | null;
  config?: Record<string, unknown>;
  // Phase X: one-time price (0 = free); the fee split only on the creator's own listings.
  price: number;
  currency: string;
  platform_fee_pct?: number | null;
  creator_net_per_sale?: number | null;
}

export interface MarketplaceCharge {
  id: number; listing_id: number; listing_title: string | null; amount: number; currency: string;
  status: "OPEN" | "PAID" | "VOID"; provider: string; checkout_url: string | null; payment_ref: string | null;
  paid_at: string | null; created_at: string | null; platform_fee_pct: number; platform_fee: number; creator_net: number;
  payout_id: number | null; buyer_tenant_id?: number;
}

export interface MarketplacePayout {
  id: number; tenant_id: number; amount: number; currency: string; status: "REQUESTED" | "PAID" | "REJECTED";
  destination_hint: string; reference: string | null; note: string | null; created_at: string | null; settled_at: string | null;
}

export interface MarketplaceEarnings {
  currency: string; sales: number; gross: number; platform_fees: number; net: number; available: number; pending_payout: number;
  paid_out: number; min_payout: number; platform_fee_pct: number; can_request_payout: boolean;
  sales_rows: MarketplaceCharge[]; payouts: MarketplacePayout[];
}

export interface MarketplaceTerms { platform_fee_pct: number; min_payout: number; max_listing_price: number }

export interface MarketplacePurchaseResponse {
  id: number; listing_id: number; status: string; custom_strategy_id: number | null; strategy_id: string | null;
  charge?: MarketplaceCharge; checkout_url?: string | null; disclaimer: string; next: string;
}

export interface MarketplaceRevenue {
  currency: string; sales: number; gross: number; platform_fees: number; creator_net: number; open_charges: number;
  payouts_requested: number; payouts_requested_amount: number; paid_out: number; terms: MarketplaceTerms;
}

export interface MarketplaceSubscription {
  id: number;
  listing_id: number;
  status: string;
  custom_strategy_id: number | null;
  strategy_id: string | null;
  title: string | null;
  created_at: string | null;
}

// ---- Phase L: AI layer -------------------------------------------------------------------------

export type AiProviderName = "anthropic" | "openai" | "rule_based";

export interface AiProviderConfig {
  provider: AiProviderName;
  model: string;
  api_key_set: boolean;
  enabled: boolean;
  configured: boolean;
  last_used_at?: string | null;
  last_error?: string | null;
  ai_features_allowed: boolean;
  providers: AiProviderName[];
  default_models: Record<string, string>;
  default_provider?: AiProviderName;
  /** P0.8-C: the model each tier runs on (strong = rule writing, fast = narration/classification/Q&A). */
  models?: { strong: string; fast: string };
  tier_models?: Record<string, { strong: string; fast: string; cheap?: string }>;
  usage?: AiUsage | null;
  /** P0.8-D: the organisation's data-sharing consent (version, accepted, by whom). */
  data_consent?: AiAcknowledgement;
  /** P0.9: per external provider, every AI task with the model it runs on and an estimated cost of one typical call. */
  task_models?: Record<string, AiTaskModel[]>;
  typical_call_tokens?: Record<string, { input: number; output: number }>;
}
export interface AiTaskModel {
  task: string; label: string; tier: "cheap" | "fast" | "strong"; model: string;
  price_per_mtok_usd: { input: number; output: number }; est_inr_per_call: number; estimated_price: boolean;
}

/** P0.8-D: a versioned acceptance - the Copilot first-use terms (per user) or the data-sharing consent (per organisation). */
export interface AiAcknowledgement {
  kind: "copilot_terms" | "data_consent";
  version: string;
  text: { en: string; mr: string };
  accepted: boolean;
  accepted_at?: string | null;
  accepted_by?: number | null;
}

/** P0.8-C: this month's AI spend of the organisation against the plan's budget. */
export interface AiUsage {
  month: string;
  calls: number;
  tokens_input: number;
  tokens_output: number;
  spent_usd: number;
  spent_inr: number;
  budget_inr: number;
  exhausted: boolean;
  note: string;
  by_feature: Record<string, number>;
  by_model: Record<string, number>;
  usd_inr_rate: number;
}

export type AiDraftStatus = "DRAFT" | "FAILED" | "BACKTESTED" | "APPROVED" | "REJECTED";

export interface AiStrategyDraft {
  id: number;
  prompt: string;
  provider: string;
  model: string;
  status: AiDraftStatus;
  config: CustomStrategyConfig | null;
  explanation: string | null;
  warnings: string[];
  backtest_run_id: number | null;
  custom_strategy_id: number | null;
  strategy_id: string | null;
  approved_by: number | null;
  approved_at: string | null;
  created_at: string | null;
  lineage: { provider: string; model: string; prompt_chars: number; generated_at: string | null };
  disclaimer: string;
  raw_response?: string | null;
  compliance?: ComplianceReport | null;
  // Phase V3
  deployment?: DeploymentSuggestion | null;
  deployment_text?: string | null;
  prompt_version?: string | null;
  runtime_context?: Record<string, unknown> | null;
}

// Phase V3: the Autopilot settings the model proposed for the rule set.
export interface DeploymentSuggestion {
  symbol: string | null;
  instrument_kind: InstrumentKind;
  option_strategy: OptionStrategy;
  option_position: OptionPosition | null;
  expiry_rule: ExpiryRule | null;
  strike_rule: StrikeRule | null;
  strike_offset: number;
  spread_width: number;
  target_credit_pct: number | null;
  stop_credit_pct: number | null;
  exit_rules: { trailing_stop_pct: number | null; break_even_at_r: number | null; time_exit_minutes: number | null; time_exit_at: string | null };
  regime_filter: string[];
  next_step: "backtest" | "paper_trade" | "small_live";
}

// Phase V2: the compliance validator's report on an AI draft.
export interface ComplianceCheck {
  rule: string;
  status: "PASS" | "FAIL" | "WARN" | "N/A";
  detail: string;
  fixed: boolean;
}

export interface ComplianceReport {
  ok: boolean;
  checks: ComplianceCheck[];
  passed: string[];
  failed: string[];
  warnings: string[];
  fixes: string[];
  user_must_accept: { max_loss_per_trade_text?: string; worst_case_text?: string };
  evidence?: { total_trades: number; win_rate: number; net_pnl: number; profit_factor: number | null; max_drawdown: number; summary: string; warnings: string[]; strength: "weak" | "adequate" } | null;
}

export type RegimeKind = "TRENDING_UP" | "TRENDING_DOWN" | "RANGING" | "VOLATILE" | "QUIET" | "UNKNOWN";

export interface Regime {
  kind: RegimeKind;
  confidence: number;
  adx: number | null;
  ema_fast: number | null;
  ema_slow: number | null;
  ema_slope_pct: number | null;
  atr_pct: number | null;
  atr_ratio: number | null;
  bars: number;
  reasons: string[];
}

export type AiActionStatus = "PROPOSED" | "APPROVED" | "EXECUTED" | "REJECTED" | "EXPIRED" | "FAILED";

export interface AiAction {
  id: number;
  deployment_id: number | null;
  trade_id: number | null;
  action: "PAUSE_DEPLOYMENT" | "EXIT_POSITION" | "REDUCE_RISK" | "REVIEW_STRATEGY";
  rule: string;
  reason: string;
  evidence: Record<string, unknown>;
  status: AiActionStatus;
  decided_by: number | null;
  decided_at: string | null;
  decision_note: string | null;
  executed_at: string | null;
  result: string | null;
  expires_at: string | null;
  created_at: string | null;
}

// ---- Phase M: platform controls, portfolio, degradation, incidents, optimisation ----------------

export interface SystemStatus {
  maintenance_mode: boolean;
  maintenance_message: string | null;
  disabled_brokers: string[];
}

// ---- Phase N: scopes, email verification, feature flags, encryption status ------------------------

export interface ScopeCatalogueEntry {
  scope: string;
  description: string;
  roles: string[];
}

export interface MemberScopes {
  id: number;
  role: string;
  scopes: string[];
  overrides: { deny: string[]; grant: string[] };
}

export interface VerificationSendResult {
  sent: boolean;
  mailer_configured?: boolean;
  expires_in_hours?: number;
  already_verified: boolean;
}

export interface FeatureFlag {
  on: boolean;
  tenants: number[];
  description: string;
}

export type FeatureFlags = Record<string, FeatureFlag>;
export type TenantFeatures = Record<string, boolean>;

export interface EncryptionStatus {
  tenant_keys: number;
  secrets_total: number;
  secrets_legacy: number;
  keys_loaded: number;
}

export interface SymbolExposure {
  symbol: string; positions: number; quantity: number; notional: number; pct_of_capital: number;
  unrealised_pnl: number; risk_at_stop: number; strategies: string[];
}

export interface PortfolioExposure {
  as_of: string; capital: number; open_positions: number; gross_notional: number; net_notional: number; long_notional: number;
  short_notional: number; gross_pct_of_capital: number; unrealised_pnl: number; realised_today: number; risk_at_stops: number;
  risk_pct_of_capital: number; largest_symbol_pct: number; by_symbol: SymbolExposure[]; by_strategy: Record<string, number>;
  by_mode: Record<string, number>; warnings: string[]; price_source: "ltp" | "entry"; priced_symbols: string[];
}

export interface DegradationMetrics {
  trades: number; win_rate: number | null; net_pnl: number; expectancy: number | null; profit_factor: number | null;
  avg_win: number | null; avg_loss: number | null;
}

export interface DegradationRow {
  strategy_id: string;
  live: DegradationMetrics;
  recent_20: DegradationMetrics;
  backtest: { run_id: number; win_rate: number | null; expectancy: number | null; profit_factor: number | null; net_pnl: number | null; total_trades: number | null; data_to: string | null } | null;
  status: "OK" | "WATCH" | "DEGRADED" | "NO_BASELINE" | "INSUFFICIENT_DATA";
  reasons: string[];
}

export interface DegradationReport { strategies: DegradationRow[]; degraded: number; watch: number; note: string }

// ---- Phase P: tax report, FX --------------------------------------------------------------------
export interface TaxClassTotals {
  trades: number; winners: number; gross_pnl: number; charges: number; net_pnl: number; turnover: number; sell_value: number;
  stt_estimate: number; ctt_estimate: number; tds_estimate: number; taxable_gains: number; tax_estimate: number; disallowed_losses: number;
  top_symbols: { symbol: string; net_pnl: number }[]; income_head: string;
}
export interface TaxReport {
  financial_year: string; mode: string; generated_at: string; trades: number; net_pnl: number;
  classes: Record<"EQUITY_INTRADAY" | "FNO" | "CRYPTO", TaxClassTotals>; notes: string[]; rates: Record<string, Record<string, number>>;
}
export interface FxRate { base: string; quote: string; rate: number; source: string; as_of: string | null }

export interface Incident {
  id: number; severity: "WARNING" | "CRITICAL" | "EMERGENCY"; title: string; summary: string; status: "OPEN" | "MITIGATED" | "RESOLVED";
  source: string; tenant_id: number | null; started_at: string | null; mitigated_at: string | null; resolved_at: string | null;
  root_cause: string | null; actions_taken: string | null; audit_log_from_id: number | null; audit_log_to_id: number | null;
  data_loss_minutes: number | null; downtime_minutes: number | null; opened_by: number | null; created_at: string | null;
}

export interface OptimizeRow {
  params: Record<string, number | string>;
  in_sample: { trades: number; net_pnl: number; win_rate: number; profit_factor: number | null; expectancy: number; max_drawdown: number };
  out_of_sample: { trades: number; net_pnl: number; win_rate: number; profit_factor: number | null; expectancy: number; max_drawdown: number };
  // P0.6 / B3: `score` is the in-sample metric the rows are ranked by; `validation` is the out-of-sample figure.
  score: number | null; validation?: number | null; overfit_gap: number | null; flags: string[];
}

export interface OptimizeResult {
  metric: string; split: number; in_sample_bars: number; out_of_sample_bars: number; combinations: number;
  best: OptimizeRow | null; best_confirmed_out_of_sample?: boolean | null; robust_count: number; results: OptimizeRow[]; note: string;
}

// Phase Z: factor and risk models.
export interface QuantSymbolInput { symbol: string; candles: OHLCVBar[]; fundamentals?: Record<string, number> | null }

export interface FactorRow {
  symbol: string; close: number; bars: number; raw: Record<string, number | null>; z: Record<string, number | null>;
  composite: number | null; rank: number | null; bucket: "LONG" | "SHORT" | "NEUTRAL"; coverage: number;
}

// Phase AG: value/quality inputs derived from the Fundamentals module's stored financials.
export interface FundamentalsFill { symbol: string; period: string | null; values: Record<string, number>; missing: string[] }
export interface FundamentalsFillSummary { enabled: boolean; filled: Record<string, FundamentalsFill>; note: string }

export interface FactorTable {
  rows: FactorRow[]; weights: Record<string, number>; bars_per_year: number; universe: number; factors: string[]; warnings: string[];
  fundamentals?: FundamentalsFillSummary;
}

export interface PortfolioRisk {
  vol_annual: number | null; var_95: number | null; cvar_95: number | null; max_drawdown_pct: number | null; diversification_ratio: number | null; bars: number;
}

export interface QuantRisk {
  bars: number; symbols: string[]; bars_per_year: number; correlation: Record<string, Record<string, number | null>>;
  volatility: Record<string, number | null>; betas: Record<string, number | null>; benchmark: string | null;
  weights_used: Record<string, number>; weights_source: string; portfolio: PortfolioRisk; risk_contributions: Record<string, number | null>;
  suggested: { inverse_volatility: Record<string, number>; risk_parity: Record<string, number>; inverse_volatility_portfolio: PortfolioRisk; risk_parity_portfolio: PortfolioRisk };
  max_drawdown_pct: Record<string, number | null>; warnings: string[]; disclaimer: string;
}

export interface QuantExposure {
  weights: Record<string, number>; weights_source: string; exposure: Record<string, number | null>; table: FactorTable; notes: string[]; reading: string;
  fundamentals?: FundamentalsFillSummary;
}

// Phase AA: broker candles for the research pages.
export interface CandleSource { broker: string; account_label: string; token_status: string; token_expires_at: string | null; usable: boolean }
export interface CandleSourcesResponse { sources: CandleSource[]; usable: boolean; timeframes: string[]; max_symbols: number; intraday_max_lookback_days: number; daily_max_lookback_days: number }
export interface CandlesResponse {
  source: { broker: string; account_label: string }; exchange: string; timeframe: string; base_interval: string; lookback_days: number; before?: string | null; fetched_at: string;
  symbols: Record<string, { bars: OHLCVBar[]; count: number; first: string | null; last: string | null; error: string | null }>; warnings: string[]; note: string;
}

export interface LtpResponse {
  symbol: string; exchange: string; source_broker: string; fetched_at: string; ltp: number; timestamp: string | null; age_seconds: number | null;
  stale: boolean; stale_reason: string | null; source: "tick" | "quote" | "ltp"; bid: number | null; ask: number | null; volume: number | null;
}

// Phase AB: go-live checklists.
export interface ReadinessItem { key: string; title: string; status: "ok" | "todo" | "warn" | "info"; detail: string; fix: string; link: string | null; scope: string }
export interface ReadinessChecklist { target: string; ready: boolean; summary: { ok: number; todo: number; warn: number; info: number }; items: ReadinessItem[]; checked_at: string; note: string }

// Phase AD: broker option chains and exchange holidays.
export interface OptionChainsResponse {
  source: { broker: string; account_label: string }; expiry: string | null; fetched_at: string;
  symbols: Record<string, { chain: OptionChain | null; rows: number; error: string | null }>; warnings: string[]; note: string;
}
export interface MarketHoliday { id: number; exchange: string; holiday_date: string; description: string }

// Phase AP: the strategy interview.
export interface InterviewOption { value: string; en: string; mr: string }
export interface InterviewQuestion {
  id: string;
  kind: "choice" | "number" | "symbol";
  en: string;
  mr: string;
  why_en: string;
  why_mr: string;
  options: InterviewOption[];
  default: string | null;
}
export interface InterviewStart {
  needs_interview: boolean;
  language: "en" | "mr";
  prefill: Record<string, string>;
  remaining: string[];
  questions: InterviewQuestion[];
  intro: string;
  /** P0.10: the Marathi line under the intro. */
  intro_mr?: string;
  /** Phase AQ: what the Copilot remembers from last time (null on a first visit). */
  profile: { answers: Record<string, string | number>; preferences: InterviewPreferences } | null;
}
// Phase AQ: options, feedback and the remembered profile.
export interface InterviewPreferences {
  risk_bias: number;
  trades_bias: number;
  reward_bias: number;
  simplicity: number;
  rejected: string[];
  chosen: { option: string; strategy_id: string | null; match?: number }[];
  match_history: number[];
}
export interface InterviewOptionMeta {
  id: "safe" | "balanced" | "active";
  label: string;
  summary: string;
  /** P0.10: the Marathi lines under the template name and summary (the interview is bilingual). */
  label_mr?: string;
  summary_mr?: string;
  /** P0.8-D: no score of any kind. Whether the template's own regime filter is open on today's candles
   * (data, not a recommendation); null when no strategy could be tested. */
  regime_filter_open: boolean | null;
  headline: { strategy: string; risk_pct: number; trades_per_day: number; min_rr: number } | null;
}
export interface FeedbackOption { code: string; en: string; mr: string }
export interface InterviewCandidate {
  strategy_id: string;
  name: string;
  family: string;
  timeframes: string[];
  description: string;
  regime_fit: number;
  score: number;
  evidence: { tested: boolean; total_trades: number; win_rate: number; net_pnl: number; profit_factor: number | null; text: string };
}
export interface InterviewPlan {
  language: "en" | "mr";
  answers: Record<string, string | number>;
  market: { last_price: number; bias: "BULLISH" | "BEARISH" | "NEUTRAL"; regime: Regime; higher_regime: Regime; higher_timeframe: string; work_timeframe: string; today: { change_pct: number; trend: string } };
  recommended: InterviewCandidate | null;
  alternatives: InterviewCandidate[];
  risk_config: RiskConfig;
  deployment: DeploymentCreateRequest | null;
  sections: { id: string; title: string; title_mr?: string; lines: string[] }[];
  warnings: string[];
  ai_prompt: string;
  disclaimer: string;
  option?: InterviewOptionMeta;
  options?: InterviewPlan[];
  best_option?: string | null;   // P0.8-D: always null - nothing is marked as best
  /** P0.8 / A3: the server-held candidate behind this option; "Deploy in PAPER" sends it with the risk acceptance. */
  candidate_id?: number | null;
  preferences?: InterviewPreferences;
  feedback_options?: FeedbackOption[];
  changes?: string[];
}

// Phase AR: the Copilot's market memory.
export interface MemorySnapshot {
  symbol: string;
  exchange: string;
  kind: "SYMBOL" | "CUE" | "GLOBAL";
  source: string;
  last_price: number | null;
  change_pct: number | null;
  bias: "BULLISH" | "BEARISH" | "NEUTRAL" | null;
  regime: string | null;
  higher_regime: string | null;
  structure: string | null;
  captured_at: string | null;
  payload: Record<string, unknown>;
}
export interface MarketMemory {
  symbols: MemorySnapshot[];
  cues: MemorySnapshot[];
  history: Record<string, { date: string; bias: string | null; regime: string | null; last_price: number | null; change_pct: number | null }[]>;
  updated_at: string | null;
  globals?: MemorySnapshot[];
  watchlist?: string[];
  interval_minutes?: number;
  report?: { symbols: number; cues: number; globals?: number; errors: string[] };
  // Phase AU: what the world's markets usually mean for India, and where the data comes from.
  global_view?: string[];
  global_source?: string;
  global_gift_note?: string;
  global_enabled?: boolean;
  // Phase BC: the deterministic market sentiment read stored with the memory.
  sentiment?: SentimentRead | null;
  sentiment_view?: string[];
}

/** Phase BD-lite: the market thesis of one symbol (shadow overlay only). */
export interface ThesisFactor { factor: string; weight: number; available: boolean; direction: -1 | 0 | 1; strength: number; value: unknown }
export interface ThesisScenario { trigger?: number; /** P0.8-D: indices only, unless the operator's thesis_stock_targets flag is on */ target?: number; invalidation?: number; low?: number; high?: number; text: string }
export interface MarketThesis {
  id?: number;
  symbol: string;
  as_of: string;
  lang: string;
  direction: "BULLISH" | "BEARISH" | "NEUTRAL";
  confidence: number | null;   // P0.8-D: null for a single stock (no confidence %), unless the operator's flag allows
  agreement: { net: number; direction: string; confidence: number; agreeing: number; with_opinion: number; share: number; coverage: number; conflict: boolean; matrix: ThesisFactor[] };
  scenarios: Partial<Record<"bull" | "base" | "bear", ThesisScenario>>;
  /** P0.9: false for a single stock without the operator's flag - no price scenarios and no score. */
  detail_shown?: boolean;
  shadow: { size_multiplier: number; reasons: string[]; mode: "shadow"; applied: false; note: string };
  events: { kind: string; action: string; size_cut_pct: number | null; start_time: string | null; end_time: string | null; description: string | null; global: boolean }[];
  inputs: Record<string, unknown>;
  lines: string[];
  narrative: string;
  narrative_source: "rules" | "model";
  outcome?: string | null;
  score?: number | null;
}
export interface ThesisHistory {
  items: { id: number; symbol: string; day: string; direction: string; confidence: number; agreement: number; shadow_multiplier: number; last_price: number | null; outcome: string | null; score: number | null; narrative_source: string; created_at: string | null }[];
  scoreboard: { scored: number; hits: number; misses: number; flat: number; hit_rate: number | null; shadow_mode: "shadow"; shadow_applied: false };
}

export interface SentimentComponent { score: number | null; weight: number; configured_weight: number; input: unknown }
export interface SentimentRead {
  score: number;
  label: "RISK_ON" | "RISK_OFF" | "NEUTRAL" | "UNKNOWN";
  components: Record<"pcr" | "vix" | "breadth" | "global" | "fii_dii", SentimentComponent>;
  missing: string[];
  coverage: number;
  news: { score: number; items: number; label: "RISK_ON" | "RISK_OFF" | "NEUTRAL" } | null;
  as_of: string;
  source: string;
}

// Phase AT: the guide.
export interface GuideConcept { id: string; title: string; body?: string; related?: string[] }
export interface GuideAnswer {
  answer: string;
  source: "library" | "ai";
  concepts: GuideConcept[];
  related: { id: string; title: string }[];
  used_market_memory: boolean;
  note?: string;
}

// Phase AV: the Copilot home - daily briefing, trade coach, ask-anything.
export type DayKind = "TREND_UP" | "TREND_DOWN" | "RANGE" | "VOLATILE" | "UNKNOWN";
export interface BriefDeployment {
  id: number; strategy_id: string; symbol: string; timeframe: string; mode: string; status: string; holding: string | null;
  family: string | null; regime: string | null; state: "ok" | "paused" | "closed" | "error" | "stale" | "regime";
  last_evaluated_at: string | null; last_signal_at: string | null; why: string[];
}
export interface BriefDay {
  realised_pnl: number; trades_today: number; max_trades: number; open_positions: number; max_open: number;
  loss_limit: number; loss_used: number; loss_left: number; consecutive_losses: number; max_consecutive_losses: number; active: boolean;
}
export interface DailyBrief {
  as_of: string; language: "en" | "mr"; experience: string | null;
  session: { open: boolean; text: string; next_open: string | null; holidays_next_7_days: string[] };
  day_type: { kind: DayKind; regime: string; symbol: string | null; bias: string | null; change_pct: number | null; higher_regime: string | null; vix: number | null };
  plan: { headline: string; lines: string[]; fit_families: string[]; avoid_families: string[] };
  global_mood: { label: "POSITIVE" | "NEGATIVE" | "MIXED"; score: number; drivers: string[] } | null;
  sentiment?: SentimentRead | null;
  sentiment_view?: string[];
  market_updated_at: string | null;
  /** P0.10: how old the market figures are; `figures_shown` false = none, stale, or a placeholder (every symbol the same). */
  market_data?: { state: "fresh" | "stale" | "suspect" | "none"; updated_at: string | null; age_minutes: number | null; figures_shown: boolean };
  market: { symbols: MemorySnapshot[]; cues: MemorySnapshot[]; globals: MemorySnapshot[] };
  events: { kind: string; action: string; description: string; start_time: string | null; end_time: string | null }[];
  you: { PAPER: BriefDay; LIVE: BriefDay };
  deployments: BriefDeployment[];
  checklist: { id: string; ok: boolean | null; text: string }[];
}
export interface CoachFlag { id: string; severity: "high" | "medium" | "low" | "good"; title: string; text: string; tip: string; evidence: (string | number)[] }
export interface CoachGroup { key: string; trades: number; wins: number; win_rate: number; net_pnl: number; avg_pnl: number; label?: string }
export interface CoachReview {
  period: { days: number | null; mode: string; from: string | null; to: string | null };
  stats: {
    trades: number; wins?: number; losses?: number; win_rate?: number; net_pnl?: number; avg_win?: number; avg_loss?: number;
    profit_factor?: number | null; expectancy?: number; expectancy_r?: number | null; avg_win_r?: number | null; avg_loss_r?: number | null;
    max_drawdown?: number; longest_losing_streak?: number; trading_days?: number; best_day?: number; worst_day?: number;
  };
  flags: CoachFlag[]; focus: string[]; score: number | null; grade: string | null;
  by_strategy: CoachGroup[]; by_hour: CoachGroup[]; by_weekday: CoachGroup[]; equity: number[];
}
export interface CopilotReply {
  intent: "interview" | "coach" | "deployments" | "brief" | "guide";
  language: "en" | "mr"; answer: string; source: "rules" | "ai"; note?: string;
  action: { tab: string; label: string };
  prefill?: Record<string, unknown>; prompt?: string;
  concepts?: GuideConcept[]; related?: { id: string; title: string }[];
  coach?: { stats: CoachReview["stats"]; grade: string | null; score: number | null; flags: CoachFlag[] };
  brief?: { day_type: DailyBrief["day_type"]; plan: DailyBrief["plan"]; checklist: DailyBrief["checklist"]; deployments: BriefDeployment[] };
}

// Phase AW: the Copilot strategist - live market study and validated strategies.
export interface StudyTimeframe {
  timeframe: string; bars: number; close: number | null; ema20: number | null; ema50: number | null; ema200: number | null;
  rsi: number | null; adx: number | null; regime: string; trend: "UP" | "DOWN" | "MIXED"; trend_text: string; above_ema200: boolean | null;
}
export interface MarketStudy {
  symbol: string; last_price: number; as_of: string; timeframes: StudyTimeframe[];
  levels: Record<string, number | null>;
  ladder: { name: string; key: string; price: number; distance_pct: number | null }[];
  atr_5m: number | null; atr_day: number | null; atr_5m_pct: number | null;
  regime: string; higher_regime: string; bias: "BULLISH" | "BEARISH" | "NEUTRAL"; bias_score: number; confidence: number | null; detail_shown?: boolean;
  character: "TREND" | "RANGE" | "VOLATILE";
  scenarios: { id: "bull" | "bear" | "range"; trigger: number | null; trigger_name?: string; target?: number; low?: number; high?: number; text: string }[];
  lines: string[]; vix: number | null; data_source?: string;
}
export interface SimMetrics {
  trades: number; win_rate: number; expectancy_r: number; profit_factor: number | null; total_r: number; max_dd_r: number;
  avg_win_r: number | null; avg_loss_r: number | null;
}
export interface StrategyCandidate {
  id: string; name: string; family: string; direction: "LONG" | "SHORT" | "BOTH"; timeframe: string; why: string;
  params: Record<string, unknown>; rules: { long: string[]; short: string[] }; exits: string;
  // Phase BF: the same rules in the trader's words (mr/en), the direction and timeframe in words, a one-line summary.
  rules_text?: { long: string[]; short: string[] }; direction_text?: string; timeframe_text?: string; summary?: string;
  triggers: { name: string; price: number }[]; stop_points: number | null; risk_amount: number; quantity_hint: number | null;
  in_sample: SimMetrics; out_of_sample: SimMetrics; all: SimMetrics; oos_sessions: string[];
  verdict: "robust" | "overfit" | "weak" | "insufficient" | "sample" | "untested" | "thin"; verdict_text: string;
  trades: { date: string; dir: string; entry: number; exit: number; reason: string; pts: number; r: number }[];
  config: CustomStrategyConfig; source: "template" | "ai";
  /** P0.8 / A3: the server-held candidate this card stands for; adopt sends this id, never the config. */
  candidate_id?: number;
}
export interface StrategistResult {
  study: MarketStudy; style: string; base_timeframe: string; higher_timeframe: string; sides: string[];
  candidates: StrategyCandidate[]; best: string | null; notes: string[]; tested: number; data_source: string;
  ai_candidates: number; provider: string;
  request_parsed?: StrategistRequestParsed | null; language?: "en" | "mr";
}
/** Phase BF: what the strategist understood from a plain-words request. */
export interface StrategistRequestParsed {
  symbol: string; style: "intraday" | "scalping"; direction: "auto" | "long" | "short" | "both"; language: "en" | "mr";
  matched: Partial<Record<"symbol" | "style" | "direction" | "language", string>>; text: string; summary?: string;
}


/** P0.9: the language of the AI's written answers; the dashboard is English only. */
export interface AiPreferences { ai_language: "en" | "mr"; languages: { code: "en" | "mr"; label: string }[] }
