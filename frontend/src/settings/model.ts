import { brokerDot, connectionTitle, shownStatus } from "../broker/brokerState";
import { BROKER_PATH } from "../broker/paths";
import type { AiConnection } from "../aiKeys/connect";
import type { NavContext } from "../shell/navModel";
import { isItemVisible, searchPages } from "../shell/navModel";
import type { Appearance } from "../theme";
import { TEXT_SIZES } from "../theme";
import type { AiProviderConfig, AlertChannel, BillingOverview, BrokerConnection } from "../types";
import { SETTINGS_PATH } from "./paths";

/**
 * Settings redesign part B (docs/design/SETTINGS_REDESIGN_REVIEW.md B1-B3): the Settings index - one row per thing a
 * trader sets up, each with its live status - and the settings search. Pure functions only (unit-tested in
 * settings.test.tsx); the page is SettingsIndexV2.tsx.
 */

export type RowId = "account" | "team" | "broker" | "ai" | "alerts" | "defaults" | "risk" | "appearance" | "help" | "setup" | "advanced";

export interface SettingsRow {
  id: RowId;
  name: string;
  /** Where the row leads; null: not built yet ("Coming soon"). */
  to: string | null;
  /** The page tour step that points at this row (src/tours/content/settings.ts). */
  tour?: string;
  /** Words a trader may type to find this row (B2). */
  keywords: string[];
}

export const SETTINGS_ROWS: readonly SettingsRow[] = [
  { id: "account", name: "Account & plan", to: "/settings/account", keywords: ["profile", "email", "password", "two-factor", "sessions", "plan", "billing", "subscription", "invoice"] },
  // UX3-9 (UX audit section 5): Team and Risk limits left the sidebar; Settings holds them (same pages, same addresses).
  { id: "team", name: "Team", to: "/team", keywords: ["members", "invite", "roles"] },
  { id: "broker", name: "Broker", to: BROKER_PATH, keywords: ["upstox", "zerodha", "connect broker", "renew", "login", "token"] },
  { id: "ai", name: "AI", to: SETTINGS_PATH.ai, tour: "settings-ai", keywords: ["ai key", "api key", "connect ai", "claude", "openai", "gemini", "copilot", "ai limits", "spend"] },
  { id: "alerts", name: "Alerts", to: SETTINGS_PATH.alerts, tour: "settings-alerts", keywords: ["telegram", "notifications", "email", "sms", "whatsapp", "push", "phone"] },
  // Part C: the user's own starting values for new trading bots and chain tickets (a pre-fill, never a rule).
  { id: "defaults", name: "My trading defaults", to: "/settings/trading-defaults", keywords: ["defaults", "lots", "exits", "presets"] },
  { id: "risk", name: "Risk limits", to: "/risk-management", keywords: ["risk", "daily loss", "loss limit", "limits", "max positions"] },
  { id: "appearance", name: "Appearance", to: SETTINGS_PATH.appearance, tour: "settings-appearance", keywords: ["theme", "dark", "light", "text size", "font", "colour-blind", "color blind", "reduce motion"] },
  { id: "help", name: "Help & tips", to: SETTINGS_PATH.help, tour: "settings-help", keywords: ["tips", "tours", "get started", "guide"] },
  { id: "setup", name: "Set up your account", to: "/settings/setup", keywords: ["setup", "setup wizard", "steps"] },
  { id: "advanced", name: "Advanced", to: SETTINGS_PATH.advanced, keywords: ["webhook", "tradingview", "own bot", "bot token", "chat id", "integrations"] },
];

/** A setting inside a section page (B2: "telegram", "ai key", "text size" ... jump straight there). */
export interface SettingsEntry {
  label: string;
  /** The row it lives under (the caption in the results). */
  row: RowId;
  to: string;
  keywords?: string[];
  /** Shown only while this feature flag is on. */
  flag?: string;
  title: string;
}

const entry = (label: string, row: RowId, to: string, keywords: string[] = [], flag?: string): SettingsEntry =>
  ({ label, row, to, keywords, flag, title: label });

