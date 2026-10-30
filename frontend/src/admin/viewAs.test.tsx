import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ refreshUser: vi.fn(async () => {}), logout: vi.fn() }));
vi.mock("../auth/AuthContext", () => {
  const value = () => ({ user: { id: 7, email: "pe****@example.com", tenant_id: 2, role: "OWNER", scopes: [] }, loading: false, ...auth });
  return { useAuth: value, useOptionalAuth: value };
});

import { getToken, request, setToken } from "../api/client";
import { endView, startView } from "../api/viewAs";
import { currentView, setView, subscribeView, timeLeft } from "../api/viewState";
import sectionsSource from "./AdminSectionsV2.tsx?raw";
import ViewAsBanner from "./ViewAsBanner";
import appSource from "../App.tsx?raw";
import accountSource from "../pages/AccountPage.tsx?raw";

/** MO3-A1: the admin's read-only "View as user" on the client - the banner, the token swap, and that an ended view
 * never falls back to the admin's token under the person's page. */

type Call = { url: string; method: string; auth: string | null };
let calls: Call[] = [];

function serve(handler: (url: string, method: string) => { status: number; body: unknown }) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const headers = (init?.headers ?? {}) as Record<string, string>;
    calls.push({ url, method, auth: headers.Authorization ?? null });
    const { status, body } = handler(url, method);
    return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }));
}

const VIEW = {
  id: 11, user_id: 7, tenant_id: 2, name: "pe****@example.com", reason: "The person asked about a trading bot", started_at: "2026-10-23T10:00:00Z",
  expires_at: "2026-10-23T10:30:00Z", ended_at: null, end_reason: null, active: true, seconds_left: 1800, requests: 0, refused: 0, last_seen_at: null,
};

beforeEach(() => { calls = []; setView(null); setToken("admin-token"); });
afterEach(() => { vi.unstubAllGlobals(); setView(null); });

const banner = () => renderToStaticMarkup(<MemoryRouter><ViewAsBanner /></MemoryRouter>);

describe("the banner", () => {
  it("is not there without a view", () => {
    expect(banner()).toBe("");
  });

  it("says whom, read only, the time left, and End", () => {
    setView({ id: 11, userId: 7, name: "pe****@example.com", expiresAt: new Date(Date.now() + 12 * 60_000).toISOString() });
    const html = banner();
    expect(html).toContain('data-view-as-banner="7"');
    expect(html).toContain("Viewing as <strong class=\"font-semibold\">pe****@example.com</strong>");
    expect(html).toContain("> - read only</span>");                     // desktop: one line
    expect(html).toContain(">Read only · </span>");                      // phone: the second line
    expect(html).toContain("12 min left");
    expect(html).toContain(">End<");
    expect(html).toContain('role="status"');
    expect(html).toContain("sticky top-0");
  });

  it("is mounted over both shells, outside the v2-only shell", () => {
    expect(appSource).toContain("<ViewAsBanner />");
    expect(appSource.indexOf("<ViewAsBanner />")).toBeLessThan(appSource.indexOf("<Shell />"));
  });

  it("time left", () => {
    const now = Date.parse("2026-10-23T10:00:00Z");
    expect(timeLeft("2026-10-23T10:30:00Z", now)).toBe("30 min left");
    expect(timeLeft("2026-10-23T10:00:45Z", now)).toBe("45 s left");
    expect(timeLeft("2026-10-23T09:00:00Z", now)).toBe("0 s left");
  });
});

describe("starting and ending", () => {
  it("start swaps the token in memory for the view's token", async () => {
    serve((url) => (url.endsWith("/admin/view-sessions") ? { status: 200, body: { token: "view-token", view: VIEW } } : { status: 404, body: {} }));
    const seen: (number | null)[] = [];
    const off = subscribeView(() => seen.push(currentView()?.id ?? null));
    const info = await startView(7, "The person asked about a trading bot");
    off();
    expect(calls[0]).toMatchObject({ method: "POST", auth: "Bearer admin-token" });
    expect(info).toEqual({ id: 11, userId: 7, name: "pe****@example.com", expiresAt: "2026-10-23T10:30:00Z" });
    expect(getToken()).toBe("view-token");
    expect(seen).toEqual([11]);
  });

  it("end tells the server with the view's token, then drops the view", async () => {
    setToken("view-token");
    setView({ id: 11, userId: 7, name: "x", expiresAt: VIEW.expires_at });
    serve(() => ({ status: 200, body: { ended: true } }));
    await endView();
    expect(calls).toEqual([{ url: "/api/v1/view-session/end", method: "POST", auth: "Bearer view-token" }]);
    expect(currentView()).toBeNull();
  });

  it("an ended view on the server (401) drops the view and is never retried with the admin's refresh", async () => {
    setToken("view-token");
    setView({ id: 11, userId: 7, name: "x", expiresAt: VIEW.expires_at });
    serve(() => ({ status: 401, body: { detail: "Invalid or expired token" } }));
    await expect(request("/positions")).rejects.toBeTruthy();
    expect(calls.map((c) => c.url)).toEqual(["/api/v1/positions"]);       // no /auth/refresh, no second try
    expect(currentView()).toBeNull();
  });

  it("a refresh still in flight when a view opens never puts the admin's token under the view", async () => {
    let release: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { release = resolve; })));
    const { tryRefresh } = await import("../api/client");
    const pending = tryRefresh();
    setToken("view-token");
    setView({ id: 11, userId: 7, name: "x", expiresAt: VIEW.expires_at });
    release(new Response(JSON.stringify({ access_token: "admin-token-2", token_type: "bearer" }), { status: 200 }));
    expect(await pending).toBe(false);
    expect(getToken()).toBe("view-token");
  });

  it("a refused change shows the server's read-only sentence", async () => {
    setToken("view-token");
    setView({ id: 11, userId: 7, name: "x", expiresAt: VIEW.expires_at });
    const sentence = "This is a read-only support view. Nothing can be changed, placed, connected or downloaded from it.";
    serve(() => ({ status: 403, body: { detail: sentence, code: "view_read_only" } }));
    await expect(request("/watchlists", { method: "POST", body: "{}" })).rejects.toThrow(sentence);
    expect(currentView()).not.toBeNull();                                // a refusal does not end the view
  });
});

describe("Support and Settings", () => {
  it("Support offers the view with a required reason, never for an administrator or a removed account", () => {
    expect(sectionsSource).toContain("View as this person");
    expect(sectionsSource).toContain("Why are you opening this account? (required)");
    expect(sectionsSource).toMatch(/role === "SUPER_ADMIN"\) return/);
    expect(sectionsSource).toContain("the person sees it and your reason in their Settings");
    expect(sectionsSource).toContain("isStepUpError(err)");               // a stale two-factor check opens the code prompt
    expect(sectionsSource).toContain("<StepUpDialog");
  });

  it("the person's Settings lists when support viewed the account", () => {
    expect(accountSource).toContain("<SupportAccessCard />");
    expect(accountSource).toContain("Support has never viewed your account.");
  });
});
