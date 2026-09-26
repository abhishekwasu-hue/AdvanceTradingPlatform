import type {
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
  ReconciliationReport,
  ReconciliationStatus,
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

  setTenantMfaPolicy: (requireMfaForLive: boolean) =>
    request<TenantInfo>("/team/tenant", { method: "PATCH", body: JSON.stringify({ require_mfa_for_live: requireMfaForLive }) }),

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
