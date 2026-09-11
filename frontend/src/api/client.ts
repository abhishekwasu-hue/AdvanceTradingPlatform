import type {
  BacktestResult,
  EnrichedSignal,
  MarketStructureResult,
  OHLCVBar,
  OptionChain,
  OptionChainAnalysis,
  SRZone,
  Signal,
  StrategyInfo,
} from "../types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string }>("/system/health"),

  listStrategies: () => request<StrategyInfo[]>("/strategies"),

  generateSignal: (strategyId: string, symbol: string, candles: Record<string, OHLCVBar[]>) =>
    request<Signal>(`/strategies/${strategyId}/signal`, {
      method: "POST",
      body: JSON.stringify({ symbol, candles }),
    }),

  enrichSignal: (
    strategyId: string,
    symbol: string,
    candles: Record<string, OHLCVBar[]>,
    optionChain?: OptionChain,
  ) =>
    request<EnrichedSignal>(`/strategies/${strategyId}/signal/enrich`, {
      method: "POST",
      body: JSON.stringify({ symbol, candles, option_chain: optionChain ?? null }),
    }),

  paperExecute: (strategyId: string, symbol: string, candles: Record<string, OHLCVBar[]>) =>
    request<{ signal: Signal; executed: boolean; reasons: string[] }>(
      `/strategies/${strategyId}/paper-execute`,
      { method: "POST", body: JSON.stringify({ symbol, candles }) },
    ),

  backtest: (strategyId: string, symbol: string, baseTimeframe: string, candles: OHLCVBar[]) =>
    request<BacktestResult>("/backtest", {
      method: "POST",
      body: JSON.stringify({ strategy_id: strategyId, symbol, base_timeframe: baseTimeframe, candles }),
    }),

  priceActionStructure: (symbol: string, candles: OHLCVBar[]) =>
    request<MarketStructureResult>("/price-action/structure", {
      method: "POST",
      body: JSON.stringify({ symbol, candles }),
    }),

  supportResistanceZones: (symbol: string, candles: OHLCVBar[]) =>
    request<SRZone[]>("/support-resistance/zones", {
      method: "POST",
      body: JSON.stringify({ symbol, candles }),
    }),

  analyzeOptionChain: (chain: OptionChain) =>
    request<OptionChainAnalysis>("/option-chain/analyze", {
      method: "POST",
      body: JSON.stringify({ chain }),
    }),

  availableBrokers: () => request<{ brokers: string[] }>("/broker/available"),
};
