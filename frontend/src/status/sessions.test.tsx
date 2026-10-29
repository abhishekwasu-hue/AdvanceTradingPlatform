import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DegradedNoticeView } from "./DegradedNotice";
import { StatusView } from "./StatusView";
import type { PublicStatus } from "./publicStatus";
import {
  headlineUptime, loadStatusSessions, measuredLine, nextOpenLine, uptimeText, type MarketSession, type StatusSessions,
} from "./sessions";

const status: PublicStatus = {
  status: "operational",
  checked_at: "2026-10-29T05:30:00+00:00",
  components: {
    api: { state: "operational", summary: "Answering requests." },
    worker: { state: "operational", summary: "Strategies, scans and alerts are being processed.", seconds_since_heartbeat: 12 },
    data_feed: {
      state: "operational", summary: "End-of-day data is up to date (2026-10-28).", market_open: true,
      eod: { state: "operational", date: "2026-10-28", summary: "End-of-day data is up to date (2026-10-28)." },
      tier_a: { enabled: false, sessions: 0, summary: "Live data through your own broker is not enabled." },
    },
    alerts: { state: "operational", summary: "Alerts are being delivered.", lag_seconds: 0 },
  },
  banner: { maintenance: false, maintenance_message: null, incidents: [] },
};

const nse: MarketSession = {
  key: "NSE", name: "NSE equity and F&O", open: true, next_open: null, state: "operational",
  recent: [
    { date: "2026-10-29", opens_at: "2026-10-29T09:15:00+05:30", closes_at: "2026-10-29T15:30:00+05:30", in_progress: true, uptime_pct: 99.8 },
    { date: "2026-10-28", opens_at: "2026-10-28T09:15:00+05:30", closes_at: "2026-10-28T15:30:00+05:30", in_progress: false, uptime_pct: 100 },
  ],
};
const mcxClosed: MarketSession = {
  key: "MCX", name: "MCX commodities", open: false, next_open: "2026-10-30T09:00:00+05:30", state: "closed",
  recent: [{ date: "2026-10-29", opens_at: "2026-10-29T09:00:00+05:30", closes_at: "2026-10-29T23:30:00+05:30", in_progress: false, uptime_pct: null }],
};
const body: StatusSessions = {
  checked_at: "2026-10-29T05:30:00+00:00",
  measured: { seconds_since_heartbeat: 20, heartbeat_limit_seconds: 180, data_fresh: true, measured_since: "2026-10-20T00:00:00+00:00" },
  sessions: [nse, mcxClosed],
  slos: [{ id: "SLO-2", objective: "Engine liveness on trading days", target: "Trading engine: checks in at least every 3 minutes while a market is open, in 99.9% of market minutes." }],
  notice: null,
  public_page: true,
};

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;|&apos;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");

describe("AUD-T8 uptime per market session", () => {
  it("formats uptime, the shared measurement line and the next open", () => {
    expect(uptimeText(100)).toBe("100%");
    expect(uptimeText(99.8)).toBe("99.8%");
    expect(uptimeText(76.67)).toBe("76.67%");
    expect(uptimeText(null)).toBe("Not measured");
    expect(headlineUptime(nse)).toBe("This session: 99.8%");
    expect(headlineUptime(mcxClosed)).toBe("Last session (29 Oct 2026): Not measured");
    expect(measuredLine(body.measured)).toBe("Engine checked in 20 seconds ago. Market data on time.");
    expect(measuredLine({ ...body.measured, seconds_since_heartbeat: null, data_fresh: null })).toBe("The engine has not checked in yet.");
    expect(nextOpenLine(mcxClosed)).toBe("Opens 30 Oct 2026, 09:00 IST");
    expect(nextOpenLine(nse)).toBeNull();
  });

  it("renders the Markets block and the targets; past sessions and the method stay in Details; no repeated NSE line", () => {
    const html = renderToStaticMarkup(<StatusView load={{ kind: "ok", status }} sessions={{ kind: "ok", body }} />);
    const words = text(html);
    expect(html.match(/data-market=/g)).toHaveLength(2);
    expect(words).toContain("Open · Operational");
    expect(words).toContain("Closed");
    expect(words).toContain("This session: 99.8%");
    expect(words).toContain("Service targets");
    expect(html).toContain('data-slo="SLO-2"');
    expect(html).toContain('data-details="status.market.NSE"');       // two sessions -> Recent sessions in Details
    expect(html).not.toContain('data-details="status.market.MCX"');   // one session: nothing more to show
    expect(html).toContain('data-details="status.uptime"');
    expect(words).not.toContain("NSE session: open");                 // said once, in the Markets block
    expect(words.match(/Engine checked in/g)).toHaveLength(1);
  });

  it("shows nothing new while the flag is off", () => {
    const html = renderToStaticMarkup(<StatusView load={{ kind: "ok", status }} sessions={{ kind: "off" }} />);
    expect(html).not.toContain("data-markets");
    expect(text(html)).toContain("NSE session: open");
  });

  it("reads 404 and failures as off", async () => {
    const reply = (code: number, payload: unknown = {}) => (async () => new Response(JSON.stringify(payload), { status: code })) as unknown as typeof fetch;
    expect(await loadStatusSessions(reply(404))).toEqual({ kind: "off" });
    expect(await loadStatusSessions(reply(200, { nope: 1 }))).toEqual({ kind: "off" });
    expect(await loadStatusSessions((async () => { throw new Error("offline"); }) as unknown as typeof fetch)).toEqual({ kind: "off" });
    const ok = await loadStatusSessions(reply(200, body));
    expect(ok.kind).toBe("ok");
  });
});

describe("AUD-T8 in-app degradation notice", () => {
  it("is silent while all is well and says the server's plain words when degraded", () => {
    expect(renderToStaticMarkup(<DegradedNoticeView notice={null} statusLink />)).toBe("");
    const html = renderToStaticMarkup(<DegradedNoticeView statusLink
      notice={{ state: "degraded", text: "Market data is running late. Signals wait for fresh data and resume on their own." }} />);
    expect(html).toContain('data-degraded-notice="degraded"');
    expect(html).toContain('role="status"');
    expect(text(html)).toContain("Market data is running late.");
    expect(html).toContain('href="/status"');
    const noLink = renderToStaticMarkup(<DegradedNoticeView statusLink={false} notice={{ state: "outage", text: "The trading engine is catching up." }} />);
    expect(noLink).toContain('data-degraded-notice="outage"');
    expect(noLink).not.toContain("href=");
  });
});
