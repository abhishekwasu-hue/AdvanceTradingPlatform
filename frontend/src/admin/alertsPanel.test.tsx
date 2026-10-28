import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AlertsPanelView, sortChecks, summaryLine } from "./AlertsPanelV2";
import type { OpsAlerts } from "./opsApi";

/** A2: Admin > System > Alerts - problems first, the channels in plain words, never a chat id or an address. */

const base: OpsAlerts = {
  checked_at: new Date(Date.now() - 120_000).toISOString(),
  source: "server",
  checks: [
    { key: "error_rate", label: "API error rate", state: "ok", detail: "0.0% of 900 requests failed.", source: "app", since: null, alerted: null },
    { key: "backup", label: "Backups", state: "fail", detail: "Local backup 30 h old.", source: "server", since: new Date(Date.now() - 3_600_000).toISOString(), alerted: "fail" },
    { key: "db_size", label: "Database size", state: "unknown", detail: "Measured on the production database only.", source: "app", since: null, alerted: null },
    { key: "disk", label: "Disk space", state: "warn", detail: "87% used.", source: "server", since: null, alerted: "warn" },
  ],
  schedule: { server_reported_at: new Date().toISOString(), late: false },
  last_message: null,
  channels: { telegram: { available: true, connected: true, from: "linked", pending: false }, email: { available: false, recipients: 1 } },
  error_tracker: { on: false },
};
const noop = () => {};
const render = (data: OpsAlerts) => renderToStaticMarkup(
  <AlertsPanelView data={data} busy={false} note={null} onRun={noop} onTest={noop} onLink={noop} onUnlink={noop} />);

describe("alerts panel", () => {
  it("puts problems first and says the summary in words", () => {
    expect(sortChecks(base.checks).map((c) => c.key)).toEqual(["backup", "disk", "db_size", "error_rate"]);
    expect(summaryLine(base)).toBe("1 failing, 1 warning");
    expect(summaryLine({ ...base, checks: [base.checks[0]] })).toBe("Everything checked is OK");
    expect(summaryLine({ ...base, checked_at: null })).toBe("No check has run yet.");
    const html = render(base);
    expect(html.indexOf('data-ops-check="backup"')).toBeLessThan(html.indexOf('data-ops-check="error_rate"'));
    expect(html).toContain("Failing");
    expect(html).toContain("Your chat is connected.");
    expect(html).toContain("E-mail (SMTP) is not set up on the server.");
    expect(html).toContain("data-ops-unlink");
    expect(html).not.toContain("data-ops-no-channel");
  });

  it("warns when nobody would be told and when the server schedule is late", () => {
    const html = render({ ...base, schedule: { server_reported_at: null, late: true },
      channels: { telegram: { available: true, connected: false, from: "none", pending: false }, email: { available: false, recipients: 0 } } });
    expect(html).toContain("data-ops-late");
    expect(html).toContain("has not reported yet");
    expect(html).toContain("data-ops-no-channel");
    expect(html).toContain("data-ops-link");
    expect(html).toMatch(/data-ops-test="true"[^>]*disabled|disabled[^>]*data-ops-test/);
  });

  it("a chat set on the server cannot be disconnected from here; a missing bot says so", () => {
    let html = render({ ...base, channels: { ...base.channels, telegram: { available: true, connected: true, from: "env", pending: false } } });
    expect(html).toContain("Connected (chat set on the server).");
    expect(html).not.toContain("data-ops-unlink");
    html = render({ ...base, channels: { ...base.channels, telegram: { available: false, connected: false, from: "none", pending: false } } });
    expect(html).toContain("The platform bot is not set up on the server yet.");
    expect(html).toContain("Set ATP_TELEGRAM_BOT_TOKEN in the server&#x27;s .env");               // SCR-FIX C4: the key NAME only
    expect(html).not.toContain("data-ops-link");
  });
});
