import type { Page, Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";

/**
 * MO3 addition (a): the made-up replies the admin console reads (e2e/admin-console-v2.e2e.ts). Every name, address,
 * number and id is invented (example.com addresses, documentation IP ranges); nothing is a real account, key or token.
 */

export type Theme = "light" | "dark";
export const NOW = "2026-10-22T11:00:00+05:30";
const T = (minutesAgo: number) => new Date(Date.parse(NOW) - minutesAgo * 60_000).toISOString();

export const FEATURES = { ui_v2: true, phase0_paper_only: true, live_trading: false };

const ADMIN_ME = { id: 1, email: "ops@example.com", tenant_id: 1, role: "SUPER_ADMIN", mfa_enabled: true, email_verified: true, scopes: [] };

const FLAG = (plain: string, area: string, area_label: string, on: boolean, status: string, status_label: string) =>
  ({ on, tenants: [], description: plain, plain, area, area_label, turns_on: "", depends_on: [], needs: "", status, status_label, default_on: on });

const FLAGS = {
  ui_v2: FLAG("The new screens", "app", "App", true, "ready", "Ready for users"),
  phase0_paper_only: FLAG("Paper only (Phase 0 lock)", "safety", "Safety", true, "owner", "Owner decision"),
  live_trading: FLAG("LIVE trading", "safety", "Safety", false, "owner", "Owner decision"),
  self_signup: FLAG("Anyone can sign up", "accounts", "Accounts", false, "owner", "Owner decision"),
};

// "Enable trader features" (Controls): a short made-up pack; POST enables it, POST .../undo puts it back.
const PACK_ROW = (name: string, plain: string, on: boolean, state: string, check: string) => ({ name, plain, on, state, check });
export const TRADER_PACK_BEFORE = [
  PACK_ROW("ui_v2", "The redesigned app (navigation and pages)", true, "on", "On, nothing to check"),
  PACK_ROW("history_backfill", "Nightly NSE end-of-day download", true, "working", "Daily bars are stored"),
  PACK_ROW("paper_account", "Your own paper account and progress ladder", false, "off", "Off"),
  PACK_ROW("watchlists", "Your own symbol lists", false, "off", "Off"),
  PACK_ROW("screener_v2", "The screener: saved screens and runs on broker data", true, "working", "2 organisations with a broker connection for screen data"),
  PACK_ROW("idea_library", "Idea library: method ideas in plain words, each with a scan to try (education only)", false, "off", "Off"),
  PACK_ROW("ai_copilot", "The AI Copilot page and AI strategy drafts", true, "needs_setup", "No AI key yet: answers come from the built-in rules until a key is connected"),
];
export const TRADER_PACK_AFTER = TRADER_PACK_BEFORE.map((r) => (r.on ? r : r.name === "watchlists"
  ? { ...r, on: true, state: "working", check: "12 ready-made lists with symbols" } : { ...r, on: true, state: "on", check: "On, nothing to check" }));
const pack = (flags: typeof TRADER_PACK_BEFORE, canUndo: boolean, extra: Record<string, unknown> = {}) => ({
  flags, all_on: flags.every((f) => f.on), on_count: flags.filter((f) => f.on).length, total: flags.length, can_undo: canUndo,
  enabled_at: canUndo ? NOW : null, ...extra });

const READINESS = { target: "PLATFORM", ready: false, summary: { ok: 2, todo: 1, warn: 0, info: 0 }, checked_at: T(1), note: "",
  items: [
    { key: "secrets", title: "Secrets set", status: "ok", detail: "All required secrets are set.", fix: "", link: null, scope: "platform" },
    { key: "worker", title: "Trading worker running", status: "ok", detail: "Last heartbeat a few seconds ago.", fix: "", link: null, scope: "platform" },
    { key: "smtp", title: "E-mail sending", status: "todo", detail: "SMTP is not configured.", fix: "Set the SMTP settings in the server environment.", link: null, scope: "platform" },
  ] };

export const SYSTEM = {
  checked_at: T(0),
  health: { status: "ok", checked_at: T(0), environment: "staging", checks: {
    database: { status: "ok", latency_ms: 1.2 }, migrations: { status: "ok" }, worker: { status: "ok" },
    live_paths: { status: "paper_only" }, redis: { status: "disabled" } } },
  workers: [
    { name: "trading_worker", state: "ok", seconds_since_heartbeat: 8, last_seen_at: T(0.15), started_at: T(600), cycles: 4120, last_cycle_ms: 310, last_error: null },
    { name: "news_worker", state: "stale", seconds_since_heartbeat: 3600, last_seen_at: T(60), started_at: T(900), cycles: 88, last_cycle_ms: 1200, last_error: "Feed answered HTTP 503" },
  ],
  data_feed: {
    market_open: true, market_reason: "Regular session", next_open: null,
    market_memory: { last_captured_at: T(4), minutes_since: 4, snapshots_last_hour: 36 },
    instrument_master: { instruments: 84211, last_synced_at: T(300) },
    data_quality_24h: { gap: 2, late: 1 },
    broker_circuits: [{ broker: "upstox", state: "CLOSED", failures_in_window: 0, calls_in_window: 52 }],
  },
};

// A2: Admin > System > Alerts (the server checks and the operator channels).
export const OPS = {
  checked_at: T(2), source: "server",
  checks: [
    { key: "error_rate", label: "API error rate", state: "ok", detail: "0.1% of 1,240 requests since the last check failed with a server error.", source: "app", since: T(600), alerted: null },
    { key: "worker", label: "Trading worker", state: "ok", detail: "Checking in every cycle.", source: "app", since: T(600), alerted: null },
    { key: "data_feed", label: "Market data feed", state: "ok", detail: "Live decisions are getting fresh data. End-of-day data is up to date (2026-10-21).", source: "app", since: T(600), alerted: null },
    { key: "broker_tokens", label: "Broker logins", state: "warn", detail: "14 broker connections: 11 working, 3 need a new login; 1 long-lived token expires within 7 days.", source: "app", since: T(240), alerted: "warn" },
    { key: "backup", label: "Backups", state: "fail", detail: "Off-site copy 31 h old.", source: "server", since: T(90), alerted: "fail" },
    { key: "disk", label: "Disk space", state: "ok", detail: "41% used.", source: "server", since: T(600), alerted: null },
    { key: "db_size", label: "Database size", state: "ok", detail: "1.4 GB.", source: "app", since: T(600), alerted: null },
    { key: "certificate", label: "HTTPS certificate", state: "ok", detail: "Valid for 62 more days.", source: "server", since: T(600), alerted: null },
    { key: "alert_outbox", label: "Alert delivery", state: "ok", detail: "2 waiting.", source: "app", since: T(600), alerted: null },
    { key: "sign_in_failures", label: "Failed sign-ins", state: "ok", detail: "3 in 15 minutes.", source: "app", since: T(600), alerted: null },
  ],
  schedule: { server_reported_at: T(2), late: false },
  last_message: { at: T(85), lines: 1, delivered: true },
  channels: { telegram: { available: true, connected: true, from: "linked", pending: false }, email: { available: true, recipients: 1 } },
  error_tracker: { on: false },
};

const USERS = { totals: { users: 4, active_7d: 3, signups_7d: 1 }, users: [
  { id: 14, email: "ne*****@example.com", role: "OWNER", active: true, mfa: false, email_verified: true, tenant_id: 9, tenant: "ne*****@example.com",
    plan: "Free", tenant_status: "active", signed_up_at: T(60 * 30), last_seen_at: T(45) },
  { id: 12, email: "tr****@example.com", role: "USER", active: true, mfa: true, email_verified: true, tenant_id: 7, tenant: "Desk Seven",
    plan: "Pro", tenant_status: "active", signed_up_at: T(60 * 24 * 12), last_seen_at: T(3) },
  { id: 11, email: "ow***@example.com", role: "OWNER", active: true, mfa: true, email_verified: true, tenant_id: 7, tenant: "Desk Seven",
    plan: "Pro", tenant_status: "active", signed_up_at: T(60 * 24 * 40), last_seen_at: T(60 * 5) },
  { id: 1, email: "op*@example.com", role: "SUPER_ADMIN", active: true, mfa: true, email_verified: true, tenant_id: 1, tenant: "Platform",
    plan: "Enterprise", tenant_status: "active", signed_up_at: T(60 * 24 * 90), last_seen_at: T(0) },
] };

const AI_COST = { days: 30, since: T(60 * 24 * 30), usd_inr: 84, total: { calls: 412, cost_usd: 6.1234, cost_inr: 514.37 },
  tenants: [
    { tenant_id: 7, tenant: "Desk Seven", plan: "Pro", calls: 380, input_tokens: 812000, output_tokens: 96000, cost_usd: 5.9, cost_inr: 495.6, own_key_cost_usd: 1.2, failed: 3 },
    { tenant_id: 9, tenant: "ne*****@example.com", plan: "Free", calls: 32, input_tokens: 41000, output_tokens: 5200, cost_usd: 0.2234, cost_inr: 18.77, own_key_cost_usd: 0, failed: 0 },
  ],
  tasks: [
    { task: "copilot", tier: "fast", provider: "anthropic", model: "example-fast-model", price: { input_per_m_usd: 3, output_per_m_usd: 15, estimated: false },
      calls: 300, input_tokens: 700000, output_tokens: 80000, cost_usd: 4.9, cost_inr: 411.6, own_key_cost_usd: 1.2, failed: 2 },
    { task: "classification", tier: "cheap", provider: "anthropic", model: "example-small-model", price: { input_per_m_usd: 1, output_per_m_usd: 5, estimated: true },
      calls: 112, input_tokens: 153000, output_tokens: 21200, cost_usd: 1.2234, cost_inr: 102.77, own_key_cost_usd: 0, failed: 1 },
  ] };

const SUPPORT_USER = {
  user: { id: 12, email: "tr****@example.com", role: "USER", active: true, mfa: true, email_verified: true, trading_paused: false, signed_up_at: T(60 * 24 * 12), last_seen_at: T(3) },
  tenant: { id: 7, name: "Desk Seven", plan: "Pro", status: "active" },
  brokers: [{ broker: "upstox", account: "primary", status: "EXPIRED", expires_at: T(60 * 8), last_verified_at: T(60 * 20) }],
  sign_ins: [
    { at: T(3), success: true, reason: null, network: "203.0.113.x", device: "Chrome on Android" },
    { at: T(20), success: false, reason: "bad_password", network: "198.51.100.x", device: "Safari on iPhone" },
  ],
  bots_with_errors: [{ id: 31, strategy: "orb_15m_5m", symbol: "NIFTY 50", mode: "PAPER", status: "ERROR", error: "The broker session has expired.", failures_in_a_row: 3 }],
  ai_failures_7d: [{ at: T(90), task: "copilot", provider: "anthropic", status: "error: timeout" }],
  alert_deliveries_failed: 1,
};

const DIAGNOSTICS = {
  checked_at: T(0),
  server_errors: [{ request_id: "5f2c9a1e0d7b4c3a", method: "GET", route: "/api/positions", status: 500, duration_ms: 41.2, at: T(12) }],
  worker_errors: [SYSTEM.workers[1]],
  bots_with_errors: [{ id: 31, tenant_id: 7, strategy: "orb_15m_5m", symbol: "NIFTY 50", mode: "PAPER", status: "ERROR", error: "The broker session has expired.", failures_in_a_row: 3, last_evaluated_at: T(5) }],
  ai_failures: [{ at: T(90), tenant_id: 7, task: "copilot", provider: "anthropic", status: "error: timeout" }],
  alert_deliveries_failed: { DEAD: 1 },
  data_quality: [{ at: T(200), kind: "gap", instrument: "NSE_INDEX|Nifty 50", timeframe: "1min", source: "upstox" }],
  failed_sign_ins_24h: 4,
};

const FEEDBACK = [
  { id: 5, tenant_id: 7, user_id: 12, email: "trader@example.com", message: "The option chain took a while to load this morning.", app_version: "1.42.0", screen: "/option-chain", user_agent: null, created_at: T(120) },
  { id: 4, tenant_id: 9, user_id: 14, email: "newbie@example.com", message: "Where do I connect my broker?", app_version: "1.42.0", screen: "/", user_agent: null, created_at: T(60 * 26) },
];

// C12: the same inbox as tickets (Admin › Support), with made-up targets, an assignee and one attached error reference.
export const TICKETS = {
  tickets: [
    { ...FEEDBACK[0], kind: "problem", status: "open", assignee_id: null, assignee_email: null, first_response_at: null, closed_at: null,
      context: { client_time: T(120), error_id: "3f9c2a7e1b", error_screen: "/option-chain", error_at: T(122), error_route: "/api/option-chain/{symbol}", error_status: 503, error_found: true },
      first_response_due_at: T(-120), first_response_late: false, resolve_due_at: T(-60 * 46), resolve_late: false },
    { ...FEEDBACK[1], kind: "feedback", status: "answered", assignee_id: 1, assignee_email: "ops@example.com", first_response_at: T(60 * 25), closed_at: null,
      context: {}, first_response_due_at: T(60 * 22), first_response_late: true, resolve_due_at: T(60 * 26 - 48 * 60), resolve_late: false },
  ],
  counts: { open: 1, answered: 1, closed: 0 }, late: 0, assignees: [{ id: 1, email: "ops@example.com" }],
  targets: { first_response_hours: 4, resolve_hours: 48 },
};
export const DESK_SETTINGS = { whatsapp: "", email: "", first_response_hours: 4, resolve_hours: 48 };

const TENANTS = [
  { id: 9, name: "newbie@example.com", plan: "free", status: "active", created_at: T(60 * 30), owners: ["newbie@example.com"], members: 1, active_deployments: 0, live_deployments: 0, open_positions: 0 },
  { id: 7, name: "Desk Seven", plan: "pro", status: "active", created_at: T(60 * 24 * 40), owners: ["owner@example.com"], members: 2, active_deployments: 2, live_deployments: 0, open_positions: 1 },
];

// Readiness C13: Admin > Users & business > Learn videos and Invite a friend (one link set, the wording empty).
const LEARN_LESSONS = (JSON.parse(readFileSync(new URL("./fixtures/learn_v2.json", import.meta.url), "utf8")) as { lessons: { id: string; title: string }[] })
  .lessons.map(({ id, title }) => ({ id, title }));
export const LEARN_SETTINGS = { videos: { "atp-how": "https://videos.example.com/how-atp-works" }, course_url: "", lessons: LEARN_LESSONS };
export const REFERRAL_SETTINGS = { reward_text: "", totals: { joined: 3, referrers: 2, links_created: 5 } };

// TM 9.4: the owner's chain history recorder (Admin › System) - made-up counts, two indices recording, one with gaps.
const chainRow = (underlying: string, exchange: string, chains: number, slots: number, missing: number, gaps: { from: string; to: string }[]) => ({
  underlying, exchange, trading_day: true, chains_today: chains, slots_today: slots, slots_expected: slots + missing, missing_slots: missing, gaps,
  stored_bytes_today: chains * 21_000, raw_bytes_today: chains * 88_000, last_captured_at: T(0.4),
});
export const CHAIN_HISTORY = {
  flag_on: true, owner_configured: true, owner_session: { broker: "upstox" }, slot_minutes: 1, expiries_per_underlying: 2, max_reads_per_slot: 12,
  stocks_enabled: false, checked_at: T(0), problem: null,
  underlyings: [
    chainRow("NIFTY", "NSE", 210, 105, 0, []),
    chainRow("BANKNIFTY", "NSE", 206, 103, 2, [{ from: "2026-10-22T09:41:00+05:30", to: "2026-10-22T09:43:00+05:30" }]),
    chainRow("SENSEX", "BSE", 196, 98, 7, [{ from: "2026-10-22T09:15:00+05:30", to: "2026-10-22T09:20:00+05:30" }, { from: "2026-10-22T10:12:00+05:30", to: "2026-10-22T10:14:00+05:30" }]),
  ],
  totals: { chains: 9_840, days: 12, first_day: "2026-10-07", last_captured_at: T(0.4), stored_bytes: 206_000_000 },
};

export interface AdminOptions {
  theme?: Theme; role?: string; features?: Record<string, boolean>;
  /** More replies, registered after the ones above (so they win) and before the page opens (MO4 slice 3). */
  routes?: (page: Page) => Promise<void>;
}

/** Opens `path` with the admin console's replies; returns the API paths the page asked for. */
export async function openAdmin(page: Page, path: string, o: AdminOptions = {}) {
  await page.clock.install({ time: new Date(NOW) });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
    } catch { /* storage unavailable */ }
    try { Object.defineProperty(Notification, "permission", { get: () => "denied" }); } catch { /* no Notification API */ }
  }, o.theme ?? "light");
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: o.features ?? FEATURES, brokers: [], connections: [] });
  const asked: string[] = [];
  const ok = (json: unknown) => (r: Route) => { asked.push(new URL(r.request().url()).pathname); return r.fulfill({ json }); };
  const A = (p: string) => new RegExp(`/api(?:/v1)?${p}`);
  await page.route(A("/auth/me$"), (r) => r.fulfill({ json: o.role ? { ...ADMIN_ME, id: 3, email: "member@example.com", tenant_id: 2, role: o.role } : ADMIN_ME }));
  await page.route(A("/admin/overview$"), ok({ tenants_total: 3, tenants_by_status: { active: 3 }, tenants_by_plan: { free: 1, pro: 1, enterprise: 1 }, users_total: 4,
    active_deployments: 2, live_deployments: 0, open_positions: 1, open_live_positions: 0, global_kill_switch_engaged: false, global_kill_switch_reason: "",
    worker_running: true, worker_last_seen_at: T(0.15), worker_last_error: null }));
  await page.route(A("/admin/controls$"), ok({ maintenance_mode: false, maintenance_message: null, disabled_brokers: [] }));
  await page.route(A("/admin/controls/flags$"), ok(FLAGS));
  let packOn = false;
  await page.route(A("/admin/controls/trader-pack(/undo)?$"), (r) => {
    const path = new URL(r.request().url()).pathname;
    asked.push(path);
    if (r.request().method() === "POST") {
      const undo = path.endsWith("/undo");
      const offBefore = TRADER_PACK_BEFORE.filter((f) => !f.on).map((f) => f.name);
      const changed = !undo && !packOn ? offBefore : [];
      const restored = undo && packOn ? offBefore : [];
      packOn = !undo;
      return r.fulfill({ json: pack(packOn ? TRADER_PACK_AFTER : TRADER_PACK_BEFORE, packOn, undo ? { restored } : { changed }) });
    }
    return r.fulfill({ json: pack(packOn ? TRADER_PACK_AFTER : TRADER_PACK_BEFORE, packOn) });
  });
  await page.route(A("/admin/controls/risk-ceilings$"), ok({ risk_per_trade_pct: 2, max_daily_loss_pct: 5, max_portfolio_risk_pct: 10, dd_level_2_pct: 25, min_stop_cooldown_minutes: 0 }));
  // M72 PR-1b: the platform AI key's limits (Controls › Limits) - every number Not set, a key on the server.
  await page.route(A("/admin/controls/ai-platform$"), ok({ plan_budget_inr: { free: null, pro: null, business: null }, user_daily_cap_inr: null,
    key_configured: true, provider: "anthropic", plans: [{ id: "free", name: "Free" }, { id: "pro", name: "Pro" }, { id: "business", name: "Business" }] }));
  await page.route(A("/admin/incidents(\\?.*)?$"), ok([]));
  await page.route(A("/admin/beta/cohort$"), ok({ gate_on: false, cap: 50, used: 3, remaining: 47, stage: 50, stages: [10, 50, 200], next_stage: 200, env_cap: 500, capped_by_env: false, headroom: 47 }));
  await page.route(A("/admin/plans$"), ok([{ id: "free", name: "Free", description: "", limits: {} }, { id: "pro", name: "Pro", description: "", limits: {} }]));
  await page.route(A("/admin/tenants(\\?.*)?$"), ok(TENANTS));
  await page.route(A("/admin/audit-logs(\\?.*)?$"), ok([{ id: 90, tenant_id: 7, user_id: 11, user_email: "owner@example.com", event: "login", detail: "Signed in", created_at: T(30) }]));
  await page.route(A("/admin/readiness$"), ok(READINESS));
  await page.route(A("/system/encryption$"), ok({ tenant_keys: 3, secrets_total: 5, secrets_legacy: 0, keys_loaded: 3 }));
  await page.route(A("/market-holidays(\\?.*)?$"), ok([{ id: 1, exchange: "NSE", holiday_date: "2026-11-09", description: "Diwali Balipratipada" }]));
  await page.route(A("/admin/system-watchlists$"), ok({ last_refresh: null, history: [], lists: 0, groups: { INDICES: 0, INDEX_STOCKS: 0, SECTORS: 0, FNO: 0 }, universe_sync_enabled: false, refresh_hour_ist: 18 }));
  await page.route(A("/admin/console/system$"), ok(SYSTEM));
  await page.route(A("/admin/ops-alerts$"), ok(OPS));
  await page.route(A("/admin/console/users(\\?.*)?$"), ok(USERS));
  await page.route(A("/admin/console/ai-cost(\\?.*)?$"), ok(AI_COST));
  await page.route(A("/admin/console/support/users/12$"), ok(SUPPORT_USER));
  await page.route(A("/admin/console/support/users/404$"), (r) => r.fulfill({ status: 404, json: { detail: "Unknown user" } }));
  await page.route(A("/admin/console/diagnostics$"), ok(DIAGNOSTICS));
  // MO3-A1: the recent read-only views as a user (Support).
  await page.route(A("/admin/view-sessions(\\?.*)?$"), ok({ views: [
    { id: 40, user_id: 14, tenant_id: 9, name: "ne*****@example.com", reason: "Could not find the Broker page", started_at: T(60 * 26), expires_at: T(60 * 26 - 30),
      ended_at: T(60 * 26 - 12), end_reason: "ended by support", active: false, seconds_left: 0, requests: 23, refused: 1, last_seen_at: T(60 * 26 - 12), admin_user_id: 1 },
  ] }));
  await page.route(A("/admin/beta/feedback(\\?.*)?$"), ok(FEEDBACK));
  await page.route(A("/admin/support/tickets(\\?.*)?$"), (r) => {
    if (r.request().method() !== "GET") return r.fallback();
    const status = new URL(r.request().url()).searchParams.get("ticket_status");
    return r.fulfill({ json: { ...TICKETS, tickets: TICKETS.tickets.filter((t) => !status || t.status === status) } });
  });
  await page.route(A("/admin/support/settings$"), (r) => (r.request().method() === "GET" ? r.fulfill({ json: DESK_SETTINGS }) : r.fallback()));
  await page.route(A("/admin/alerts/outbox$"), ok({ by_status: { DELIVERED: 1200, PENDING: 2, DEAD: 1 }, claimed: 0, oldest_pending_seconds: 14 }));
  await page.route(A("/admin/alerts/dead-letters(\\?.*)?$"), ok([{ id: 77, tenant_id: 7, channel: "TELEGRAM", event_type: "SIGNAL", status: "DEAD", error_class: "http_4xx", attempts: 5, created_at: T(300) }]));
  await page.route(A("/admin/retention$"), ok({ policy: {}, eligible_now: { market_snapshots: 120, login_events: 0 }, last_run: { at: T(60 * 20), detail: "120 rows" } }));
  await page.route(A("/admin/screener/audit$"), ok({ screens_checked: 14, alert_rules_checked: 6, failing: [] }));
  await page.route(A("/admin/history-import/progress(\\?.*)?$"), ok({ datasets: { daily: { done: 480, pending: 2 } }, symbols: [], symbol_count: 482 }));
  await page.route(A("/admin/history-backfill/progress(\\?.*)?$"), ok({ flag_on: false, segments: { cm: { by_status: { done: 240 } } }, attention: [] }));
  await page.route(A("/admin/history-intraday/progress(\\?.*)?$"), ok({ flag_on: false, tenant_id: null, instruments: {}, attention: [] }));
  await page.route(A("/admin/learn/settings$"), ok(LEARN_SETTINGS));
  await page.route(A("/admin/growth/referral-settings$"), ok(REFERRAL_SETTINGS));
  await page.route(A("/admin/chain-history/status$"), ok(CHAIN_HISTORY));
  if (o.routes) await o.routes(page);
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  return asked;
}
