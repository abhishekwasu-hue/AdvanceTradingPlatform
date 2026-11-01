import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ALERT_DELIVERY_PATH } from "../components/AlertChannelsCard";
import { GO_LIVE_PATH_V2, SETTINGS_PATH } from "../settings/paths";
import { RISK_LIMITS_PATH } from "../settings/riskLimits";
import { ALERTS_PATH } from "../setup/getStarted";
import { START_BOT_PATH } from "../setup/model";
import { CONNECT_BROKER_PATH } from "../ui/BrokerGate";
import { createInPlaceApi, handoffs, openPanels, pushedIn, type NavigateFn, type RouterLocation } from "./api";
import { DEFAULT_REGISTRY } from "./panels";

/**
 * M75 NAV-7 (docs/design/M75_NO_NAVIGATION.md, the Settings and set-up rows): the 26 click-navigations of Settings and
 * set-up open in place: the PATH of the tab stays, the panel registered for the destination is on top (a section's row
 * kept as the hash), and closing returns to the exact entry before. "Show Get started" stays on the tab.
 */

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (f: string) => readFileSync(join(SRC, f), "utf8");

/** A made-up browser history driving the same API the provider uses (as in terminalChartsNav.test.tsx). */
function history(start: string) {
  const at = (path: string, st: unknown): RouterLocation => {
    const u = new URL(path, "http://app.invalid");
    return { pathname: u.pathname, search: u.search, hash: u.hash, state: st };
  };
  const entries: RouterLocation[] = [at(start, null)];
  let index = 0;
  const navigate = ((to: number | string | { pathname: string; search: string; hash: string }, opts?: { replace?: boolean; state?: unknown }) => {
    if (typeof to === "number") { index = Math.max(0, Math.min(entries.length - 1, index + to)); return; }
    const next = typeof to === "string" ? at(to, opts?.state ?? null) : { ...to, state: opts?.state ?? null };
    if (opts?.replace) entries[index] = next;
    else { entries.splice(index + 1); entries.push(next); index += 1; }
  }) as NavigateFn;
  return { navigate, location: () => entries[index], url: () => { const l = entries[index]; return `${l.pathname}${l.search}${l.hash}`; } };
}

const ON: Record<string, boolean> = { terminal: true };
const on = (f: string) => ON[f] === true;

const SITES: { site: string; to: string; kind: string; params?: Record<string, string> }[] = [
  { site: "aiKeys/ConnectAiPanelV2.tsx:385 AI connected chip", to: SETTINGS_PATH.ai, kind: "ai-key" },
  { site: "components/AlertChannelsCard.tsx:258 your own bot", to: "/settings/advanced#own-bot", kind: "advanced", params: { h: "own-bot" } },
  { site: "components/AlertChannelsCard.tsx:279 alert delivery", to: ALERT_DELIVERY_PATH, kind: "advanced", params: { h: "alert-delivery" } },
  { site: "components/DataSource.tsx:271 the broker", to: "/broker", kind: "broker" },
  { site: "growth/DisciplineCardV2.tsx:101 Risk status", to: "/risk-management", kind: "risk" },
  { site: "pages/SettingsPage.tsx:45 the broker (classic: a plain link)", to: "/broker", kind: "broker" },
  { site: "pages/SettingsPage.tsx:93 Prepare for LIVE (classic: a plain link; v2 address below)", to: GO_LIVE_PATH_V2, kind: "advanced", params: { h: "go-live" } },
  { site: "risk/RiskBlockV2.tsx:31 a risk block's link (data)", to: RISK_LIMITS_PATH, kind: "risk-limits" },
  { site: "settings/MyLimitsCardV2.tsx:26 Risk limits", to: RISK_LIMITS_PATH, kind: "risk-limits" },
  { site: "settings/SettingsIndexV2.tsx:213 Prepare for LIVE", to: GO_LIVE_PATH_V2, kind: "advanced", params: { h: "go-live" } },
  { site: "settings/SettingsSectionV2.tsx:193 the previous Terminal", to: "/terminal?view=classic", kind: "terminal", params: { q: "view=classic" } },
  { site: "settings/TradeSettingsV2.tsx:81 today's page (data)", to: "/settings/trading-defaults", kind: "trading-defaults" },
  { site: "settings/TradingDefaultsV2.tsx:284 Risk limits", to: RISK_LIMITS_PATH, kind: "risk-limits" },
  { site: "setup/GetStartedCardV2.tsx:77 a done step (data)", to: CONNECT_BROKER_PATH, kind: "broker" },
  { site: "setup/GetStartedCardV2.tsx:82 the next step (data)", to: ALERTS_PATH, kind: "alerts" },
  { site: "setup/GoLiveChecklistV2.tsx:87 a row's action (data)", to: RISK_LIMITS_PATH, kind: "risk-limits" },
  { site: "setup/SetupChipV2.tsx:17 the set-up chip (data)", to: "/settings/setup", kind: "setup" },
  { site: "setup/SetupPageV2.tsx:25 Prepare for LIVE", to: GO_LIVE_PATH_V2, kind: "advanced", params: { h: "go-live" } },
  { site: "setup/SetupWizardV2.tsx:113 Go to Today", to: "/", kind: "today" },
  { site: "setup/StepsV2.tsx:77 Connect (secondary)", to: CONNECT_BROKER_PATH, kind: "broker" },
  { site: "setup/StepsV2.tsx:90 Connect (primary)", to: CONNECT_BROKER_PATH, kind: "broker" },
  { site: "setup/StepsV2.tsx:173 Risk status", to: "/risk-management", kind: "risk" },
  { site: "setup/StepsV2.tsx:315 Start a bot", to: START_BOT_PATH, kind: "bots" },
  { site: "setup/StepsV2.tsx:356 Strategies", to: "/strategies", kind: "strategies" },
  { site: "setup/StepsV2.tsx:487 Alerts", to: ALERTS_PATH, kind: "alerts" },
];

