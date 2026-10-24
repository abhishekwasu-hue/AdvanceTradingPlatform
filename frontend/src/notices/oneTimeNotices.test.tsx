import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, NOTICES_CHANGED } from "../api/client";
import shellSource from "../shell/AppShellV2.tsx?raw";
import todayShellSource from "../App.tsx?raw";
import { NoticeCard } from "./OneTimeNoticesV2";
import { dismissNotice, readNotices, resetDismissedForTests, type OneTimeNotice } from "./oneTimeNotices";

/** TT12-1: the one-time "risk limits are now not set" notice - shown while the server says it is due, gone for good
 * once dismissed (in this tab at once, on the server for every device), only in the v2 shell. */

const TEXT = "Your risk limits are now not set — set your own in Settings › Risk";
const NOTICE: OneTimeNotice = { id: "limits_not_set", text: TEXT, link: "/risk-management" };

let seenOnServer = false;
let calls: { url: string; method: string }[] = [];

beforeEach(() => {
  seenOnServer = false;
  calls = [];
  resetDismissedForTests();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method });
    if (url.endsWith("/me/notices/limits_not_set/seen") && method === "POST") seenOnServer = true;
    const body = { notices: seenOnServer ? [] : [NOTICE] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); });

describe("TT12-1 one-time notice", () => {
  it("shows the exact text with a way to the Risk page and a close button", () => {
    const html = renderToStaticMarkup(<MemoryRouter><NoticeCard notice={NOTICE} onDismiss={() => undefined} /></MemoryRouter>);
    expect(html).toContain("Your risk limits are now not set — set your own in Settings › Risk");
    expect(html).toContain('href="/risk-management"');
    expect(html).toContain('aria-label="Dismiss"');
    expect(html).not.toMatch(/text-\[\d+px\]/);
  });

  it("is shown once: after a dismissal it never comes back", async () => {
    expect(await readNotices()).toEqual([NOTICE]);
    expect(await readNotices()).toEqual([NOTICE]);                    // reading does not mark it seen
    await dismissNotice("limits_not_set");
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual([expect.stringContaining("/me/notices/limits_not_set/seen")]);
    expect(await readNotices()).toEqual([]);
    resetDismissedForTests();                                         // a new tab / device: the server remembers
    expect(await readNotices()).toEqual([]);
  });

  it("stays hidden in this tab even when the server has not stored the dismissal yet", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ notices: [NOTICE] }), { status: 200, headers: { "Content-Type": "application/json" } })));
    await dismissNotice("limits_not_set");
    expect(await readNotices()).toEqual([]);
  });

  it("a failed read shows nothing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 500 })));
    expect(await readNotices()).toEqual([]);
  });

  it("saving a risk limit tells the shell to re-read", async () => {
    const heard = vi.fn();
    const target = new EventTarget();
    vi.stubGlobal("window", target);
    target.addEventListener(NOTICES_CHANGED, heard);
    await api.setPaperRisk({ min_rr: 2 });
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("lives in the v2 shell only (behind ui_v2)", () => {
    expect(shellSource).toContain("<OneTimeNoticesV2 />");
    expect(todayShellSource).not.toContain("OneTimeNoticesV2");
  });
});
