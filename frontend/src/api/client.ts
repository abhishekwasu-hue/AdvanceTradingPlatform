import type {
  StrategistResult,
  MarketStudy,
  CoachReview,
  CopilotReply,
  DailyBrief,
  GuideAnswer,
  GuideConcept,
  MarketMemory,
  InterviewPlan,
  InterviewStart,
  ContractPreview,
  ContractRules,
  ContractNoteIngest,
  ContractNoteSummary,
  AdminOverview,
  AdminPlan,
  AdminTenantDetail,
  AdminTenantSummary,
  AlertChannel,
  AlertChannelUpsert,
  AlertDelivery,
  TelegramInboundStatus,
  AnalyticsSummary,
  AuditLogEntry,
  BacktestResult,
  BrokerCredentialsInput,
  BrokerTokenInfo,
  ChartRunResponse,
  Deployment,
  DeploymentCreateRequest,
  ContractSpec,
  CustomStrategyConfig,
  CustomStrategyResponse,
  EnrichedSignal,
  InviteInfo,
  LoginEvent,
  MarkPriceResponse,
  MarketStructureResult,
  MfaStatus,
  NewsEvent,
  NewsEventCategory,
  NewsEventResponse,
  NotificationEntry,
  OHLCVBar,
  OptionChain,
  OptionChainAnalysis,
  ParseStrategyResult,
  PlatformAuditLog,
  RiskConfig,
  ScannerRequest,
  ScannerResult,
  ScanPlan,
  ScanRead,
  CandleSourcesResponse,
  MarketHoliday,
  OptionChainsResponse,
  ReadinessChecklist,
  CandlesResponse,
  LtpResponse,
  FactorTable,
  QuantExposure,
  QuantRisk,
  QuantSymbolInput,
  SessionInfo,
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
  BacktestRunSummary,
  BrokerAccount,
  ExitRules,
  MonteCarloResult,
  WalkForwardResult,
  PositionGreeks,
  RiskEvent,
  RiskLimit,
  RiskLimitRequest,
  ReconciliationReport,
  ReconciliationStatus,
  ApiKey,
  BillingOverview,
  BillingTransaction,
  MarketplaceListing,
  MarketplaceSubscription,
  PlanCatalogueEntry,
  Subscription,
  AiAction,
  AiProviderConfig,
  AiStrategyDraft,
  Regime,
  DegradationReport,
  Incident,
  OptimizeResult,
  PortfolioExposure,
  SystemStatus,
  EncryptionStatus,
  FeatureFlags,
  MemberScopes,
  ScopeCatalogueEntry,
  TenantFeatures,
  VerificationSendResult,
  TaxReport,
  FxRate,
  GuardianStatus,
  MarketEvent,
  MarketEventRequest,
  RiskCeilings,
  OptionBacktestConfig, OptionChainCoverage, OptionChainSnapshotRow,
  MarketplaceCharge, MarketplaceEarnings, MarketplacePayout, MarketplacePurchaseResponse, MarketplaceRevenue, MarketplaceTerms,
  SmokeReport,
  NewsFeedItem,
  NewsFeedSource,
  NewsFeedStatus,
} from "../types";

const BASE = "/api/v1";
const TOKEN_KEY = "atp_token";
const REFRESH_KEY = "atp_refresh";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string, refreshToken?: string | null): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
    if (refreshToken) localStorage.setItem(REFRESH_KEY, refreshToken);
  } catch {
    // localStorage unavailable (private mode, etc) - session just won't persist across reloads.
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
  } catch {
    // ignore
  }
}

// Access tokens live for minutes; the refresh token (rotated on every use) keeps the session.
// One refresh in flight at a time so a burst of 401s from parallel requests rotates once.
let refreshInFlight: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const response = await fetch(`${BASE}/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!response.ok) {
          clearToken();
          return false;
        }
        const body = (await response.json()) as TokenResponse;
        setToken(body.access_token, body.refresh_token);
        return true;
      } catch {
        return false;
      } finally {
        refreshInFlight = null;
      }
    })();
  }
  return refreshInFlight;
}

async function rawRequest(path: string, init?: RequestInit): Promise<Response> {
  const token = getToken();
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response = await rawRequest(path, init);
  if (response.status === 401 && getRefreshToken() && !path.startsWith("/auth/refresh") && !path.startsWith("/auth/login")) {
    if (await tryRefresh()) response = await rawRequest(path, init);
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export type ExportDataset = "audit-logs" | "orders" | "trades" | "login-events";

export interface ExportDownload { blob: Blob; filename: string; sha256: string; rows: string; chainIntact?: string }

/** Authenticated file download for the compliance exports (Phase D2): same token and silent
 *  refresh as `request`, but returns the body as a Blob plus the integrity headers. */
async function downloadExport(
  dataset: ExportDataset, format: "csv" | "json",
  opts: { from?: string; to?: string; scope: "tenant" | "platform"; tenantId?: number },
): Promise<ExportDownload> {
  const params = new URLSearchParams({ format });
  if (opts.from) params.set("from", opts.from);
  if (opts.to) params.set("to", opts.to);
  if (opts.scope === "platform" && opts.tenantId) params.set("tenant_id", String(opts.tenantId));
  const path = `${opts.scope === "platform" ? "/admin/exports" : "/exports"}/${dataset}?${params.toString()}`;
  let response = await rawRequest(path);
  if (response.status === 401 && getRefreshToken() && (await tryRefresh())) response = await rawRequest(path);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}: ${await response.text()}`);
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  return {
    blob: await response.blob(),
    filename: match?.[1] ?? `${dataset}.${format}`,
    sha256: response.headers.get("x-content-sha256") ?? "",
    rows: response.headers.get("x-export-rows") ?? "?",
    chainIntact: response.headers.get("x-audit-chain-intact") ?? undefined,
  };
}

