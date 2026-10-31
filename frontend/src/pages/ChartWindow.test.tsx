import { beforeEach, describe, expect, it, vi } from "vitest";

/** M63 I-7: smoke test for the single-chart window (`?chart=<symbol>`, opened from a chart's "New tab"). */

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

import ChartWindow from "./ChartWindow";
import { expectNoDeveloperText, renderPage, textOf } from "./pageSmoke.fixture";

const TRADER = { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] };
const open = (query: string) => renderPage(<ChartWindow params={new URLSearchParams(query)} />);

describe("Chart window", () => {
  beforeEach(() => { ctx.user = TRADER; ctx.loading = false; ctx.flags = []; ctx.loaded = true; });

  it("says it is loading, in words, until the first candles answer", () => {
    const html = open("chart=NIFTY&tf=15min");
    expect(textOf(html)).toBe("Loading…");
    expectNoDeveloperText(html);
  });

  it("says it is loading while the flags are not known yet", () => {
    ctx.loaded = false;
    expect(textOf(open("chart=NIFTY"))).toBe("Loading…");
  });

  it("asks for a symbol when the link carries none", () => {
    const html = open("");
    expect(textOf(html)).toBe("No symbol given.");
    expectNoDeveloperText(html);
  });

  it("asks a signed-out visitor to log in first, in words", () => {
    ctx.user = null;
    const html = open("chart=NIFTY");
    expect(textOf(html)).toBe("Log in in the main tab first, then open the chart again.");
    expectNoDeveloperText(html);
  });
});
