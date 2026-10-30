import { describe, expect, it } from "vitest";
import alertsSource from "../components/AlertChannelsCard.tsx?raw";
import fundamentalsSource from "../pages/FundamentalsPage.tsx?raw";
import newsSource from "../pages/NewsEventsPage.tsx?raw";
import notificationsSource from "../pages/NotificationsPage.tsx?raw";
import settingsSource from "../settings/SettingsSectionV2.tsx?raw";
import teamSource from "../pages/TeamPage.tsx?raw";
import { DCF_FIELDS, EMPTY_DCF, dcfAssumptionsFrom, numberOrNull } from "../pages/fundamentalsInputs";
import { notificationEventWords } from "./notificationWords";
import { pauseReasonDetail, pauseReasonText } from "./pauseReason";
import { attentionRows } from "../dashboard/model";
import { permissionWords, routingHelp, shownPermission } from "./permissionWords";

/** REV-1 M2 part 2: Signals, Library, News, Company research, Alerts, Setup, Notifications, Team. */
describe("pause reason in words", () => {
  it("the server's own user-pause text reads 'Paused by you', never the prefix or the page note", () => {
    expect(pauseReasonText("Paused by user: paused from the Trading bot page")).toBe("Paused by you");
    expect(pauseReasonText("Paused by user: paused from the chart")).toBe("Paused by you");
    expect(pauseReasonText("Paused by user")).toBe("Paused by you");
    expect(pauseReasonText("Paused on AI proposal #4 (loss_streak), approved by user 2")).toBe("Paused by you (you approved a suggestion to pause it)");
    expect(pauseReasonText("3 losses in a row.")).toBe("Paused: 3 losses in a row");
    expect(pauseReasonText("Auto-paused after 5 consecutive failures: broker_error {code=500}")).toBeNull();
    expect(pauseReasonText(null)).toBeNull();
  });

  it("Today's paused row: one sentence, never the stored prefix", () => {
    expect(pauseReasonDetail("Paused by user: paused from the Trading bot page")).toBe("You paused it.");
    expect(pauseReasonDetail("Paused on AI proposal #4 (loss_streak), approved by user 2")).toBe("You approved a suggestion to pause it.");
    expect(pauseReasonDetail("It hit your rule of 3 losses in a row at 10:21.")).toBe("It hit your rule of 3 losses in a row at 10:21.");
    expect(pauseReasonDetail("3 losses in a row")).toBe("3 losses in a row.");
    expect(pauseReasonDetail("auto_paused: consecutive_failures>=5")).toBeNull();
    const rows = attentionRows({ nowMs: 0, broker: { kind: "connected", brokers: [] }, status: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [] }, brokerRows: [], pending: [], risk: null, exposure: null, unread: [], emailVerified: true, approvalsPath: "/copilot/watchtower", deployments: [{ strategy_id: "orb_nifty", symbol: "NIFTY", status: "PAUSED",
      pause_reason: "Paused by user: paused from the Trading bot page", last_error: null }] } as never);
    expect(rows.find((r) => r.id === "paused")?.detail).toBe("You paused it. Nothing else is affected.");
  });
});

describe("Notifications: readable event names", () => {
  it("each server code in words, an unknown one as words, never the code", () => {
    expect(notificationEventWords("DAILY_LOSS_LIMIT")).toBe("Daily loss limit reached");
    expect(notificationEventWords("TOKEN_EXPIRED")).toBe("Broker login ran out");
    expect(notificationEventWords("AI_PROPOSAL")).toBe("Suggestion to review");
    expect(notificationEventWords("SOME_NEW_KIND")).toBe("Some new kind");
    for (const code of ["ENTRY", "EXIT", "REJECTION", "BROKER_DISCONNECT", "RISK_REJECTION", "EMERGENCY_EXIT", "SYSTEM_FAILURE", "SECURITY",
      "MARKETPLACE", "EOD_SUMMARY", "SCREEN_ALERT", "NEWS_ALERT", "THESIS_REPORT", "OI_BANNER", "OI_COLLECTOR", "SETTLEMENT_WARNING", "STRUCTURE_INCOMPLETE"]) {
      const words = notificationEventWords(code);
      expect(words).not.toMatch(/_|^[A-Z ]+$/);
      expect(words).not.toMatch(/\b(buy|sell|target|stop-loss)\b/i);
    }
    expect(notificationsSource).toContain("notificationEventWords(n.event_type)");
    expect(notificationsSource).not.toContain("{n.event_type}");
  });
});