export const api = {
  downloadExport,

  health: () => request<{ status: string }>("/system/health"),

  listStrategies: () => request<StrategyInfo[]>("/strategies"),
  // Phase AO: the strategy's entries/exits on the chart's own candles (not recorded as a backtest run).
  strategyChartRun: (strategyId: string, symbol: string, timeframe: string, candles: OHLCVBar[]) =>
    request<ChartRunResponse>(`/strategies/${encodeURIComponent(strategyId)}/chart-run`, { method: "POST", body: JSON.stringify({ symbol, timeframe, candles }) }),

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

  // Phase W: `options` present = the signals are traded as option structures.
  backtest: (strategyId: string, symbol: string, baseTimeframe: string, candles: OHLCVBar[], exitRules?: ExitRules | null, dataSource = "sample", options?: OptionBacktestConfig | null) =>
    request<BacktestResult>("/backtest", {
      method: "POST",
      body: JSON.stringify({ strategy_id: strategyId, symbol, base_timeframe: baseTimeframe, candles, exit_rules: exitRules ?? null, data_source: dataSource, options: options ?? null }),
    }),
  backtestMonteCarlo: (strategyId: string, symbol: string, baseTimeframe: string, candles: OHLCVBar[], exitRules?: ExitRules | null, runs = 1000, options?: OptionBacktestConfig | null) =>
    request<{ monte_carlo: MonteCarloResult }>(`/backtest/monte-carlo?runs=${runs}`, {
      method: "POST",
      body: JSON.stringify({ strategy_id: strategyId, symbol, base_timeframe: baseTimeframe, candles, exit_rules: exitRules ?? null, options: options ?? null }),
    }),
  backtestWalkForward: (strategyId: string, symbol: string, baseTimeframe: string, candles: OHLCVBar[], exitRules?: ExitRules | null, folds = 4, options?: OptionBacktestConfig | null) =>
    request<WalkForwardResult>(`/backtest/walk-forward?folds=${folds}`, {
      method: "POST",
      body: JSON.stringify({ strategy_id: strategyId, symbol, base_timeframe: baseTimeframe, candles, exit_rules: exitRules ?? null, options: options ?? null }),
    }),
  listBacktests: () => request<BacktestRunSummary[]>("/backtests"),
  optionChainCoverage: () => request<OptionChainCoverage[]>("/backtest/option-chain/coverage"),
  uploadOptionChainSnapshots: (underlying: string, rows: OptionChainSnapshotRow[]) =>
    request<{ underlying: string; received: number; written: number }>("/backtest/option-chain/snapshots", {
      method: "POST", body: JSON.stringify({ underlying, rows }),
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

  logout: () => request<void>("/auth/logout", { method: "POST" }),

  mfaVerifyLogin: (mfaToken: string, code: string) =>
    request<TokenResponse>("/auth/mfa/verify", { method: "POST", body: JSON.stringify({ mfa_token: mfaToken, code }) }),

  mfaStatus: () => request<MfaStatus>("/auth/mfa/status"),

  mfaEnrol: () => request<{ secret: string; otpauth_uri: string }>("/auth/mfa/enrol", { method: "POST" }),

  mfaConfirm: (code: string) =>
    request<{ backup_codes: string[] }>("/auth/mfa/confirm", { method: "POST", body: JSON.stringify({ code }) }),

  mfaStepUp: (code: string) => request<void>("/auth/mfa/step-up", { method: "POST", body: JSON.stringify({ code }) }),

  mfaRegenerateBackupCodes: (code: string) =>
    request<{ backup_codes: string[] }>("/auth/mfa/backup-codes", { method: "POST", body: JSON.stringify({ code }) }),

  mfaDisable: (password: string, code: string) =>
    request<void>("/auth/mfa/disable", { method: "POST", body: JSON.stringify({ password, code }) }),

  forgotPassword: (email: string) =>
    request<{ detail: string }>("/auth/password/forgot", { method: "POST", body: JSON.stringify({ email }) }),

  resetInfo: (token: string) =>
    request<{ email_hint: string; valid: boolean; reason: string | null }>(`/auth/password/reset/${encodeURIComponent(token)}`),

  resetPassword: (token: string, password: string) =>
    request<TokenResponse>(`/auth/password/reset/${encodeURIComponent(token)}`, { method: "POST", body: JSON.stringify({ password }) }),

  changePassword: (currentPassword: string, newPassword: string) =>
    request<TokenResponse>("/auth/password/change", {
      method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),

  issueMemberResetLink: (id: number) =>
    request<{ reset_url: string; expires_at: string; delivered_by_email: boolean }>(`/team/members/${id}/reset-link`, { method: "POST" }),

  logoutEverywhere: () => request<void>("/auth/logout-all", { method: "POST" }),

  listSessions: () => request<SessionInfo[]>("/auth/sessions"),

  loginHistory: () => request<LoginEvent[]>("/auth/login-history"),

  revokeSession: (id: number) => request<void>(`/auth/sessions/${id}`, { method: "DELETE" }),

  logoutMemberEverywhere: (id: number) => request<void>(`/team/members/${id}/logout-all`, { method: "POST" }),

  listTrades: () => request<TradeRecord[]>("/trades"),

  listPositions: () => request<TradeRecord[]>("/positions"),

  listContractNotes: () => request<ContractNoteSummary[]>("/contract-notes"),

  uploadContractNote: async (file: File, brokerName: string, apply: boolean): Promise<ContractNoteIngest> => {
    const form = new FormData();
    form.append("file", file);
    form.append("broker_name", brokerName);
    form.append("apply", apply ? "true" : "false");
    // No JSON content type: the browser sets the multipart boundary itself.
    const token = getToken();
    let response = await fetch(`${BASE}/contract-notes`, { method: "POST", body: form, headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (response.status === 401 && getRefreshToken() && (await tryRefresh())) {
      response = await fetch(`${BASE}/contract-notes`, { method: "POST", body: form, headers: { Authorization: `Bearer ${getToken()}` } });
    }
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}: ${await response.text()}`);
    return response.json() as Promise<ContractNoteIngest>;
  },

  markPrice: (tradeId: number, currentPrice: number) =>
    request<MarkPriceResponse>(`/positions/${tradeId}/mark-price`, {
      method: "POST",
      body: JSON.stringify({ current_price: currentPrice }),
    }),

  getRiskSettings: () => request<RiskConfig>("/risk-settings"),

  listRiskLimits: () => request<RiskLimit[]>("/risk/limits"),
  upsertRiskLimit: (body: RiskLimitRequest) => request<RiskLimit>("/risk/limits", { method: "PUT", body: JSON.stringify(body) }),
  deleteRiskLimit: (id: number) => request<void>(`/risk/limits/${id}`, { method: "DELETE" }),
  listRiskEvents: (params?: { strategy_id?: string; status?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.strategy_id) q.set("strategy_id", params.strategy_id);
    if (params?.status) q.set("status", params.status);
    if (params?.limit) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<RiskEvent[]>(`/risk/events${qs ? `?${qs}` : ""}`);
  },

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

  storeBrokerCredentials: (name: string, credentials: BrokerCredentialsInput, accountLabel = "primary") =>
    request<void>(`/broker/${name}/credentials?account_label=${encodeURIComponent(accountLabel)}`, { method: "POST", body: JSON.stringify(credentials) }),

  listAccounts: () => request<BrokerAccount[]>("/accounts"),
  syncAccount: (id: number) => request<BrokerAccount>(`/accounts/${id}/sync`, { method: "POST" }),
  setAccountStatus: (id: number, enabled: boolean) => request<BrokerAccount>(`/accounts/${id}/${enabled ? "enable" : "disable"}`, { method: "POST" }),
  setDefaultAccount: (id: number) => request<BrokerAccount>(`/accounts/${id}/default`, { method: "POST" }),

  deleteBrokerCredentials: (name: string, accountLabel = "primary") =>
    request<void>(`/broker/${name}/credentials?account_label=${encodeURIComponent(accountLabel)}`, { method: "DELETE" }),

  authenticateBroker: (name: string) =>
    request<Record<string, unknown>>(`/broker/${name}/authenticate`, { method: "POST" }),
  // Phase AJ: read-only probes of a stored broker session (never places an order).
  brokerSmokeTest: (name: string, accountLabel = "primary") =>
    request<SmokeReport>(`/broker/${name}/smoke-test?account_label=${encodeURIComponent(accountLabel)}`, { method: "POST" }),

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
  // Phase Y: AI scanner - plan from plain language, read of a result.
  scannerAiPlan: (text: string, language = "en") =>
    request<ScanPlan>("/scanner/ai/plan", { method: "POST", body: JSON.stringify({ text, language }) }),
  // Phase AD: broker option chains and exchange holidays.
  marketDataOptionChains: (underlyings: string[], expiry?: string, broker?: string) =>
    request<OptionChainsResponse>("/market-data/option-chains", { method: "POST", body: JSON.stringify({ underlyings, expiry: expiry ?? null, broker: broker ?? null }) }),
  marketHolidays: (year?: number, exchange = "NSE") => request<MarketHoliday[]>(`/market-holidays?exchange=${exchange}${year ? `&year=${year}` : ""}`),
  addMarketHoliday: (body: { exchange: string; holiday_date: string; description: string }) =>
    request<MarketHoliday>("/market-holidays", { method: "POST", body: JSON.stringify(body) }),
  deleteMarketHoliday: (id: number) => request<void>(`/market-holidays/${id}`, { method: "DELETE" }),
  // Phase AB: go-live checklists.
  readiness: (target: "PAPER" | "LIVE" = "PAPER") => request<ReadinessChecklist>(`/readiness?target=${target}`),
  adminReadiness: () => request<ReadinessChecklist>("/admin/readiness"),
  // Phase AA: broker candles for the research pages.
  marketDataSources: () => request<CandleSourcesResponse>("/market-data/sources"),
  // Phase AN: the live chart's last price (tick > quote with staleness > bare LTP).
  marketDataLtp: (symbol: string, exchange = "NSE", broker?: string) =>
    request<LtpResponse>(`/market-data/ltp?symbol=${encodeURIComponent(symbol)}&exchange=${encodeURIComponent(exchange)}${broker ? `&broker=${encodeURIComponent(broker)}` : ""}`),
  /** `before` (YYYY-MM-DD, IST): older history ending the day before it - a chart scrolled back past its oldest bar. */
  marketDataCandles: (symbols: string[], timeframe: string, lookbackDays: number, exchange = "NSE", broker?: string, before?: string) =>
    request<CandlesResponse>("/market-data/candles", { method: "POST", body: JSON.stringify({ symbols, timeframe, lookback_days: lookbackDays, exchange, broker: broker ?? null, before: before ?? null }) }),
  // Phase Z: factor and risk models.
  quantFactors: (symbols: QuantSymbolInput[], weights?: Record<string, number>, useFundamentals = true) =>
    request<FactorTable>("/quant/factors", { method: "POST", body: JSON.stringify({ symbols, weights: weights ?? null, use_fundamentals: useFundamentals }) }),
  quantRisk: (symbols: QuantSymbolInput[], weights?: Record<string, number>, benchmark?: string) =>
    request<QuantRisk>("/quant/risk", { method: "POST", body: JSON.stringify({ symbols, weights: weights ?? null, benchmark: benchmark ?? null }) }),
  quantExposure: (symbols: QuantSymbolInput[], weights?: Record<string, number>) =>
    request<QuantExposure>("/quant/exposure", { method: "POST", body: JSON.stringify({ symbols, weights: weights ?? null }) }),
  scannerAiRead: (scanRequest: ScannerRequest, result: ScannerResult, language = "en") =>
    request<ScanRead>("/scanner/ai/read", { method: "POST", body: JSON.stringify({ request: scanRequest, result, language }) }),

  listNewsEvents: (filters?: { category?: NewsEventCategory; symbol?: string; since?: string; origin?: "MANUAL" | "FEED" }) => {
    const params = new URLSearchParams();
    if (filters?.category) params.set("category", filters.category);
    if (filters?.symbol) params.set("symbol", filters.symbol);
    if (filters?.since) params.set("since", filters.since);
    if (filters?.origin) params.set("origin", filters.origin);
    const qs = params.toString();
    return request<NewsEventResponse[]>(`/news-events${qs ? `?${qs}` : ""}`);
  },

  createNewsEvent: (event: NewsEvent) =>
    request<NewsEventResponse>("/news-events", { method: "POST", body: JSON.stringify(event) }),

  deleteNewsEvent: (id: number) =>
    request<void>(`/news-events/${id}`, { method: "DELETE" }),
  // Phase BB: the live news feed
  newsFeedStatus: () => request<NewsFeedStatus>("/news-feed/status"),
  setNewsFeedSource: (id: string, on: boolean) => request<{ sources: NewsFeedSource[] }>(`/news-feed/sources/${id}`, { method: "PUT", body: JSON.stringify({ on }) }),
  refreshNewsFeed: () => request<NewsFeedStatus["last_run"]>("/news-feed/refresh", { method: "POST" }),
  newsFeedItems: (hours = 24, minSeverity = 1) => request<NewsFeedItem[]>(`/news-feed/items?hours=${hours}&min_severity=${minSeverity}`),
  classifyNewsFeed: () => request<{ classified: number; proposals: number; skipped?: string }>("/news-feed/classify", { method: "POST" }),

  listInstruments: () => request<ContractSpec[]>("/instruments"),

  // --- Autonomous trading core ---

  brokerTokenStatus: () => request<BrokerTokenInfo[]>("/broker/token-status"),

  upstoxOAuthStart: () => request<{ authorization_url: string }>("/broker/upstox/oauth/start"),

  positionGreeks: () => request<PositionGreeks>("/positions/greeks"),

  reconciliationStatus: () => request<ReconciliationStatus>("/reconciliation/status"),
  runReconciliation: (brokerName: string) =>
    request<ReconciliationReport>(`/reconciliation/${brokerName}`, { method: "POST" }),

  workerStatus: () => request<WorkerStatus>("/system/worker-status"),

  listDeployments: (includeStopped = false) =>
    request<Deployment[]>(`/deployments${includeStopped ? "?include_stopped=true" : ""}`),

  createDeployment: (body: DeploymentCreateRequest) =>
    request<Deployment>("/deployments", { method: "POST", body: JSON.stringify(body) }),

  previewContract: (body: ContractRules & { symbol: string; spot?: number | null }) =>
    request<ContractPreview>("/deployments/preview-contract", { method: "POST", body: JSON.stringify(body) }),

  pauseDeployment: (id: number, reason = "") =>
    request<Deployment>(`/deployments/${id}/pause`, { method: "POST", body: JSON.stringify({ reason }) }),

  resumeDeployment: (id: number) => request<Deployment>(`/deployments/${id}/resume`, { method: "POST" }),

  stopDeployment: (id: number, reason = "") =>
    request<Deployment>(`/deployments/${id}/stop`, { method: "POST", body: JSON.stringify({ reason }) }),

  deleteDeployment: (id: number) => request<void>(`/deployments/${id}`, { method: "DELETE" }),

  // --- Alert delivery ---

  // ---- Phase M: platform controls, portfolio, degradation, incidents, optimisation
  systemStatus: () => request<SystemStatus>("/system/status"),
  adminControls: () => request<SystemStatus>("/admin/controls"),
  adminSetMaintenance: (on: boolean, message?: string) =>
    request<SystemStatus>("/admin/controls/maintenance", { method: "PUT", body: JSON.stringify({ on, message }) }),
  adminSetDisabledBrokers: (names: string[]) =>
    request<SystemStatus>("/admin/controls/brokers", { method: "PUT", body: JSON.stringify({ names }) }),
  // ---- Phase V1: Risk Guardian
  guardianStatus: () => request<GuardianStatus>("/risk-guardian/status"),
  riskCeilings: () => request<RiskCeilings>("/risk-settings/ceilings"),
  listMarketEvents: (from?: string, to?: string) => {
    const params = new URLSearchParams();
    if (from) params.set("from", from);
    if (to) params.set("to", to);
    const qs = params.toString();
    return request<MarketEvent[]>(`/risk-guardian/events${qs ? `?${qs}` : ""}`);
  },
  createMarketEvent: (body: MarketEventRequest) => request<MarketEvent>("/risk-guardian/events", { method: "POST", body: JSON.stringify(body) }),
  deleteMarketEvent: (id: number) => request<void>(`/risk-guardian/events/${id}`, { method: "DELETE" }),
  adminRiskCeilings: () => request<RiskCeilings>("/admin/controls/risk-ceilings"),
  adminSetRiskCeilings: (values: Record<string, number>) =>
    request<RiskCeilings>("/admin/controls/risk-ceilings", { method: "PUT", body: JSON.stringify({ values }) }),
  // ---- Phase N: scopes, email verification, feature flags, encryption status
  scopeCatalogue: () => request<ScopeCatalogueEntry[]>("/auth/scopes"),
  memberScopes: (memberId: number) => request<MemberScopes>(`/team/members/${memberId}/scopes`),
  setMemberScopes: (memberId: number, deny: string[], grant: string[]) =>
    request<MemberScopes>(`/team/members/${memberId}/scopes`, { method: "PUT", body: JSON.stringify({ deny, grant }) }),
  resendVerification: () => request<VerificationSendResult>("/auth/verify-email/resend", { method: "POST" }),
  verifyEmail: (token: string) => request<UserResponse>(`/auth/verify-email/${encodeURIComponent(token)}`, { method: "POST" }),
  adminVerifyEmail: (memberId: number) => request<{ id: number; email_verified: boolean }>(`/team/members/${memberId}/verify-email`, { method: "POST" }),
  myFeatures: () => request<TenantFeatures>("/system/features"),
  adminFlags: () => request<FeatureFlags>("/admin/controls/flags"),
  adminSetFlag: (name: string, on: boolean, tenants: number[] = []) =>
    request<FeatureFlags>(`/admin/controls/flags/${name}`, { method: "PUT", body: JSON.stringify({ on, tenants }) }),
  adminEncryptionStatus: () => request<EncryptionStatus>("/system/encryption"),
  disableMemberTrading: (memberId: number, reason: string) =>
    request<{ id: number; trading_disabled_reason: string | null }>(`/team/members/${memberId}/trading-disable`, { method: "POST", body: JSON.stringify({ reason }) }),
  enableMemberTrading: (memberId: number) =>
    request<{ id: number; trading_disabled_reason: string | null }>(`/team/members/${memberId}/trading-enable`, { method: "POST" }),
  portfolioExposure: (livePrices = true) => request<PortfolioExposure>(`/portfolio/exposure?live_prices=${livePrices}`),
  analyticsDegradation: () => request<DegradationReport>("/analytics/degradation"),
  updateTradeJournal: (tradeId: number, body: { notes?: string | null; tags?: string[] | null }) =>
    request<TradeRecord>(`/trades/${tradeId}/journal`, { method: "PATCH", body: JSON.stringify(body) }),
  adminIncidents: (status?: string) => request<Incident[]>(`/admin/incidents${status ? `?status=${status}` : ""}`),
  adminCreateIncident: (body: { title: string; severity: string; summary?: string; tenant_id?: number | null }) =>
    request<Incident>("/admin/incidents", { method: "POST", body: JSON.stringify(body) }),
  adminUpdateIncident: (id: number, body: Partial<Pick<Incident, "status" | "summary" | "root_cause" | "actions_taken" | "data_loss_minutes" | "downtime_minutes">>) =>
    request<Incident>(`/admin/incidents/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  backtestOptimize: (strategyId: string, symbol: string, baseTimeframe: string, candles: OHLCVBar[], paramGrid: Record<string, (number | string)[]>, metric = "net_pnl", split = 0.7) =>
    request<OptimizeResult>("/backtest/optimize", { method: "POST", body: JSON.stringify({ strategy_id: strategyId, symbol, base_timeframe: baseTimeframe, candles, param_grid: paramGrid, metric, split }) }),
  // ---- Phase L: AI layer
  aiProvider: () => request<AiProviderConfig>("/ai/provider"),
  aiSaveProvider: (body: { provider: string; model?: string | null; api_key?: string | null; enabled?: boolean }) =>
    request<AiProviderConfig>("/ai/provider", { method: "PUT", body: JSON.stringify(body) }),
  aiDeleteProvider: () => request<void>("/ai/provider", { method: "DELETE" }),
  aiGenerate: (prompt: string, opts: { language?: string; regime?: string | null; symbol?: string | null } = {}) =>
    request<AiStrategyDraft>("/ai/drafts", { method: "POST", body: JSON.stringify({ prompt, ...opts }) }),
  aiContext: (language?: string, regime?: string | null, symbol?: string | null) => {
    const params = new URLSearchParams();
    if (language) params.set("language", language);
    if (regime) params.set("regime", regime);
    if (symbol) params.set("symbol", symbol);
    const qs = params.toString();
    return request<{ prompt_version: string; context: Record<string, unknown> }>(`/ai/context${qs ? `?${qs}` : ""}`);
  },
  aiDrafts: () => request<AiStrategyDraft[]>("/ai/drafts"),
  aiDraft: (id: number) => request<AiStrategyDraft>(`/ai/drafts/${id}`),
  aiBacktestDraft: (id: number, symbol: string, base_timeframe: string, candles: OHLCVBar[], data_source = "sample") =>
    request<{ draft: AiStrategyDraft; run: BacktestRunSummary; result: BacktestResult }>(`/ai/drafts/${id}/backtest`, {
      method: "POST", body: JSON.stringify({ symbol, base_timeframe, candles, data_source }),
    }),
  aiApproveDraft: (id: number, name?: string, acceptRisk = false) =>
    request<{ draft: AiStrategyDraft; custom_strategy_id: number; strategy_id: string; origin: string }>(`/ai/drafts/${id}/approve`, { method: "POST", body: JSON.stringify({ name, accept_risk: acceptRisk }) }),
  aiRejectDraft: (id: number, note?: string) => request<AiStrategyDraft>(`/ai/drafts/${id}/reject`, { method: "POST", body: JSON.stringify({ note }) }),
  // Phase AP: the strategy interview.
  aiInterviewStart: (prompt: string) =>
    request<InterviewStart>("/ai/interview/start", { method: "POST", body: JSON.stringify({ prompt }) }),
  aiInterviewPlan: (answers: Record<string, string | number>, baseTimeframe: string, candles: OHLCVBar[], dataSource: string) =>
    request<InterviewPlan>("/ai/interview/plan", { method: "POST", body: JSON.stringify({ answers, base_timeframe: baseTimeframe, candles, data_source: dataSource }) }),
  aiInterviewRefine: (answers: Record<string, string | number>, baseTimeframe: string, candles: OHLCVBar[], dataSource: string,
                      feedback: string[], optionId: string, strategyId: string | null) =>
    request<InterviewPlan>("/ai/interview/refine", { method: "POST", body: JSON.stringify({
      answers, base_timeframe: baseTimeframe, candles, data_source: dataSource, feedback, option_id: optionId, strategy_id: strategyId }) }),
  aiInterviewChoose: (answers: Record<string, string | number>, optionId: string, strategyId: string | null, match: number) =>
    request<{ preferences: unknown }>("/ai/interview/choose", { method: "POST", body: JSON.stringify({ answers, option_id: optionId, strategy_id: strategyId, match }) }),
  aiProfileDelete: () => request<void>("/ai/profile", { method: "DELETE" }),
  aiMarketMemory: () => request<MarketMemory>("/ai/market-memory"),
  aiMarketMemoryRefresh: (symbols?: string[]) =>
    request<MarketMemory>("/ai/market-memory/refresh", { method: "POST", body: JSON.stringify({ symbols: symbols ?? null }) }),
  // Phase AW: the strategist.
  aiStrategistStudy: (body: { symbol: string; candles?: OHLCVBar[]; broker?: string; language: "en" | "mr" }) =>
    request<MarketStudy>("/ai/strategist/study", { method: "POST", body: JSON.stringify(body) }),
  aiStrategistBuild: (body: { symbol: string; candles?: OHLCVBar[]; broker?: string; style: "intraday" | "scalping"; direction: "auto" | "long" | "short" | "both"; language: "en" | "mr" }) =>
    request<StrategistResult>("/ai/strategist/build", { method: "POST", body: JSON.stringify(body) }),
  aiStrategistAdopt: (name: string, config: CustomStrategyConfig, symbol: string) =>
    request<{ strategy_id: string; name: string; deployment: DeploymentCreateRequest }>("/ai/strategist/adopt", { method: "POST", body: JSON.stringify({ name, config, symbol }) }),
  // Phase AV: the Copilot home.
  aiBrief: (language: "en" | "mr") => request<DailyBrief>(`/ai/brief?language=${language}`),
  aiCoach: (language: "en" | "mr", days = 30, mode: "ALL" | "PAPER" | "LIVE" = "ALL") =>
    request<CoachReview>(`/ai/coach?language=${language}&days=${days}&mode=${mode}`),
  aiCopilot: (message: string, language?: "en" | "mr") =>
    request<CopilotReply>("/ai/copilot", { method: "POST", body: JSON.stringify({ message, language: language ?? null }) }),
  aiAsk: (question: string, language?: "en" | "mr") =>
    request<GuideAnswer>("/ai/ask", { method: "POST", body: JSON.stringify({ question, language: language ?? null }) }),
  aiConcepts: (language: "en" | "mr") => request<{ concepts: GuideConcept[] }>(`/ai/concepts?language=${language}`),
  aiConcept: (id: string, language: "en" | "mr") => request<GuideConcept>(`/ai/concepts/${encodeURIComponent(id)}?language=${language}`),
  aiRegime: (candles: OHLCVBar[]) => request<Regime>("/ai/regime", { method: "POST", body: JSON.stringify({ candles }) }),
  aiActions: (status?: string) => request<AiAction[]>(`/ai/actions${status ? `?status=${status}` : ""}`),
  aiApproveAction: (id: number, note?: string) => request<AiAction>(`/ai/actions/${id}/approve`, { method: "POST", body: JSON.stringify({ note }) }),
  aiRejectAction: (id: number, note?: string) => request<AiAction>(`/ai/actions/${id}/reject`, { method: "POST", body: JSON.stringify({ note }) }),
  // ---- Phase K: billing, API keys, marketplace
  billingPlans: () => request<PlanCatalogueEntry[]>("/billing/plans"),
  billingOverview: () => request<BillingOverview>("/billing"),
  billingSubscribe: (plan_id: string, billing_cycle: "MONTHLY" | "YEARLY") =>
    request<Subscription>("/billing/subscribe", { method: "POST", body: JSON.stringify({ plan_id, billing_cycle }) }),
  billingCancel: (immediately: boolean) =>
    request<Subscription>("/billing/cancel", { method: "POST", body: JSON.stringify({ immediately }) }),
  billingTransactions: () => request<BillingTransaction[]>("/billing/transactions"),
  billingUsage: (days = 30) => request<Record<string, number>>(`/billing/usage?days=${days}`),
  adminRecordPayment: (tenantId: number, amount: number, reference?: string) =>
    request<Subscription>(`/admin/billing/${tenantId}/payment`, { method: "POST", body: JSON.stringify({ amount, reference }) }),
  listApiKeys: () => request<ApiKey[]>("/api-keys"),
  apiKeyScopes: () => request<Record<string, string>>("/api-keys/scopes"),
  createApiKey: (body: { name: string; scopes: string[]; rate_limit_per_minute: number; expires_in_days?: number | null }) =>
    request<ApiKey>("/api-keys", { method: "POST", body: JSON.stringify(body) }),
  revokeApiKey: (id: number) => request<void>(`/api-keys/${id}`, { method: "DELETE" }),
  marketplace: () => request<MarketplaceListing[]>("/marketplace"),
  marketplaceListing: (id: number) => request<MarketplaceListing>(`/marketplace/${id}`),
  marketplaceMine: () => request<MarketplaceListing[]>("/marketplace/listings/mine"),
  marketplaceSubscriptions: () => request<MarketplaceSubscription[]>("/marketplace/subscriptions"),
  marketplaceCreate: (body: { custom_strategy_id: number; title: string; description: string; methodology?: string | null; backtest_run_id?: number | null; version_number?: number | null; price?: number }) =>
    request<MarketplaceListing>("/marketplace/listings", { method: "POST", body: JSON.stringify(body) }),
  // Phase X: revenue share.
  marketplaceSetPrice: (id: number, price: number) => request<MarketplaceListing>(`/marketplace/listings/${id}/price`, { method: "PUT", body: JSON.stringify({ price }) }),
  marketplaceTerms: () => request<MarketplaceTerms>("/marketplace/terms"),
  marketplacePurchases: () => request<MarketplaceCharge[]>("/marketplace/purchases"),
  marketplaceEarnings: () => request<MarketplaceEarnings>("/marketplace/earnings"),
  marketplaceRequestPayout: (destination: string) => request<MarketplacePayout>("/marketplace/payouts", { method: "POST", body: JSON.stringify({ destination }) }),
  adminMarketplaceCharges: (status?: string) => request<MarketplaceCharge[]>(`/admin/marketplace/charges${status ? `?status=${status}` : ""}`),
  adminMarketplaceChargePaid: (id: number, reference: string) => request<MarketplaceCharge>(`/admin/marketplace/charges/${id}/paid`, { method: "POST", body: JSON.stringify({ reference }) }),
  adminMarketplaceChargeVoid: (id: number, note: string) => request<MarketplaceCharge>(`/admin/marketplace/charges/${id}/void`, { method: "POST", body: JSON.stringify({ note }) }),
  adminMarketplacePayouts: (status?: string) => request<MarketplacePayout[]>(`/admin/marketplace/payouts${status ? `?status=${status}` : ""}`),
  adminMarketplacePayoutDestination: (id: number) => request<{ id: number; destination: string }>(`/admin/marketplace/payouts/${id}/destination`),
  adminMarketplacePayoutSettle: (id: number, paid: boolean, reference?: string, note?: string) =>
    request<MarketplacePayout>(`/admin/marketplace/payouts/${id}/${paid ? "paid" : "reject"}`, { method: "POST", body: JSON.stringify({ reference, note }) }),
  adminMarketplaceRevenue: () => request<MarketplaceRevenue>("/admin/marketplace/revenue"),
  adminMarketplaceTerms: () => request<MarketplaceTerms>("/admin/controls/marketplace-terms"),
  adminSetMarketplaceTerms: (values: Record<string, number>) => request<MarketplaceTerms>("/admin/controls/marketplace-terms", { method: "PUT", body: JSON.stringify({ values }) }),
  marketplaceSubmit: (id: number) => request<MarketplaceListing>(`/marketplace/listings/${id}/submit`, { method: "POST" }),
  marketplaceUnlist: (id: number) => request<MarketplaceListing>(`/marketplace/listings/${id}/unlist`, { method: "POST" }),
  marketplaceSubscribe: (id: number) => request<MarketplacePurchaseResponse>(`/marketplace/${id}/subscribe`, { method: "POST" }),
  marketplaceUnsubscribe: (id: number) => request<void>(`/marketplace/${id}/unsubscribe`, { method: "POST" }),
  adminMarketplacePending: () => request<MarketplaceListing[]>("/admin/marketplace/pending"),
  adminMarketplaceReview: (id: number, publish: boolean, note?: string) =>
    request<MarketplaceListing>(`/admin/marketplace/${id}/${publish ? "publish" : "reject"}`, { method: "POST", body: JSON.stringify({ note }) }),
  listAlertChannels: () => request<AlertChannel[]>("/alert-channels"),

  upsertAlertChannel: (type: string, body: AlertChannelUpsert) =>
    request<AlertChannel>(`/alert-channels/${type}`, { method: "PUT", body: JSON.stringify(body) }),

  deleteAlertChannel: (type: string) => request<void>(`/alert-channels/${type}`, { method: "DELETE" }),

  pushPublicKey: () => request<{ public_key: string; configured: boolean }>("/alert-channels/push/public-key"),

  testAlertChannel: (type: string) =>
    request<{ ok: boolean; detail: string }>(`/alert-channels/${type}/test`, { method: "POST" }),

  listAlertDeliveries: (limit = 20) => request<AlertDelivery[]>(`/alert-channels/deliveries?limit=${limit}`),

  // Phase BE: Telegram inbound (commands + PAPER approval buttons) - owner-only settings.
  telegramInboundStatus: () => request<TelegramInboundStatus>("/telegram/inbound/status"),
  telegramInboundConfigure: (body: { enabled: boolean; allowed_chat_ids: string[] }) =>
    request<TelegramInboundStatus>("/telegram/inbound", { method: "PUT", body: JSON.stringify(body) }),
  telegramInboundRegister: () =>
    request<{ ok: boolean; description?: string | null; webhook_url: string }>("/telegram/inbound/register", { method: "POST" }),

  // --- Team ---

  getTenant: () => request<TenantInfo>("/team/tenant"),

  renameTenant: (name: string) => request<TenantInfo>("/team/tenant", { method: "PATCH", body: JSON.stringify({ name }) }),

  setTenantMfaPolicy: (requireMfaForLive: boolean) =>
    request<TenantInfo>("/team/tenant", { method: "PATCH", body: JSON.stringify({ require_mfa_for_live: requireMfaForLive }) }),

  setTenantBaseCurrency: (code: string) =>
    request<TenantInfo>("/team/tenant", { method: "PATCH", body: JSON.stringify({ base_currency: code }) }),
  setTenantRoutingPolicy: (policy: string) =>
    request<TenantInfo>("/team/tenant", { method: "PATCH", body: JSON.stringify({ default_routing_policy: policy }) }),
  taxYears: () => request<{ years: string[]; current: string }>("/tax/years"),
  taxReport: (fy: string, mode: string) => request<TaxReport>(`/tax/report?fy=${encodeURIComponent(fy)}&mode=${mode}`),
  taxReportCsvUrl: (fy: string, mode: string) => `${BASE}/tax/report.csv?fy=${encodeURIComponent(fy)}&mode=${mode}`,
  fxRates: () => request<{ rates: FxRate[]; supported: string[] }>("/fx/rates"),
  adminSetFxRate: (base: string, quote: string, rate: number, source = "manual") =>
    request<FxRate>("/admin/fx-rates", { method: "PUT", body: JSON.stringify({ base, quote, rate, source }) }),

  setTenantAlgoId: (algoId: string) =>
    request<TenantInfo>("/team/tenant", { method: "PATCH", body: JSON.stringify({ algo_id: algoId }) }),

  listMembers: () => request<TeamMember[]>("/team/members"),

  changeMemberRole: (id: number, role: string) =>
    request<TeamMember>(`/team/members/${id}`, { method: "PATCH", body: JSON.stringify({ role }) }),

  removeMember: (id: number) => request<void>(`/team/members/${id}`, { method: "DELETE" }),

  reactivateMember: (id: number) => request<TeamMember>(`/team/members/${id}/reactivate`, { method: "POST" }),

  eraseMember: (id: number, reason: string) =>
    request<void>(`/team/members/${id}/erase`, { method: "POST", body: JSON.stringify({ reason }) }),

  listInvites: () => request<TeamInvite[]>("/team/invites"),

  createInvite: (email: string, role: string) =>
    request<TeamInvite>("/team/invites", { method: "POST", body: JSON.stringify({ email, role }) }),

  revokeInvite: (id: number) => request<void>(`/team/invites/${id}`, { method: "DELETE" }),

  inviteInfo: (token: string) => request<InviteInfo>(`/auth/invite/${encodeURIComponent(token)}`),

  acceptInvite: (token: string, password: string) =>
    request<TokenResponse>(`/auth/invite/${encodeURIComponent(token)}/accept`, { method: "POST", body: JSON.stringify({ password }) }),

  // --- Platform admin ---

  adminOverview: () => request<AdminOverview>("/admin/overview"),

  adminPlans: () => request<AdminPlan[]>("/admin/plans"),

  adminTenants: (q = "") => request<AdminTenantSummary[]>(`/admin/tenants${q ? `?q=${encodeURIComponent(q)}` : ""}`),

  adminTenant: (id: number) => request<AdminTenantDetail>(`/admin/tenants/${id}`),

  adminUpdateTenant: (id: number, body: { plan?: string; status?: string; reason?: string }) =>
    request<AdminTenantSummary>(`/admin/tenants/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  adminAuditLogs: (tenantId?: number) =>
    request<PlatformAuditLog[]>(`/admin/audit-logs${tenantId ? `?tenant_id=${tenantId}` : ""}`),

  engageGlobalKillSwitch: (reason: string) =>
    request<unknown>("/kill-switch/global/engage", { method: "POST", body: JSON.stringify({ reason }) }),

  disengageGlobalKillSwitch: () => request<unknown>("/kill-switch/global/disengage", { method: "POST" }),
};
