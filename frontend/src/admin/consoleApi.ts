import { request } from "../api/client";
import type { ServerView } from "../api/viewAs";
import { formatDateIST } from "../ui/format";

/**
 * MO3 addition (a): the admin console's reads. The new sections come from `/api/admin/console/*` (read-only, SUPER_ADMIN
 * with two-factor on the server); Testing tools and Support reuse the admin endpoints that already exist.
 */

export type AdminSection = "system" | "controls" | "users" | "testing" | "support" | "compliance";

export const ADMIN_SECTIONS: { id: AdminSection; label: string; hint: string }[] = [
  { id: "system", label: "System", hint: "Alerts, health, workers, the data feed and the platform go-live checklist" },
  { id: "controls", label: "Controls", hint: "Feature flags, maintenance, kill switches and limits" },
  { id: "users", label: "Users & business", hint: "People, organisations, plans and AI cost" },
  { id: "testing", label: "Testing tools", hint: "Read-only state of the operator tools" },
  { id: "support", label: "Support", hint: "Tickets, support contact and one person's status" },
  { id: "compliance", label: "Compliance", hint: "Algo trading readiness per broker, the go-live checklist and the audit-trail exports" },
];

/**
 * ADM-1 (owner 30 Oct: "too many controls, very confusing"): the Controls section in four groups, one on screen at a time
 * (`/admin?section=controls&group=flags`). Safety first: the PAPER lock, the kill switch, maintenance and brokers.
 */
export type ControlsGroup = "safety" | "limits" | "features" | "flags";
export const CONTROLS_GROUPS: { id: ControlsGroup; label: string; hint: string }[] = [
  { id: "safety", label: "Safety", hint: "The PAPER lock, the emergency stop, maintenance and broker switches" },
  { id: "limits", label: "Limits", hint: "The ceilings every organisation's risk settings stay under" },
  { id: "features", label: "Features", hint: "Everything a trader uses, in one switch" },
  { id: "flags", label: "All flags", hint: "Every switch one by one, in categories" },
];

export function isControlsGroup(value: string | null): value is ControlsGroup {
  return CONTROLS_GROUPS.some((g) => g.id === value);
}

export function isAdminSection(value: string | null): value is AdminSection {
  return ADMIN_SECTIONS.some((s) => s.id === value);
}

/** `abhi.wasu@example.com` -> `ab*******@example.com` (the server masks its own lists the same way). */
export function maskEmail(email: string | null | undefined): string {
  if (!email) return "-";
  const at = email.indexOf("@");
  if (at < 0) return `${email.slice(0, 2)}***`;
  const local = email.slice(0, at);
  return `${local.slice(0, 2)}${"*".repeat(Math.max(1, local.length - 2))}${email.slice(at)}`;
}

/** An organisation is named after its owner's e-mail at sign-up: such a name is masked like the address. */
export function orgName(name: string | null | undefined): string {
  if (!name) return "-";
  return name.includes("@") ? maskEmail(name) : name;
}

export interface HealthCheck { status: string; [key: string]: unknown }
export interface ConsoleWorker {
  name: string; state: "ok" | "stale" | "never_seen"; seconds_since_heartbeat: number | null; last_seen_at: string | null;
  started_at: string | null; cycles: number; last_cycle_ms: number | null; last_error: string | null;
}
export interface ConsoleSystem {
  checked_at: string;
  health: { status: string; checked_at: string; environment: string; checks: Record<string, HealthCheck> };
  workers: ConsoleWorker[];
  data_feed: {
    market_open: boolean; market_reason: string; next_open: string | null;
    market_memory: { last_captured_at: string | null; minutes_since: number | null; snapshots_last_hour: number };
    instrument_master: { instruments: number; last_synced_at: string | null };
    data_quality_24h: Record<string, number>;
    broker_circuits: { broker: string; state: string; failures_in_window: number; calls_in_window: number }[];
  };
}

export interface ConsoleUser {
  id: number; email: string; role: string; active: boolean; mfa: boolean; email_verified: boolean; tenant_id: number; tenant: string;
  plan: string; tenant_status: string; signed_up_at: string | null; last_seen_at: string | null;
}
export interface ConsoleUsers { totals: { users: number; active_7d: number; signups_7d: number }; users: ConsoleUser[] }

export interface AiCostNumbers {
  calls: number; input_tokens: number; output_tokens: number; cost_usd: number; cost_inr: number; own_key_cost_usd: number; failed: number;
}
export interface AiCostTenant extends AiCostNumbers { tenant_id: number; tenant: string; plan: string }
export interface AiCostTask extends AiCostNumbers {
  task: string; tier: string; provider: string; model: string;
  price: { input_per_m_usd: number; output_per_m_usd: number; estimated: boolean } | null;
}
export interface ConsoleAiCost {
  days: number; since: string; usd_inr: number; total: { calls: number; cost_usd: number; cost_inr: number };
  tenants: AiCostTenant[]; tasks: AiCostTask[];
}

