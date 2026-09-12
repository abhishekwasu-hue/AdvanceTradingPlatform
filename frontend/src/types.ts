export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface UserResponse {
  id: number;
  email: string;
}

export interface TradeRecord {
  id: number;
  mode: string;
  symbol: string;
  strategy_id: string;
  direction: string;
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
export type IndicatorName = "EMA" | "SMA" | "RSI" | "ADX" | "PLUS_DI" | "MINUS_DI" | "ATR" | "SUPERTREND" | "CLOSE" | "OPEN" | "HIGH" | "LOW";

export interface Operand {
  type: "value" | "indicator";
  value: number;
  indicator: IndicatorName;
  period: number;
  multiplier: number;
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