export const SETTINGS_ENTRIES: readonly SettingsEntry[] = [
  entry("Plan and billing", "account", "/settings/account#plan", ["subscription", "invoice", "renewal", "pay"]),
  entry("Password", "account", "/settings/account", ["change password"]),
  entry("Two-factor sign-in", "account", "/settings/account", ["authenticator", "mfa", "2fa"]),
  entry("Connect or renew your broker", "broker", BROKER_PATH, ["upstox", "zerodha", "login", "token"]),
  entry("Connect an AI key", "ai", SETTINGS_PATH.ai, ["ai key", "api key", "claude", "openai", "gemini", "paste key"]),
  entry("AI spending limits", "ai", SETTINGS_PATH.ai, ["limit", "cap", "spend", "cost"]),
  entry("Connect Telegram", "alerts", `${SETTINGS_PATH.alerts}#telegram`, ["telegram", "phone alerts"]),
  entry("Daily briefs on Telegram", "alerts", `${SETTINGS_PATH.alerts}#daily-briefs`, ["brief", "morning brief", "closing wrap", "weekly review", "digest"], "daily_rhythm"),
  entry("Email, SMS and app notifications", "alerts", `${SETTINGS_PATH.alerts}#channels`, ["email", "sms", "push", "whatsapp", "alert channels"]),
  entry("Text size", "appearance", `${SETTINGS_PATH.appearance}#appearance-text-size-label`, ["font", "bigger", "larger", "zoom"]),
  entry("Theme: light or dark", "appearance", `${SETTINGS_PATH.appearance}#appearance-theme-label`, ["dark mode", "light mode", "night"]),
  entry("Colour-blind friendly profit and loss", "appearance", `${SETTINGS_PATH.appearance}#appearance`, ["color blind", "colours"]),
  entry("Reduce motion", "appearance", `${SETTINGS_PATH.appearance}#appearance`, ["animations"]),
  entry("Show Get started again", "help", `${SETTINGS_PATH.help}#get-started`, ["get started", "steps"]),
  entry("Page tips on or off", "help", `${SETTINGS_PATH.help}#tips`, ["tips", "tours", "skip tips"]),
  entry("TradingView webhook", "advanced", `${SETTINGS_PATH.advanced}#webhook`, ["webhook", "tradingview", "alert url"]),
  entry("Your own Telegram bot", "advanced", `${SETTINGS_PATH.advanced}#own-bot`, ["bot token", "chat id", "botfather"]),
  entry("Personal API keys", "advanced", `${SETTINGS_PATH.advanced}#api-keys`, ["api keys", "public api"], "public_api"),
  entry("Vision background reviews", "advanced", `${SETTINGS_PATH.advanced}#vision`, ["vision", "chart review", "daily review"], "ai_one_key"),
];

export interface SearchHit {
  label: string;
  /** The row name it lives under, or null for a row itself. */
  under: string | null;
  to: string | null;
}

const ROW_NAME: Record<RowId, string> = Object.fromEntries(SETTINGS_ROWS.map((r) => [r.id, r.name])) as Record<RowId, string>;

/** B2: the rows and the settings inside them that match what the trader typed, best first (navModel's search). */
export function searchSettings(query: string, ctx: NavContext): SearchHit[] {
  if (!query.trim()) return [];
  const items = [
    ...SETTINGS_ROWS.map((r) => ({ label: r.name, title: r.name, keywords: r.keywords, hit: { label: r.name, under: null, to: r.to } as SearchHit })),
    ...SETTINGS_ENTRIES.filter((e) => isItemVisible(e, ctx)).map((e) => ({
      label: e.label, title: e.title, keywords: e.keywords, flag: e.flag, hit: { label: e.label, under: ROW_NAME[e.row], to: e.to } as SearchHit,
    })),
  ];
  return searchPages(query, ctx, items).map((i) => i.hit);
}

// --- the status chips (B1) ------------------------------------------------------------------------------------------

export type Tone = "ok" | "warn" | "bad" | "off";
export interface Chip { text: string; tone: Tone }

