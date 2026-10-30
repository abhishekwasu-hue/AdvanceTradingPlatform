import { describe, expect, it } from "vitest";

/** M55 C11: smoke test for the public Status page (no login, no app shell). */

import StatusPage from "./StatusPage";
import source from "./StatusPage.tsx?raw";
import { StatusView } from "../status/StatusView";
import { loadPublicStatus, type PublicStatus } from "../status/publicStatus";
import { expectNoDeveloperText, renderPage, textOf } from "./pageSmoke.fixture";

const ok = (): PublicStatus => ({
  status: "operational",
  checked_at: "2026-10-30T04:00:00Z",
  components: {
    api: { state: "operational", summary: "The app is answering." },
    worker: { state: "operational", summary: "Background jobs are running.", seconds_since_heartbeat: 4 },
    data_feed: {
      state: "operational", summary: "Market data is flowing.", market_open: true,
      eod: { state: "operational", summary: "End-of-day data is up to date.", date: "2026-10-29" },
      tier_a: { enabled: true, sessions: 3, summary: "Live data is on." },
    },
    alerts: { state: "operational", summary: "Alerts are on time.", lag_seconds: 2 },
  },
  banner: { maintenance: false, maintenance_message: null, incidents: [] },
});

describe("Status page", () => {
  it("shows its heading and says it is checking, before any answer", () => {
    const html = renderPage(<StatusPage />);
    expect(textOf(html)).toContain("AiAlgoDesk status");
    expect(textOf(html)).toContain("Checking the platform");
    expectNoDeveloperText(html);
  });

  it("shows the platform's state in words when the status answers", () => {
    const html = renderPage(<StatusView load={{ kind: "ok", status: ok() }} sessions={{ kind: "off" }} />);
    expect(textOf(html)).toContain("AiAlgoDesk status");
    expect(textOf(html)).toMatch(/operational/i);
    expectNoDeveloperText(html);
  });

  it("says a failed read in a plain sentence, never an error text", async () => {
    const down = (async () => { throw new TypeError("Failed to fetch"); }) as unknown as typeof fetch;
    const load = await loadPublicStatus(down);
    expect(load.kind).toBe("unreachable");
    const text = textOf(renderPage(<StatusView load={load} />));
    expect(text).toContain("Cannot reach the platform.");
    expect(text).not.toMatch(/TypeError|Failed to fetch/);
    const off = await loadPublicStatus((async () => new Response("", { status: 404 })) as unknown as typeof fetch);
    expect(textOf(renderPage(<StatusView load={off} />))).toContain("The status page is not enabled.");
  });

  it("needs no login: the page never reads the signed-in user", () => {
    expect(source).not.toMatch(/useAuth/);
  });
});
