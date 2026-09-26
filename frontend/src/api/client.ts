import type {
  AlertChannel,
  AlertChannelUpsert,
  AlertDelivery,
  AnalyticsSummary,
  AuditLogEntry,
  BacktestResult,
  BrokerCredentialsInput,
  BrokerTokenInfo,
  Deployment,
  DeploymentCreateRequest,
  ContractSpec,
  CustomStrategyConfig,
  CustomStrategyResponse,
  EnrichedSignal,
  InviteInfo,
  MarkPriceResponse,
  MarketStructureResult,
  NewsEvent,
  NewsEventCategory,
  NewsEventResponse,
  NotificationEntry,
  OHLCVBar,
  OptionChain,
  OptionChainAnalysis,
  ParseStrategyResult,
  RiskConfig,
  ScannerRequest,
  ScannerResult,
  WebhookTokenResponse,
  SRZone,
  Signal,
  SignalHistoryEntry,
  StoredBrokerInfo,
  StrategyInfo,
  TeamInvite,
  TeamMember,
  TenantInfo,
  TokenResponse,
  TradeRecord,
  UserResponse,
  WorkerStatus,
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

  parseStrategyDescription: (text: string, name?: string) =>
    request<ParseStrategyResult>("/custom-strategies/parse", {
      method: "POST",
      body: JSON.stringify({ text, name: name ?? "Parsed Strategy" }),
    }),

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

  getWebhookToken: () => request<WebhookTokenResponse>("/webhooks/tradingview/token"),

  rotateWebhookToken: () =>
    request<WebhookTokenResponse>("/webhooks/tradingview/token/rotate", { method: "POST" }),

  runScanner: (scanRequest: ScannerRequest) =>
    request<ScannerResult>("/scanner/run", { method: "POST", body: JSON.stringify(scanRequest) }),

  listNewsEvents: (filters?: { category?: NewsEventCategory; symbol?: string; since?: string }) => {
    const params = new URLSearchParams();
    if (filters?.category) params.set("category", filters.category);
    if (filters?.symbol) params.set("symbol", filters.symbol);
    if (filters?.since) params.set("since", filters.since);
    const qs = params.toString();
    return request<NewsEventResponse[]>(`/news-events${qs ? `?${qs}` : ""}`);
  },

  createNewsEvent: (event: NewsEvent) =>
    request<NewsEventResponse>("/news-events", { method: "POST", body: JSON.stringify(event) }),

  deleteNewsEvent: (id: number) =>
    request<void>(`/news-events/${id}`, { method: "DELETE" }),

  listInstruments: () => request<ContractSpec[]>("/instruments"),

  // --- Autonomous trading core ---

  brokerTokenStatus: () => request<BrokerTokenInfo[]>("/broker/token-status"),

  upstoxOAuthStart: () => request<{ authorization_url: string }>("/broker/upstox/oauth/start"),

  workerStatus: () => request<WorkerStatus>("/system/worker-status"),

  listDeployments: (includeStopped = false) =>
    request<Deployment[]>(`/deployments${includeStopped ? "?include_stopped=true" : ""}`),

  createDeployment: (body: DeploymentCreateRequest) =>
    request<Deployment>("/deployments", { method: "POST", body: JSON.stringify(body) }),

  pauseDeployment: (id: number, reason = "") =>
    request<Deployment>(`/deployments/${id}/pause`, { method: "POST", body: JSON.stringify({ reason }) }),

  resumeDeployment: (id: number) => request<Deployment>(`/deployments/${id}/resume`, { method: "POST" }),

  stopDeployment: (id: number, reason = "") =>
    request<Deployment>(`/deployments/${id}/stop`, { method: "POST", body: JSON.stringify({ reason }) }),

  deleteDeployment: (id: number) => request<void>(`/deployments/${id}`, { method: "DELETE" }),

  // --- Alert delivery ---

  listAlertChannels: () => request<AlertChannel[]>("/alert-channels"),

  upsertAlertChannel: (type: string, body: AlertChannelUpsert) =>
    request<AlertChannel>(`/alert-channels/${type}`, { method: "PUT", body: JSON.stringify(body) }),

  deleteAlertChannel: (type: string) => request<void>(`/alert-channels/${type}`, { method: "DELETE" }),

  testAlertChannel: (type: string) =>
    request<{ ok: boolean; detail: string }>(`/alert-channels/${type}/test`, { method: "POST" }),

  listAlertDeliveries: (limit = 20) => request<AlertDelivery[]>(`/alert-channels/deliveries?limit=${limit}`),

  // --- Team ---

  getTenant: () => request<TenantInfo>("/team/tenant"),

  renameTenant: (name: string) => request<TenantInfo>("/team/tenant", { method: "PATCH", body: JSON.stringify({ name }) }),

  listMembers: () => request<TeamMember[]>("/team/members"),

  changeMemberRole: (id: number, role: string) =>
    request<TeamMember>(`/team/members/${id}`, { method: "PATCH", body: JSON.stringify({ role }) }),

  removeMember: (id: number) => request<void>(`/team/members/${id}`, { method: "DELETE" }),

  reactivateMember: (id: number) => request<TeamMember>(`/team/members/${id}/reactivate`, { method: "POST" }),

  listInvites: () => request<TeamInvite[]>("/team/invites"),

  createInvite: (email: string, role: string) =>
    request<TeamInvite>("/team/invites", { method: "POST", body: JSON.stringify({ email, role }) }),

  revokeInvite: (id: number) => request<void>(`/team/invites/${id}`, { method: "DELETE" }),

  inviteInfo: (token: string) => request<InviteInfo>(`/auth/invite/${encodeURIComponent(token)}`),

  acceptInvite: (token: string, password: string) =>
    request<TokenResponse>(`/auth/invite/${encodeURIComponent(token)}/accept`, { method: "POST", body: JSON.stringify({ password }) }),
};
