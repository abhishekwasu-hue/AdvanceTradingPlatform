import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import appSource from "../App.tsx?raw";
import routesSource from "../routes.ts?raw";
import settingsPageSource from "../pages/SettingsPage.tsx?raw";
import setupPageSource from "../setup/SetupPageV2.tsx?raw";
import { SETTINGS_TOUR } from "../tours/content/settings";
import { fallbackAddress, flagOffFallback } from "../shell/aliases";
import { shellKeyFromPath, shellTitle } from "../shell/shellRoutes";
import { tourIdFor } from "../tours/pages";
import { ALERTS_PATH } from "../setup/getStarted";
import type { BrokerConnection } from "../types";
import {
  SETTINGS_ROWS, aiChip, aiProviderChip, alertsChip, appearanceChip, brokerChip, planChip, searchSettings, tipsChip,
} from "./model";
import { GO_LIVE_PATH_V2, SETTINGS_SECTIONS, settingsHashTarget } from "./paths";
import { IndexRow, StatusChip } from "./SettingsIndexV2";
import indexSource from "./SettingsIndexV2.tsx?raw";
import sectionSource from "./SettingsSectionV2.tsx?raw";
import { liveHiddenFrom } from "./useLiveHidden";

/** Settings redesign part B: the Settings index, its section pages, the search, the status chips and the old links. */

const ctx = (flags: string[] = [], role: string | null = "OWNER") => ({ on: (f: string) => flags.includes(f), role });
const conn = (over: Partial<BrokerConnection>): BrokerConnection =>
  ({ broker: "upstox", account_label: "primary", name: "Upstox", status: "connected", message: "", data_only: true, renew: false, ...over });

describe("B4: old links", () => {
  it.each([
    ["#alerts", "/settings/alerts"], ["#ai", "/settings/ai"], ["#integrations", "/settings/advanced#webhook"],
    ["#advanced", "/settings/advanced"], ["#appearance", "/settings/appearance"], ["#help", "/settings/help"],
  ])("%s on the index opens %s", (hash, to) => {
    expect(settingsHashTarget(hash, true)).toBe(to);
    expect(settingsHashTarget(hash, false)).toBe(to);
  });

  it("#go-live opens the index itself in Phase 0 (SR-9), the Go-live checklist after it", () => {
    expect(settingsHashTarget("#go-live", true)).toBe("/settings");
    expect(settingsHashTarget("#go-live", false)).toBe(GO_LIVE_PATH_V2);
  });

  it("no hash, or an unknown one, stays on the index", () => {
    expect(settingsHashTarget("", true)).toBeNull();
    expect(settingsHashTarget("#something", false)).toBeNull();
  });

  it("with ui_v2 off a section page goes back to today's Settings page at the part that holds it", () => {
    expect(flagOffFallback("/settings/ai")).toBe("/settings#ai");
    expect(flagOffFallback("/settings/alerts/")).toBe("/settings#alerts");
    expect(flagOffFallback("/settings/advanced")).toBe("/settings#integrations");
    expect(flagOffFallback("/settings/appearance")).toBe("/settings");
    expect(flagOffFallback("/settings/help")).toBe("/settings");
    expect(flagOffFallback("/settings")).toBeNull();
    expect(fallbackAddress("/settings/alerts", "?from=x", "")).toBe("/settings?from=x#alerts");
    expect(fallbackAddress("/settings/appearance", "", "#appearance-theme-label")).toBe("/settings#appearance-theme-label");
    expect(fallbackAddress("/settings/setup", "?a=1", "")).toBe("/settings?a=1");
    expect(fallbackAddress("/positions", "", "")).toBeNull();
  });

  it("the in-app links use the new pages", () => {
    expect(ALERTS_PATH).toBe("/settings/alerts");
    expect(setupPageSource).toContain("GO_LIVE_PATH_V2");
  });
});

