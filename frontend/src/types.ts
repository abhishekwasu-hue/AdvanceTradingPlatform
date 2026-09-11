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