describe("Team: no permission codes, no LIVE in Phase 0", () => {
  it("permissions in words; the LIVE one and platform admin are not shown in Phase 0", () => {
    expect(permissionWords("trading:write", "Place, configure and stop PAPER/LIVE trading").label).toBe("Run trading");
    expect(permissionWords("unknown:x", "Something new").label).toBe("Something new");
    expect(shownPermission("admin:platform", false)).toBe(false);
    expect(shownPermission("trading:live", true)).toBe(false);
    expect(shownPermission("trading:live", false)).toBe(true);
    expect(shownPermission("trading:write", true)).toBe(true);
    for (const scope of ["trading:write", "strategies:write", "brokers:write", "alerts:write", "risk:write", "billing:manage", "team:manage",
      "api_keys:manage", "marketplace:publish", "ai:manage", "analytics:read"]) {
      const w = permissionWords(scope, "");
      expect(`${w.label} ${w.line}`).not.toMatch(/:|\bLIVE\b|PAPER\//);
    }
    expect(routingHelp("The active account carrying the fewest open LIVE positions.", true)).toBe("The active account carrying the fewest open positions.");
    expect(routingHelp("The active account carrying the fewest open LIVE positions.", false)).toContain("LIVE");
  });

  it("the page uses useLiveHidden and never prints the code", () => {
    expect(teamSource).toContain("useLiveHidden()");
    expect(teamSource).not.toContain("{c.scope}</span>");
    expect(teamSource).toMatch(/liveHidden \? \(\s*<div[^]*Paper trading: practice money/);
  });
});

describe("Company research: no sample inputs, an honest header", () => {
  it("every input starts empty and nothing is computed until each figure is his own", () => {
    expect(fundamentalsSource).not.toMatch(/useState\((300|60|70)\)/);
    expect(fundamentalsSource).not.toMatch(/base_revenue: 1000|net_debt: 300|shares_outstanding: 50/);
    expect(fundamentalsSource).not.toContain('useState<"LONG" | "SHORT" | "NO_TRADE">("LONG")');
    expect(dcfAssumptionsFrom(EMPTY_DCF)).toBeNull();
    const filled = Object.fromEntries(DCF_FIELDS.map(([k], i) => [k, i + 1])) as typeof EMPTY_DCF;
    expect(dcfAssumptionsFrom({ ...filled, net_debt: null })).toBeNull();
    const body = dcfAssumptionsFrom(filled)!;
    expect(body.base_revenue).toBe(1);
    expect(body.revenue_growth_pct).toEqual([2, 2, 2, 2, 2]);
    expect(body.shares_outstanding).toBe(10);
    expect(numberOrNull("")).toBeNull();
    expect(numberOrNull("abc")).toBeNull();
    expect(numberOrNull("0")).toBe(0);
  });

  it("the header no longer says nothing is fetched while the page fetches from NSE", () => {
    expect(fundamentalsSource).not.toContain("no SEBI/NSE/BSE credentials are wired in yet");
    expect(fundamentalsSource).not.toContain("Institutional-grade");
    expect(fundamentalsSource).toContain('title="Company research"');
    expect(fundamentalsSource).toMatch(/fetched from NSE with Add from NSE/);
  });
});

describe("News: no flag name; a failed delete is shown", () => {
  it("source guards", () => {
    expect(newsSource).not.toMatch(/feature flag|news_feed\)/);
    expect(newsSource).toMatch(/async function handleDelete[^]*try \{[^]*catch \(e\) \{[^]*setDeleteError/);
    expect(newsSource).toContain("data-news-delete-error");
  });
});

describe("Settings › Alerts: no SMTP / DLT internals outside Advanced", () => {
  it("the channels part is plain words; the set-up lives under Advanced", () => {
    const channels = alertsSource.slice(alertsSource.indexOf('if (part === "channels") {'), alertsSource.indexOf('const delivery = part === "delivery";'));
    expect(channels.length).toBeGreaterThan(200);
    expect(channels).not.toMatch(/SMTP|DLT|dlt_|sender_id|trading worker|CRITICAL|webhook \(signed|HMAC/);
    expect(channels).toContain("ALERT_DELIVERY_PATH");
    expect(settingsSource).toMatch(/<Fold id="alert-delivery"[^]*<AlertChannelsCard part="delivery" \/>/);
    expect(settingsSource).toContain('<AlertChannelsCard ownBotOnly part="channels" />');
  });
});