describe("B1: the v2 router", () => {
  it("each section page has its shell location and title; the tour stays on the index", () => {
    for (const s of SETTINGS_SECTIONS) {
      expect(shellKeyFromPath(s.path)).toBe(s.key);
      expect(shellTitle(s.key)).not.toBe("");
      expect(tourIdFor(s.key)).toBeNull();
    }
    expect(shellKeyFromPath("/settings")).toBe("settings");
    expect(shellKeyFromPath("/settings/account")).toBe("account");
    expect(shellKeyFromPath("/settings/nothing")).toBeNull();
    expect(tourIdFor("settings")).toBe("settings");
  });

  it("ui_v2 off: today's router and App never load the index or the section pages", () => {
    expect(routesSource).toContain('settings: () => import("./pages/SettingsPage")');
    expect(routesSource).not.toMatch(/SettingsIndexV2|SettingsSectionV2/);
    expect(appSource).not.toMatch(/SettingsIndexV2|SettingsSectionV2/);
    // today's page keeps every card it had
    for (const card of ["<AppearanceCard />", "<BrokerLinkCard />", "<BillingCard />", "<AlertChannelsCard ownBotOnly={v2} />", "<WebhookCard />", "<ApiKeysCard />", "Prepare for LIVE", "<GoLiveChecklistV2 />"]) {
      expect(settingsPageSource).toContain(card);
    }
  });

  it("the rows: account, broker, AI, alerts, defaults (part C), appearance, help, setup, advanced", () => {
    expect(SETTINGS_ROWS.map((r) => r.id)).toEqual(["account", "broker", "ai", "alerts", "defaults", "appearance", "help", "setup", "advanced"]);
    expect(SETTINGS_ROWS.find((r) => r.id === "account")?.to).toBe("/settings/account");
    expect(SETTINGS_ROWS.find((r) => r.id === "broker")?.to).toBe("/broker");
    expect(SETTINGS_ROWS.find((r) => r.id === "defaults")?.to).toBe("/settings/trading-defaults");
  });

  it("the four settings tour steps point at index rows", () => {
    const tours = SETTINGS_ROWS.map((r) => r.tour).filter(Boolean);
    expect(SETTINGS_TOUR.steps.map((s) => s.target).sort()).toEqual([...tours].sort());
  });

  it("a row: icon, name, chip, chevron; 56 px tall; the chip wraps under the name on a phone", () => {
    const row = SETTINGS_ROWS.find((r) => r.id === "alerts")!;
    const html = renderToStaticMarkup(<MemoryRouter><ul><IndexRow row={row} chip={{ text: "Telegram connected", tone: "ok" }} /></ul></MemoryRouter>);
    expect(html).toContain('href="/settings/alerts"');
    expect(html).toContain('data-tour="settings-alerts"');
    expect(html).toContain("min-h-14");
    expect(html).toContain("flex-col");
    expect(html).toContain("sm:flex-row");
    expect(html).toContain("Telegram connected");
    expect(html).toContain('data-chip="ok"');
    const soon = renderToStaticMarkup(<MemoryRouter><ul><IndexRow row={{ ...SETTINGS_ROWS.find((r) => r.id === "defaults")!, to: null }} chip={{ text: "Coming soon", tone: "off" }} /></ul></MemoryRouter>);
    expect(soon).not.toContain("href=");                                    // a row not built yet has no link
    expect(soon).toContain("Coming soon");
    const defaults = renderToStaticMarkup(<MemoryRouter><ul><IndexRow row={SETTINGS_ROWS.find((r) => r.id === "defaults")!} chip={{ text: "Optional", tone: "off" }} /></ul></MemoryRouter>);
    expect(defaults).toContain('href="/settings/trading-defaults"');
    expect(renderToStaticMarkup(<StatusChip chip={{ text: "Not working", tone: "bad" }} />)).toContain("text-down");
  });
});

describe("B2: search", () => {
  const labels = (q: string, flags: string[] = []) => searchSettings(q, ctx(flags)).map((h) => h.label);

  it("finds the settings inside the rows and where they live", () => {
    expect(labels("telegram")).toContain("Connect Telegram");
    expect(searchSettings("telegram", ctx()).find((h) => h.label === "Connect Telegram")).toEqual({ label: "Connect Telegram", under: "Alerts", to: "/settings/alerts#telegram" });
    expect(labels("ai key")[0]).toMatch(/AI/);
    expect(labels("ai key")).toContain("Connect an AI key");
    expect(labels("text size")[0]).toBe("Text size");
    expect(searchSettings("text size", ctx())[0].to).toBe("/settings/appearance#appearance-text-size-label");
    expect(labels("webhook")).toContain("TradingView webhook");
    expect(labels("broker")[0]).toBe("Broker");
  });

  it("empty finds nothing; no match finds nothing", () => {
    expect(searchSettings("  ", ctx())).toEqual([]);
    expect(searchSettings("zzqq", ctx())).toEqual([]);
  });

  it("personal API keys only while public_api is on", () => {
    expect(labels("api keys")).not.toContain("Personal API keys");
    expect(labels("api keys", ["public_api"])).toContain("Personal API keys");
  });

  it("the Vision switches only with one AI key", () => {
    expect(labels("vision")).not.toContain("Vision background reviews");
    expect(labels("vision", ["ai_one_key"])).toContain("Vision background reviews");
  });
});

