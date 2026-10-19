import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StatusView } from "./StatusView";
import { checkedAt, headline, incidentText, loadPublicStatus, type PublicStatus } from "./publicStatus";

const ok: PublicStatus = {
  status: "operational",
  checked_at: "2026-10-13T05:30:00+00:00",
  components: {
    api: { state: "operational", summary: "Answering requests." },
    worker: { state: "operational", summary: "Strategies, scans and alerts are being processed.", seconds_since_heartbeat: 12 },
    data_feed: {
      state: "operational", summary: "End-of-day data is up to date (2026-10-12).", market_open: true,
      eod: { state: "operational", date: "2026-10-12", summary: "End-of-day data is up to date (2026-10-12)." },
      tier_a: { enabled: true, sessions: 3, summary: "3 live sessions through users' own brokers." },
    },
    alerts: { state: "operational", summary: "Alerts are being delivered.", lag_seconds: 0 },
  },
  banner: { maintenance: false, maintenance_message: null, incidents: [] },
};

const render = (status: PublicStatus) => renderToStaticMarkup(<StatusView load={{ kind: "ok", status }} />);
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");

describe("public status page (Z10-e G7)", () => {
  it("shows the overall state, every component in words and the data feed details", () => {
    const html = render(ok);
    const words = text(html);
    expect(words).toContain("All systems operational");
    expect(words).toContain("Checked at 11:00 IST");
    for (const name of ["Website and API", "Trading engine", "Market data", "Alert delivery"]) expect(words).toContain(name);
    expect(html.match(/data-component=/g)).toHaveLength(4);
    expect(words).toContain("End-of-day data as of 12 Oct 2026");
    expect(words).toContain("NSE session: open");
    expect(words).toContain("3 live sessions through users' own brokers.");
    expect(words.match(/Operational/g)?.length).toBe(4);           // the state is said in words, not colour only
  });

  it("is accessible: one h1, a labelled main landmark, a polite live region, decorative icons hidden", () => {
    const html = render(ok);
    expect(html.match(/<h1/g)).toHaveLength(1);
    expect(html).toContain('<main aria-labelledby="status-title"');
    expect(html).toContain('id="status-title"');
    expect(html).toContain('role="status" aria-live="polite" aria-label="Overall status"');
    expect(html.match(/<h3/g)).toHaveLength(4);
    expect(html).toContain("<h2");                                 // headings do not skip a level
    const svgs = html.match(/<svg[^>]*>/g) ?? [];
    const icons = svgs.filter((s) => s.includes("lucide"));
    expect(icons.length).toBeGreaterThanOrEqual(5);                 // the overall icon and one per component
    for (const svg of icons) expect(svg).toContain('aria-hidden="true"');
  });

  it("shows degraded and outage states, maintenance and incidents without any internal detail", () => {
    const html = render({
      ...ok,
      status: "outage",
      components: {
        ...ok.components,
        worker: { state: "outage", summary: "The engine has not checked in for 20 minutes during market hours.", seconds_since_heartbeat: 1200 },
        alerts: { state: "degraded", summary: "Telegram, email and push alerts are delayed by about 6 minutes.", lag_seconds: 400 },
      },
      banner: {
        maintenance: true, maintenance_message: "Upgrade until 18:00 IST",
        incidents: [{ impact: "major", state: "investigating", started_at: "2026-10-13T04:30:00+00:00" }],
      },
    });
    const words = text(html);
    expect(words).toContain("Part of the platform is down");
    expect(words).toContain("Planned maintenance: no new entries. Exits and monitoring keep working. Upgrade until 18:00 IST");
    expect(words).toContain("Major incident: we are investigating (since 10:00 IST).");
    expect(html).toContain('data-component="worker" data-state="outage"');
    expect(html).toContain('data-component="alerts" data-state="degraded"');
    expect(words).toContain("Outage");
    expect(words).toContain("Degraded");
  });

  it("says plainly when the page is off, loading or the platform cannot be reached", () => {
    expect(text(renderToStaticMarkup(<StatusView load={{ kind: "disabled" }} />))).toContain("The status page is not enabled.");
    expect(renderToStaticMarkup(<StatusView load={{ kind: "loading" }} />)).toContain('aria-live="polite"');
    const down = renderToStaticMarkup(<StatusView load={{ kind: "unreachable" }} />);
    expect(down).toContain('role="alert"');
    expect(text(down)).toContain("Cannot reach the platform.");
  });

  it("uses no advice wording and no Devanagari", () => {
    const words = text(render(ok)).toLowerCase();
    for (const w of [" buy ", " sell ", "recommend", " target", "guarantee", "profit"]) expect(words).not.toContain(w);
    expect(/[\u0900-\u097f]/.test(words)).toBe(false);
  });
});

describe("loadPublicStatus", () => {
  const respond = (status: number, body: unknown) =>
    (async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;

  it("reads 200 and 503 bodies, 404 as disabled, anything else as unreachable", async () => {
    expect(await loadPublicStatus(respond(200, ok))).toEqual({ kind: "ok", status: ok });
    expect((await loadPublicStatus(respond(503, { ...ok, status: "outage" }))).kind).toBe("ok");
    expect(await loadPublicStatus(respond(404, { detail: "The status page is not enabled." }))).toEqual({ kind: "disabled" });
    expect(await loadPublicStatus(respond(429, {}))).toEqual({ kind: "unreachable" });
    expect(await loadPublicStatus(respond(200, { nope: 1 }))).toEqual({ kind: "unreachable" });
    const offline = (async () => { throw new TypeError("Failed to fetch"); }) as unknown as typeof fetch;
    expect(await loadPublicStatus(offline)).toEqual({ kind: "unreachable" });
  });

  it("calls the versioned path without credentials", async () => {
    const calls: [string, RequestInit | undefined][] = [];
    const spy = (async (url: string, init?: RequestInit) => { calls.push([url, init]); return new Response("{}", { status: 404 }); }) as unknown as typeof fetch;
    await loadPublicStatus(spy);
    expect(calls[0][0]).toBe("/api/v1/status");
    expect(calls[0][1]?.credentials).toBe("omit");
  });

  it("formats headline, time and incidents", () => {
    expect(headline("degraded")).toBe("Some systems are degraded");
    expect(headline("unknown")).toBe("Status not known yet");
    expect(checkedAt("not a date")).toBe("Checked just now");
    expect(incidentText({ impact: "minor", state: "monitoring", started_at: null })).toBe("Incident: a fix is in place and we are monitoring.");
  });
});
