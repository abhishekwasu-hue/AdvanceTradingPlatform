import type {
  AnalyticsSummary,
  AuditLogEntry,
  BacktestResult,
  BrokerCredentialsInput,
  CustomStrategyConfig,
  CustomStrategyResponse,
  EnrichedSignal,
  MarkPriceResponse,
  MarketStructureResult,
  NotificationEntry,
  OHLCVBar,
  OptionChain,
  OptionChainAnalysis,
  RiskConfig,
  SRZone,
  Signal,
  SignalHistoryEntry,
  StoredBrokerInfo,
  StrategyInfo,
  TokenResponse,
  TradeRecord,
  UserResponse,
} from "../types";

const BASE = "/api";
const TOKEN_KEY = "atp_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // localStorage unavailable (private mode, etc) - session just won't persist across reloads.
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // ignore
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  if (response.status === 204) return undefined as T;
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

  register: (email: string, password: string) =>
    request<TokenResponse>("/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),

  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),

  me: () => request<UserResponse>("/auth/me"),

  listTrades: () => request<TradeRecord[]>("/trades"),

  listPositions: () => request<TradeRecord[]>("/positions"),

  markPrice: (tradeId: number, currentPrice: number) =>
    request<MarkPriceResponse>(`/positions/${tradeId}/mark-price`, {
      method: "POST",
      body: JSON.stringify({ current_price: currentPrice }),
    }),

  getRiskSettings: () => request<RiskConfig>("/risk-settings"),

  updateRiskSettings: (config: RiskConfig) =>
    request<RiskConfig>("/risk-settings", { method: "PUT", body: JSON.stringify(config) }),

  getAnalyticsSummary: () => request<AnalyticsSummary>("/analytics/summary"),

  listAuditLogs: () => request<AuditLogEntry[]>("/audit-logs"),

  listSignalHistory: () => request<SignalHistoryEntry[]>("/signal-history"),

  createCustomStrategy: (config: CustomStrategyConfig) =>
    request<CustomStrategyResponse>("/custom-strategies", { method: "POST", body: JSON.stringify(config) }),

  listCustomStrategies: () => request<CustomStrategyResponse[]>("/custom-strategies"),

  deleteCustomStrategy: (id: number) =>
    request<void>(`/custom-strategies/${id}`, { method: "DELETE" }),

  listStoredBrokerCredentials: () => request<StoredBrokerInfo[]>("/broker/credentials"),

  storeBrokerCredentials: (name: string, credentials: BrokerCredentialsInput) =>
    request<void>(`/broker/${name}/credentials`, { method: "POST", body: JSON.stringify(credentials) }),

  deleteBrokerCredentials: (name: string) =>
    request<void>(`/broker/${name}/credentials`, { method: "DELETE" }),

  authenticateBroker: (name: string) =>
    request<Record<string, unknown>>(`/broker/${name}/authenticate`, { method: "POST" }),

  listNotifications: (unreadOnly = false) =>
    request<NotificationEntry[]>(`/notifications${unreadOnly ? "?unread_only=true" : ""}`),

  markNotificationRead: (id: number) =>
    request<NotificationEntry>(`/notifications/${id}/read`, { method: "POST" }),

  markAllNotificationsRead: () =>
    request<{ marked_read: number }>("/notifications/read-all", { method: "POST" }),
};