describe("B1: the status chips", () => {
  it("broker", () => {
    expect(brokerChip(null)).toBeNull();
    expect(brokerChip([])).toEqual({ text: "Not connected", tone: "off" });
    expect(brokerChip([conn({})])).toEqual({ text: "Connected (Upstox)", tone: "ok" });
    expect(brokerChip([conn({}), conn({ broker: "zerodha", name: "Zerodha", status: "needs_login", data_only: false, renew: true })]))
      .toEqual({ text: "Renew today (Zerodha)", tone: "warn" });
    expect(brokerChip([conn({ status: "not_working" })])).toEqual({ text: "Not working", tone: "bad" });
    // an analytics-only Upstox has nothing to log in to each day
    expect(brokerChip([conn({ status: "needs_login", data_only: true })])).toEqual({ text: "Connected (Upstox)", tone: "ok" });
  });

  it("AI with one key", () => {
    expect(aiChip(null)).toBeNull();
    expect(aiChip({ connected: false, label: null, ok: null })).toEqual({ text: "Not connected", tone: "off" });
    expect(aiChip({ connected: true, label: "Claude", ok: true })).toEqual({ text: "Connected (Claude)", tone: "ok" });
    expect(aiChip({ connected: true, label: "Claude", ok: null })).toEqual({ text: "Connected (Claude)", tone: "ok" });
    expect(aiChip({ connected: true, label: "Google Gemini", ok: false })).toEqual({ text: "Needs a fix (Google Gemini)", tone: "warn" });
  });

  it("AI without one key: the organisation's AI service", () => {
    expect(aiProviderChip(null)).toBeNull();
    expect(aiProviderChip({ configured: false, enabled: true, provider: "anthropic" })).toEqual({ text: "Not connected", tone: "off" });
    expect(aiProviderChip({ configured: true, enabled: false, provider: "anthropic" })).toEqual({ text: "Not connected", tone: "off" });
    expect(aiProviderChip({ configured: true, enabled: true, provider: "openai" })).toEqual({ text: "Connected (OpenAI)", tone: "ok" });
  });

  it("alerts", () => {
    const ch = (channel_type: string, enabled = true, last_error: string | null = null) => ({ channel_type, enabled, last_error }) as never;
    expect(alertsChip(null)).toBeNull();
    expect(alertsChip([])).toEqual({ text: "Not connected", tone: "off" });
    expect(alertsChip([ch("TELEGRAM")])).toEqual({ text: "Telegram connected", tone: "ok" });
    expect(alertsChip([ch("TELEGRAM", true, "Forbidden")])).toEqual({ text: "Telegram not working", tone: "bad" });
    expect(alertsChip([ch("TELEGRAM", false), ch("PUSH")])).toEqual({ text: "App notifications on · Telegram not connected", tone: "warn" });
  });

  it("plan", () => {
    const sub = (status: string) => ({ subscription: { plan_name: "Starter", status } }) as never;
    expect(planChip(null)).toBeNull();
    expect(planChip(sub("ACTIVE"))).toEqual({ text: "Starter plan", tone: "ok" });
    expect(planChip(sub("TRIALING"))).toEqual({ text: "Starter · trial", tone: "ok" });
    expect(planChip(sub("PAST_DUE"))).toEqual({ text: "Starter · payment due", tone: "warn" });
    expect(planChip(sub("NONE"))).toEqual({ text: "No plan", tone: "off" });
  });

  it("appearance and tips", () => {
    expect(appearanceChip({ theme: "dark", textSize: "large" }).text).toBe("Dark · Large text");
    expect(appearanceChip({ theme: "system", textSize: "normal" }).text).toBe("Follows the device · Normal text");
    expect(tipsChip(false, null)).toBeNull();
    expect(tipsChip(true, null)).toEqual({ text: "Tips on", tone: "ok" });
    expect(tipsChip(true, "2026-10-01T00:00:00Z")).toEqual({ text: "Tips off", tone: "off" });
  });
});

describe("B3: nothing about LIVE in Phase 0", () => {
  it("hidden while phase0_paper_only is on, or the flags are not known", () => {
    expect(liveHiddenFrom({ answered: false, on: () => false })).toBe(true);
    expect(liveHiddenFrom({ answered: true, on: (f) => f === "phase0_paper_only" })).toBe(true);
    expect(liveHiddenFrom({ answered: true, on: () => false })).toBe(false);
  });

  it("the index, the Advanced page and the Setup page gate their LIVE parts on it", () => {
    expect(indexSource).toMatch(/!liveHidden && \(\s*<Link to=\{GO_LIVE_PATH_V2\}/);
    expect(sectionSource).toMatch(/!liveHidden && \(\s*<Fold id="go-live"/);
    expect(setupPageSource).toContain("user && !liveHidden && (");
  });

  it("personal API keys on the Advanced page only while public_api is on", () => {
    expect(sectionSource).toMatch(/features\.on\("public_api"\) && \(\s*<Fold id="api-keys"/);
  });
});