/** Broker: from GET /broker/connections. Null: not known (no chip, never a guess). */
export function brokerChip(rows: readonly BrokerConnection[] | null): Chip | null {
  if (!rows) return null;
  if (rows.length === 0) return { text: "Not connected", tone: "off" };
  const dot = brokerDot(rows);
  if (dot === "not_working") return { text: "Not working", tone: "bad" };
  if (dot === "needs_login") {
    const due = rows.find((c) => shownStatus(c) === "needs_login");
    return { text: due ? `Renew today (${connectionTitle(due)})` : "Renew today", tone: "warn" };
  }
  const names = rows.filter((c) => shownStatus(c) === "connected").map(connectionTitle);
  return { text: `Connected (${names.join(", ")})`, tone: "ok" };
}

/** AI with one key (Part A): "Connected" means a key row exists; a failed last test asks for a fix. */
export function aiChip(conn: Pick<AiConnection, "connected" | "label" | "ok"> | null): Chip | null {
  if (!conn) return null;
  if (!conn.connected) return { text: "Not connected", tone: "off" };
  if (conn.ok === false) return { text: `Needs a fix (${conn.label ?? "AI"})`, tone: "warn" };
  return { text: `Connected (${conn.label ?? "AI"})`, tone: "ok" };
}

const PROVIDER_LABEL: Record<string, string> = { anthropic: "Claude", openai: "OpenAI", gemini: "Google Gemini" };

/** AI without one key: the organisation's AI service (GET /ai/provider). */
export function aiProviderChip(p: Pick<AiProviderConfig, "configured" | "enabled" | "provider"> | null): Chip | null {
  if (!p) return null;
  if (!p.configured || !p.enabled) return { text: "Not connected", tone: "off" };
  return { text: `Connected (${PROVIDER_LABEL[p.provider] ?? p.provider})`, tone: "ok" };
}

const CHANNEL_NAME: Record<string, string> = { EMAIL: "Email", PUSH: "App notifications", SMS: "SMS", WEBHOOK: "Webhook", WHATSAPP: "WhatsApp", FCM: "Phone notifications", SIGNED_WEBHOOK: "Webhooks" };

/** Alerts: the organisation's channels (GET /alert-channels, a plain read: Telegram is never asked from here). */
export function alertsChip(channels: readonly Pick<AlertChannel, "channel_type" | "enabled" | "last_error">[] | null): Chip | null {
  if (!channels) return null;
  const tg = channels.find((c) => c.channel_type === "TELEGRAM");
  if (tg?.enabled && tg.last_error) return { text: "Telegram not working", tone: "bad" };
  if (tg?.enabled) return { text: "Telegram connected", tone: "ok" };
  const other = channels.filter((c) => c.enabled && c.channel_type !== "TELEGRAM").map((c) => CHANNEL_NAME[c.channel_type] ?? c.channel_type);
  if (other.length) return { text: `${other[0]} on · Telegram not connected`, tone: "warn" };
  return { text: "Not connected", tone: "off" };
}

/** Account & plan: the plan's name, and a payment that is due. */
export function planChip(o: Pick<BillingOverview, "subscription"> | null): Chip | null {
  const sub = o?.subscription;
  if (!sub) return null;
  if (sub.status === "PAST_DUE") return { text: `${sub.plan_name} · payment due`, tone: "warn" };
  if (sub.status === "TRIALING") return { text: `${sub.plan_name} · trial`, tone: "ok" };
  if (sub.status === "NONE" || sub.status === "CANCELLED") return { text: "No plan", tone: "off" };
  return { text: `${sub.plan_name} plan`, tone: "ok" };
}

const THEME_TEXT = { dark: "Dark", light: "Light", system: "Follows the device" } as const;

export function appearanceChip(a: Pick<Appearance, "theme" | "textSize"> & { highContrast?: boolean }): Chip {
  const size = TEXT_SIZES.find((t) => t.value === a.textSize)?.label ?? "Normal";
  return { text: `${THEME_TEXT[a.theme]} · ${size} text${a.highContrast ? " · High contrast" : ""}`, tone: "off" };
}

/** Help & tips: the tips' marks; null while not known. */
export function tipsChip(known: boolean, skipAllAt: string | null): Chip | null {
  if (!known) return null;
  return skipAllAt ? { text: "Tips off", tone: "off" } : { text: "Tips on", tone: "ok" };
}

export const COMING_SOON: Chip = { text: "Coming soon", tone: "off" };
/** My trading defaults: optional; nothing is used until the user saves a value. */
export const DEFAULTS_CHIP: Chip = { text: "Optional", tone: "off" };
