import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { StatusView } from "./StatusView";
import { StatusLinkView, externalStatusUrl, resetStatusProbe, statusProbe, statusTarget } from "./StatusLink";
import { pageHeadline, type PublicStatus } from "./publicStatus";

/** A2 (RDY-11): the footer's Status link, the maintenance headline and the incident's public note. */

const ok: PublicStatus = {
  status: "operational",
  checked_at: "2026-10-13T05:30:00+00:00",
  components: {
    api: { state: "operational", summary: "Answering requests." },
    worker: { state: "operational", summary: "ok", seconds_since_heartbeat: 12 },
    data_feed: {
      state: "operational", summary: "ok", market_open: true,
      eod: { state: "operational", date: "2026-10-12", summary: "ok" },
      tier_a: { enabled: false, sessions: 0, summary: "off" },
    },
    alerts: { state: "operational", summary: "ok", lag_seconds: 0 },
  },
  banner: { maintenance: false, maintenance_message: null, incidents: [] },
};

afterEach(() => resetStatusProbe());

describe("where the Status link goes", () => {
  it("an external status page (https only) wins and needs no read", () => {
    expect(externalStatusUrl("https://status.example.com")).toBe("https://status.example.com");
    expect(externalStatusUrl("http://status.example.com")).toBeNull();
    expect(externalStatusUrl("javascript:alert(1)")).toBeNull();
    expect(externalStatusUrl("")).toBeNull();
    expect(statusTarget(null, "https://status.example.com")).toEqual({ href: "https://status.example.com", external: true, state: null });
  });

  it("the in-app page only while it is on; an unreachable server still gets the link", () => {
    expect(statusTarget(null, null)).toBeNull();
    expect(statusTarget({ kind: "loading" }, null)).toBeNull();
    expect(statusTarget({ kind: "disabled" }, null)).toBeNull();
    expect(statusTarget({ kind: "ok", status: { ...ok, status: "degraded" } }, null)).toEqual({ href: "/status", external: false, state: "degraded" });
    expect(statusTarget({ kind: "unreachable" }, null)).toEqual({ href: "/status", external: false, state: "outage" });
  });

  it("reads the status once for every footer on the screen", async () => {
    let calls = 0;
    const fetcher = (async () => { calls += 1; return new Response("{}", { status: 404 }); }) as unknown as typeof fetch;
    const [a, b] = await Promise.all([statusProbe(fetcher), statusProbe(fetcher)]);
    expect(a).toEqual({ kind: "disabled" });
    expect(b).toBe(a);
    expect(calls).toBe(1);
  });

  it("renders the state in words for screen readers and opens an external page in a new tab", () => {
    const inApp = renderToStaticMarkup(<StatusLinkView target={{ href: "/status", external: false, state: "operational" }} />);
    expect(inApp).toContain('href="/status"');
    expect(inApp).toContain("Status");
    expect(inApp).toContain(": Operational");
    expect(inApp).not.toContain("target=");
    const ext = renderToStaticMarkup(<StatusLinkView target={{ href: "https://status.example.com", external: true, state: null }} />);
    expect(ext).toContain('target="_blank"');
    expect(ext).toContain('rel="noopener noreferrer"');
    expect(ext).toContain("opens in a new tab");
    expect(renderToStaticMarkup(<StatusLinkView target={null} />)).toBe("");
  });
});

describe("status page: maintenance and incident notes", () => {
  it("planned maintenance is the headline when nothing is worse", () => {
    const maint = { ...ok, banner: { ...ok.banner, maintenance: true, maintenance_message: "Back at 18:00 IST." } };
    expect(pageHeadline(maint)).toEqual({ text: "Planned maintenance: no new entries", maintenance: true });
    expect(pageHeadline({ ...maint, status: "outage" }).maintenance).toBe(false);
    const html = renderToStaticMarkup(<StatusView load={{ kind: "ok", status: maint }} />);
    expect(html).toContain('data-state="maintenance"');
    expect(html).toContain("Planned maintenance: no new entries");
    expect(html).toContain("Back at 18:00 IST.");
  });

  it("an incident's public note is shown under it", () => {
    const html = renderToStaticMarkup(<StatusView load={{ kind: "ok", status: { ...ok, status: "degraded", banner: { ...ok.banner,
      incidents: [{ impact: "minor", state: "investigating", started_at: null, note: "Charts load slowly; a fix is being rolled out." }] } } }} />);
    expect(html).toContain("data-incident-note");
    expect(html).toContain("Charts load slowly; a fix is being rolled out.");
  });
});