describe("NAV-7: every Settings / set-up link opens in place (the path stays)", () => {
  it("covers 25 of the 26 sites (the 26th, Show Get started, now stays on the tab: below)", () => expect(SITES).toHaveLength(25));

  for (const s of SITES) {
    it(s.site, () => {
      const h = history("/settings");
      const before = h.location();
      const api = createInPlaceApi({ enabled: true, on, registry: DEFAULT_REGISTRY, location: h.location, navigate: h.navigate });
      expect(api.handles(s.to)).toBe(true);
      api.open(s.to);
      expect(h.location().pathname).toBe("/settings");
      expect(openPanels(h.location().search, DEFAULT_REGISTRY, on).map((p) => p.spec)).toEqual([{ kind: s.kind, params: s.params ?? {} }]);
      expect(pushedIn(h.location().state)).toBe(1);
      expect(handoffs(h.location().state)).toEqual([]);
      api.close();
      expect(h.location()).toBe(before);
    });
  }

  it("a section's row survives the panel's own address (the section opens on it)", () => {
    expect(DEFAULT_REGISTRY.get("advanced")!.href({ h: "go-live" })).toBe("/settings/advanced#go-live");
    expect(DEFAULT_REGISTRY.get("terminal")!.href({ q: "view=classic" })).toBe("/terminal?view=classic");
  });

  it("Today answers only for the bare `/`: the classic chart window's addresses on `/` are not Today", () => {
    expect(DEFAULT_REGISTRY.resolve("/")).toEqual({ kind: "today", params: {} });
    expect(DEFAULT_REGISTRY.resolve("/?contract=X")).toBeNull();
    expect(DEFAULT_REGISTRY.resolve("/?chart=NIFTY")).toEqual({ kind: "chart", params: { symbol: "NIFTY" } });
  });

  it("without the terminal flag the previous Terminal navigates as before", () => {
    const h = history("/settings/advanced");
    createInPlaceApi({ enabled: true, on: () => false, registry: DEFAULT_REGISTRY, location: h.location, navigate: h.navigate }).open("/terminal?view=classic");
    expect(h.url()).toBe("/terminal?view=classic");
  });
});

describe("NAV-7: the sites use the host (source)", () => {
  const files = ["aiKeys/ConnectAiPanelV2.tsx", "components/AlertChannelsCard.tsx", "components/DataSource.tsx", "growth/DisciplineCardV2.tsx",
    "pages/SettingsPage.tsx", "risk/RiskBlockV2.tsx", "settings/MyLimitsCardV2.tsx", "settings/TradeSettingsV2.tsx", "settings/TradingDefaultsV2.tsx",
    "setup/GetStartedCardV2.tsx", "setup/GoLiveChecklistV2.tsx", "setup/SetupChipV2.tsx", "setup/SetupPageV2.tsx", "setup/SetupWizardV2.tsx", "setup/StepsV2.tsx"];
  for (const f of files) it(`${f}: no plain Link left`, () => { expect(read(f)).not.toMatch(/<Link\b/); expect(read(f)).toMatch(/<InPlaceLink\b/); });
  it("settings/SettingsIndexV2.tsx: Prepare for LIVE", () => expect(read("settings/SettingsIndexV2.tsx")).toMatch(/<InPlaceLink to=\{GO_LIVE_PATH_V2\} data-prepare-live/));
  it("settings/SettingsSectionV2.tsx: the previous Terminal", () => expect(read("settings/SettingsSectionV2.tsx")).toMatch(/<InPlaceLink to="\/terminal\?view=classic" data-terminal-classic/));
  it("Show Get started stays on the tab he is on (no jump to Today)", () => expect(read("setup/ShowGetStartedV2.tsx")).not.toMatch(/navigate\(/));
});