export interface SupportUser {
  user: { id: number; email: string; role: string; active: boolean; mfa: boolean; email_verified: boolean; trading_paused: boolean;
    signed_up_at: string | null; last_seen_at: string | null };
  tenant: { id: number; name: string; plan: string; status: string } | null;
  brokers: { broker: string; account: string; status: string; expires_at: string | null; last_verified_at: string | null }[];
  sign_ins: { at: string | null; success: boolean; reason: string | null; network: string | null; device: string | null }[];
  bots_with_errors: { id: number; strategy: string; symbol: string; mode: string; status: string; error: string | null; failures_in_a_row: number }[];
  ai_failures_7d: { at: string | null; task: string; provider: string; status: string | null }[];
  alert_deliveries_failed: number;
}

export interface ConsoleDiagnostics {
  checked_at: string;
  server_errors: { request_id: string; method: string; route: string; status: number; duration_ms: number; at: string }[];
  worker_errors: ConsoleWorker[];
  bots_with_errors: { id: number; tenant_id: number; strategy: string; symbol: string; mode: string; status: string; error: string | null;
    failures_in_a_row: number; last_evaluated_at: string | null }[];
  ai_failures: { at: string | null; tenant_id: number; task: string; provider: string; status: string | null }[];
  alert_deliveries_failed: Record<string, number>;
  data_quality: { at: string | null; kind: string; instrument: string; timeframe: string | null; source: string }[];
  failed_sign_ins_24h: number;
}

export interface FeedbackItem {
  id: number; tenant_id: number; user_id: number | null; email: string | null; message: string; app_version: string; screen: string;
  user_agent: string | null; created_at: string | null;
}
export interface OutboxState { by_status: Record<string, number>; claimed: number; oldest_pending_seconds: number | null }
export interface RetentionState { policy: Record<string, unknown>; eligible_now: Record<string, number>; last_run: { at: string; detail: string } | null }
export interface DeadLetter { id: number; tenant_id: number; channel: string; event_type: string; status: string; error_class: string | null; attempts: number; created_at: string | null }

/** TM 9.4: the owner's chain history recorder (GET /api/admin/chain-history/status) - counts and times only. */
export interface ChainHistoryRow {
  underlying: string; exchange: string; trading_day: boolean; chains_today: number; slots_today: number; slots_expected: number;
  missing_slots: number; gaps: { from: string; to: string }[]; stored_bytes_today: number; raw_bytes_today: number; last_captured_at: string | null;
}
export interface ChainHistoryStatus {
  flag_on: boolean; owner_configured: boolean; owner_session: { broker: string } | null; slot_minutes: number;
  expiries_per_underlying: number; max_reads_per_slot: number; stocks_enabled: boolean; underlyings: ChainHistoryRow[];
  totals: { chains: number; days: number; first_day: string | null; last_captured_at: string | null; stored_bytes: number } | null;
  problem: string | null; checked_at: string;
}

export const consoleApi = {
  system: () => request<ConsoleSystem>("/admin/console/system"),
  users: (q = "") => request<ConsoleUsers>(`/admin/console/users${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  aiCost: (days = 30) => request<ConsoleAiCost>(`/admin/console/ai-cost?days=${days}`),
  supportUser: (id: number) => request<SupportUser>(`/admin/console/support/users/${id}`),
  diagnostics: () => request<ConsoleDiagnostics>("/admin/console/diagnostics"),
  // MO3-A1: the read-only "View as user" sessions (start / end live in api/viewAs.ts).
  views: () => request<{ views: (ServerView & { admin_user_id: number | null })[] }>("/admin/view-sessions?limit=20"),
  // Existing admin reads, reused as they are.
  feedback: () => request<FeedbackItem[]>("/admin/beta/feedback?limit=50"),
  outbox: () => request<OutboxState>("/admin/alerts/outbox"),
  deadLetters: () => request<DeadLetter[]>("/admin/alerts/dead-letters?limit=20"),
  retention: () => request<RetentionState>("/admin/retention"),
  screenerAudit: () => request<Record<string, unknown>>("/admin/screener/audit"),
  historyImport: () => request<Record<string, unknown>>("/admin/history-import/progress?limit=1"),
  historyBackfill: () => request<Record<string, unknown>>("/admin/history-backfill/progress?limit=1"),
  historyIntraday: () => request<Record<string, unknown>>("/admin/history-intraday/progress?limit=1"),
  chainHistory: () => request<ChainHistoryStatus>("/admin/chain-history/status"),
};

/** "3 min ago" / "2 h ago" / "5 d ago" from an ISO time (now passed in for tests). */
export function ago(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  return formatDateIST(iso);                                // REV-1 M3: the IST day
}
