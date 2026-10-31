import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** M63 I-7: smoke test for the multi-chart workspace window (`?workspace=1`, flag chart_workspace). */

const ctx = vi.hoisted(() => ({ user: null as unknown, loading: false, flags: [] as string[], loaded: true }));
vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  const pending = () => new Promise(() => {});                       // no answer yet: the window shows its first screen
  return { ...real, api: new Proxy({}, { get: () => pending }) };
});
vi.mock("../auth/AuthContext", () => {
  const value = () => ({ user: ctx.user, loading: ctx.loading });
  return { useAuth: value, useOptionalAuth: value };
});
vi.mock("../ui/useFeatures", () => ({
  useFeatures: () => ({ on: (n: string) => ctx.loaded && ctx.flags.includes(n), loaded: ctx.loaded, answered: ctx.loaded, features: {} }),
  useUiV2: () => ctx.loaded && ctx.flags.includes("ui_v2"),
}));

import ChartWorkspace from "./ChartWorkspace";
import { expectNoDeveloperText, renderPage, textOf } from "./pageSmoke.fixture";

const TRADER = { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] };
const open = (query: string) => renderPage(<ChartWorkspace params={new URLSearchParams(query)} />);

describe("Chart workspace window", () => {
  beforeEach(() => { ctx.user = TRADER; ctx.loading = false; ctx.flags = []; ctx.loaded = true; });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("says it is loading, in words, while the flags are not known yet", () => {
    ctx.loaded = false;
    expect(textOf(open("workspace=1"))).toBe("Loading…");
  });

  it("says the workspace is not enabled, in words, while the flag is off", () => {
    const html = open("workspace=1");
    expect(textOf(html)).toContain("The multi-chart workspace is not enabled for your organisation yet.");
    expectNoDeveloperText(html);
  });

  it("asks a signed-out visitor to log in first, in words", () => {
    ctx.user = null;
    const html = open("workspace=1");
    expect(textOf(html)).toBe("Log in in the main tab first, then open the workspace again.");
    expectNoDeveloperText(html);
  });

  it("renders the workspace with its first chart and no developer text while the flag is on", () => {
    ctx.flags = ["chart_workspace"];
    vi.stubGlobal("window", { innerHeight: 800, location: { pathname: "/", search: "" }, matchMedia: () => ({ matches: false }) });
    const html = open("workspace=1&chart=NIFTY&tf=15min&exchange=NSE");
    expect(textOf(html)).toContain("NIFTY");
    expectNoDeveloperText(html);
  });
});
