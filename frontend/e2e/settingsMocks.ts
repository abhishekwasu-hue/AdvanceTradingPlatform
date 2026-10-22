import type { Page, Route } from "@playwright/test";
import { mockApi } from "./mockApi";

/**
 * Settings redesign part B: the made-up replies the Settings index and its section pages read (e2e/settings-index-v2.e2e.ts).
 * Every status chip has a state here: the broker connections, the alert channels, the AI connection (Part A) or the
 * organisation's AI provider, the plan and the tips. Nothing here is a real key, token or account.
 */

export type Theme = "light" | "dark";

export const UPSTOX = { broker: "upstox", account_label: "primary", name: "Upstox", status: "connected",
  message: "Connected.", data_only: true, renew: false };
export const ZERODHA_LOGIN = { broker: "zerodha", account_label: "primary", name: "Zerodha", status: "needs_login",
  message: "Needs today's login.", data_only: false, renew: true };

export const TELEGRAM_PLATFORM = { channel_type: "TELEGRAM", enabled: true, min_severity: "WARNING", config: { platform_bot: true },
  last_delivered_at: "2026-10-21T09:00:00Z", last_error: null, updated_at: "2026-10-21T09:00:00Z" };

const LIMITS = { month_inr: 42, monthly_cap_inr: 880, today_inr: 3, daily_cap_inr: 88, max_cap_inr: 44000,
  line: "AI this month: ₹42 of your ₹880 limit", reached: null };
export const AI_NONE = { connected: false, provider: null, label: null, key_end: null, ok: null, error: null, tested_at: null,
  consent: null, may_train: false, paid_plan: true, free_tier_warning: null, limits: LIMITS, copy_tenant_key: null };
export const AI_CLAUDE = { ...AI_NONE, connected: true, provider: "anthropic", label: "Claude", key_end: "a9F2", ok: true,
  tested_at: "2026-10-22T05:30:00+00:00",
  consent: { accepted: true, text: "Your questions and chart images are sent to Claude to answer you. Never your broker login or passwords." } };
export const AI_FAILED = { ...AI_CLAUDE, ok: false, error: "This key has no credit left." };

const SUB = { plan_id: "starter", plan_name: "Starter", price_monthly: 0, price_yearly: 0, currency: "INR", trial_days: 0,
  provider: "manual", status: "ACTIVE", billing_cycle: null, current_period_end: null, grace_until: null, cancel_at_period_end: false };

const BYOK_STATUS = {
  enabled: true, active: true, provider: null, vision_line: "Vision is on", providers: [],
  audit: { proposals: true, scan_alerts: false, daily_review: false }, channels: { in_app: true, telegram: false },
  caps: { daily_cap_usd: 1, monthly_cap_usd: 10, max_cap_usd: 500 },
  spend: { today_usd: 0, month_usd: 0, month: "2026-10", resets: "midnight UTC" },
  notes: { subscription: "", privacy: "" },
  scope: { v3_enabled: false, scan_audits_per_hour: 0, daily_review_at: "", daily_review_max_symbols: 0, daily_review_timeframe: "" },
};

export interface SettingsMocks {
  theme?: Theme;
  features?: Record<string, boolean>;
  connections?: unknown[];
  channels?: unknown[];
  /** Part A's connection (with `ai_one_key`); null: the route answers 404. */
  ai?: unknown;
  /** The organisation's AI provider (without `ai_one_key`). */
  provider?: { configured: boolean; enabled: boolean; provider: string };
  /** The tips marks (GET /me/prefs?ns=tours); left out: 404 (the tips stay quiet). */
  tips?: "on" | "off";
  role?: string;
  /** More replies, registered after the ones above (so they win) and before the page opens. */
  routes?: (page: Page) => Promise<void>;
}

export const FEATURES = { ui_v2: true, ai_copilot: true, ai_one_key: true, phase0_paper_only: true };

/** Opens `path` with the Settings replies; returns the writes the page sent. */
export async function openSettings(page: Page, path: string, o: SettingsMocks = {}) {
  await page.clock.install({ time: new Date("2026-10-22T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
    } catch { /* storage unavailable */ }
    try { Object.defineProperty(Notification, "permission", { get: () => "denied" }); } catch { /* no Notification API */ }
  }, o.theme ?? "light");
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const calls = await mockApi(page, { features: o.features ?? FEATURES, brokers: [], connections: o.connections ?? [UPSTOX] });
  const ok = (json: unknown) => (r: Route) => r.fulfill({ json });
  await page.route(/\/api(?:\/v1)?\/alert-channels$/, ok(o.channels ?? []));
  await page.route(/\/api(?:\/v1)?\/alert-channels\/deliveries/, ok([]));
  await page.route(/\/api(?:\/v1)?\/alert-channels\/push\/public-key$/, ok({ public_key: "", configured: false }));
  await page.route(/\/api(?:\/v1)?\/alerts\/telegram\/connect$/, ok({ available: true, connected: (o.channels ?? []).length > 0, own_bot: false, working: true }));
  await page.route(/\/api(?:\/v1)?\/ai\/byok\/connection$/, (r) => (o.ai === null ? r.fulfill({ status: 404, json: { detail: "Not found" } }) : r.fulfill({ json: o.ai ?? AI_NONE })));
  await page.route(/\/api(?:\/v1)?\/ai\/byok\/status$/, ok(BYOK_STATUS));
  if (o.provider) {
    await page.route(/\/api(?:\/v1)?\/ai\/provider$/, ok({ model: "", api_key_set: o.provider.configured, ai_features_allowed: true, providers: [],
      default_models: {}, ...o.provider }));
  }
  await page.route(/\/api(?:\/v1)?\/billing$/, ok({ subscription: SUB, tenant_status: "ACTIVE", status_reason: null, limits: {}, metered_30d: {}, usage: {} }));
  await page.route(/\/api(?:\/v1)?\/billing\/plans$/, ok([]));
  await page.route(/\/api(?:\/v1)?\/billing\/transactions$/, ok([]));
  await page.route(/\/api(?:\/v1)?\/api-keys$/, ok([]));
  await page.route(/\/api(?:\/v1)?\/api-keys\/scopes$/, ok({}));
  await page.route(/\/api(?:\/v1)?\/webhooks\/tradingview\/token$/, ok({ webhook_token: null, webhook_url: null, configured: false }));
  await page.route(/\/api(?:\/v1)?\/auth\/sessions$/, ok([]));
  await page.route(/\/api(?:\/v1)?\/auth\/mfa\/status$/, ok({ enabled: false, recovery_codes_left: 0 }));
  if (o.tips) {
    const marks = o.tips === "off"
      ? { welcome_done_at: "2026-10-01T00:00:00Z", skip_all_at: "2026-10-01T00:00:00Z", seen: {} }
      : { welcome_done_at: "2026-10-01T00:00:00Z", skip_all_at: null, seen: { settings: "2026-10-01T00:00:00Z" } };
    await page.route(/\/api(?:\/v1)?\/me\/prefs$/, (r) => r.fulfill({ json: {
      prefs: { chart: {}, setup: {}, oc_analyses: { items: {} }, appearance: { text_size: "normal" }, tours: marks },
      versions: { chart: 0, setup: 0, oc_analyses: 0, appearance: 0, tours: 1 } } }));
  }
  if (o.role) {
    await page.route(/\/api(?:\/v1)?\/auth\/me$/, ok({ id: 3, email: "member@example.com", tenant_id: 2, role: o.role, email_verified: true, scopes: [] }));
  }
  if (o.routes) await o.routes(page);
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  return calls;
}
