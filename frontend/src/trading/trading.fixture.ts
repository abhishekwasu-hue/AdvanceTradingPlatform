import type { GuardianModeStatus, MarketEvent, PortfolioExposure, RiskConfig, TradeRecord } from "../types";

/** UI2-9a: made-up records for the unit tests (no real account, price or id). Symbols are "DEMO ..." names. */
export function trade(extra: Partial<TradeRecord> = {}): TradeRecord {
  return {
    id: 1, mode: "PAPER", symbol: "DEMO A", strategy_id: "demo_breakout", direction: "LONG", entry_time: "2026-10-08T04:00:00",
    entry_price: 100, quantity: 150, stop_loss: 95, target1: 110, target2: null, exit_time: null, exit_price: null, exit_reason: null,
    pnl: null, charges: 40, lot_size: 75, instrument_kind: "UNDERLYING", ...extra,
  };
}

export function exposure(extra: Partial<PortfolioExposure> = {}): PortfolioExposure {
  return {
    as_of: "2026-10-08T05:00:00", capital: 1_000_000, open_positions: 2, gross_notional: 300_000, net_notional: 100_000, long_notional: 200_000,
    short_notional: 100_000, gross_pct_of_capital: 30, unrealised_pnl: 1_500, realised_today: 2_540, risk_at_stops: 12_000,
    risk_pct_of_capital: 1.2, largest_symbol_pct: 20, warnings: [], price_source: "ltp", priced_symbols: ["DEMO A", "DEMO B"],
    by_symbol: [
      { symbol: "DEMO B", positions: 1, quantity: 50, notional: 100_000, pct_of_capital: 10, unrealised_pnl: -500, risk_at_stop: 4_000, strategies: ["demo_swing"] },
      { symbol: "DEMO A", positions: 1, quantity: 150, notional: 16_500, pct_of_capital: 45, unrealised_pnl: 1_500, risk_at_stop: 750, strategies: ["demo_breakout"] },
    ],
    by_strategy: {}, by_mode: {}, ...extra,
  };
}

export const CONFIG: RiskConfig = {
  capital: 100_000, risk_per_trade_pct: 1, max_daily_loss_pct: 3, max_trades_per_day: 5, max_open_positions: 3, max_consecutive_losses: 3,
  min_risk_reward: 1.5, lot_size: 1, max_portfolio_risk_pct: 6, stop_cooldown_minutes: 30, dd_level_1_pct: 5, dd_level_2_pct: 10,
  event_size_cut_pct: 50,
};

export function guardianMode(extra: Partial<GuardianModeStatus> = {}): GuardianModeStatus {
  return {
    equity: 98_000, peak: 104_000, drawdown_pct: 5.77, closed_trades: 12, state: "reduced", size_multiplier: 0.5,
    open_risk_by_bucket: { INDEX: 1_200 }, open_risk_total: 1_200, open_risk_pct: 1.2, portfolio_cap: 6_000,
    cooldowns: [{ underlying: "DEMO A", exit_reason: "STOP_LOSS", minutes_left: 12 }], ...extra,
  };
}

export function marketEvent(extra: Partial<MarketEvent> = {}): MarketEvent {
  return {
    id: 7, tenant_id: 1, global: false, underlying: "INDEX", event_date: "2026-11-02", start_time: null, end_time: null, kind: "BUDGET",
    action: "BLOCK", size_cut_pct: null, description: "Made-up event", created_at: null, ...extra,
  };
}
