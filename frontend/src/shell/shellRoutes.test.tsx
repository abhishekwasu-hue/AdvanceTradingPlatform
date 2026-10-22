import { describe, expect, it } from "vitest";
import { DATA_PAGES } from "../ui/dataPages";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, vi } from "vitest";
import { shellMode } from "./aliases";
import ShellWaitingV2 from "./ShellWaitingV2";
import { SHELL_WAIT_MS, startWaitCap } from "./shellWait";
import { DEFAULT_SYMBOL, chartsSymbol } from "../chart/chartsPage";
import { V2_ALIASES, chartsPath, flagOffFallback, routerPath, chartsSymbolFromPath, isGatedPath, shellKeyFromPath, shellTitle, v2RedirectFor } from "./shellRoutes";

const flags = (...on: string[]) => (f: string) => on.includes(f);
const signedIn = { signedIn: true, on: flags() };

describe("UI2-1 v2 addresses", () => {
  it("/charts and /charts/<symbol> are the Charts page", () => {
    expect(chartsSymbolFromPath("/charts")).toBeNull();
    expect(chartsSymbolFromPath("/charts/")).toBeNull();
    expect(chartsSymbolFromPath("/charts/reliance")).toBe("RELIANCE");
    expect(chartsSymbolFromPath("/charts/NIFTY%2050")).toBe("NIFTY 50");
    expect(chartsSymbolFromPath("/charts/%E0%A4")).toBe("%E0%A4");          // a broken escape is used as written
    expect(chartsSymbolFromPath("/charts/a/b")).toBeUndefined();
    expect(chartsSymbolFromPath("/chartsx")).toBeUndefined();
    expect(chartsPath("nifty 50")).toBe("/charts/NIFTY%2050");
    expect(chartsPath("")).toBe("/charts");
    expect(chartsSymbolFromPath(chartsPath("M&M"))).toBe("M&M");
  });

  it("Charts opens on the address's symbol, else the last one viewed, else NIFTY 50", () => {
    expect(chartsSymbol("RELIANCE", "TCS")).toBe("RELIANCE");
    expect(chartsSymbol(null, "tcs")).toBe("TCS");
    expect(chartsSymbol(undefined, null)).toBe(DEFAULT_SYMBOL);
    expect(chartsSymbol(null, "   ")).toBe(DEFAULT_SYMBOL);
  });

  it("names every location (merged pages at their new addresses too)", () => {
    expect(shellKeyFromPath("/")).toBe("dashboard");
    expect(shellKeyFromPath("/charts/TCS")).toBe("charts");
    expect(shellKeyFromPath("/screener/custom")).toBe("scanner");
    expect(shellKeyFromPath("/option-chain/plan/")).toBe("options-builder");
    expect(shellKeyFromPath("/journal/coach")).toBe("coach");
    expect(shellKeyFromPath("/settings/account")).toBe("account");
    expect(shellKeyFromPath("/copilot/ask")).toBe("copilot");
    expect(shellKeyFromPath("/settings/other")).toBeNull();
    expect(shellTitle("dashboard")).toBe("Today");
    expect(shellTitle("charts")).toBe("Charts");
    expect(shellTitle("scanner")).toBe("Custom scan");
    expect(shellTitle(null)).toBe("Page not found");
  });

  it("every alias shows a real page, and its old address is that page today", () => {
    for (const a of V2_ALIASES) {
      expect(shellKeyFromPath(a.path)).toBe(a.page);
      expect(shellKeyFromPath(a.old)).toBe(a.page);
    }
  });

  it("with the flag off, only a merged page's new address goes back to today's address", () => {
    expect(flagOffFallback("/settings/account")).toBe("/account");          // signed out on Settings -> Account
    expect(flagOffFallback("/screener/custom/")).toBe("/scanner");
    expect(flagOffFallback("/option-chain/plan")).toBe("/options-builder");
    expect(flagOffFallback("/journal/coach")).toBe("/coach");
    for (const p of ["/", "/settings", "/account", "/scanner", "/charts", "/charts/TCS", "/positions", "/nope"]) expect(flagOffFallback(p), p).toBeNull();
  });
});

describe("UI2-1 redirects (only ever asked with ui_v2 on)", () => {
  it("forwards the merged pages' old addresses, keeping the query", () => {
    expect(v2RedirectFor("/scanner", "", signedIn)).toBe("/screener/custom");
    expect(v2RedirectFor("/scanner/", "?preset=gap", signedIn)).toBe("/screener/custom?preset=gap");
    expect(v2RedirectFor("/options-builder", "?underlying=NIFTY", signedIn)).toBe("/option-chain/plan?underlying=NIFTY");
  });

  it("moves Coach under Journal only while Journal exists", () => {
    expect(v2RedirectFor("/coach", "", signedIn)).toBeNull();
    expect(v2RedirectFor("/coach", "", { ...signedIn, on: flags("discipline_engine") })).toBe("/journal/coach");
  });

  it("moves Account into Settings for a signed-in trader only, never with a verify / reset / invite link", () => {
    expect(v2RedirectFor("/account", "", signedIn)).toBe("/settings/account");
    expect(v2RedirectFor("/account", "", { ...signedIn, signedIn: false })).toBeNull();
    for (const q of ["?verify=abc", "?reset=abc", "?invite=abc"]) expect(v2RedirectFor("/account", q, signedIn), q).toBeNull();
  });

  it("leaves every other address alone", () => {
    for (const p of ["/", "/screener", "/screener/custom", "/charts", "/settings", "/positions", "/ai-copilot/today", "/Scanner"]) {
      expect(v2RedirectFor(p, "", { ...signedIn, on: flags("discipline_engine") }), p).toBeNull();
    }
  });
});

describe("UI2-1 broker gate wiring: every data page, and nothing else", () => {
  it("gates every DATA_PAGES route and its sub-paths", () => {
    for (const { path } of DATA_PAGES) {
      expect(isGatedPath(path), path).toBe(true);
      if (path !== "/") expect(isGatedPath(`${path}/x`), `${path}/x`).toBe(true);
    }
    for (const p of ["/charts/NIFTY%2050", "/screener/custom", "/option-chain/plan", "/copilot/market-pulse"]) expect(isGatedPath(p), p).toBe(true);
  });

  it("does not gate settings, account, or the trader's own records", () => {
    for (const p of ["/settings", "/settings/account", "/account", "/orders", "/journal", "/journal/coach", "/coach", "/team", "/notifications", "/risk-management", "/strategies"]) {
      expect(isGatedPath(p), p).toBe(false);
    }
  });
});

describe("UI2-1 review M1: the gate reads the path the router matches (percent-encoded addresses)", () => {
  it("decodes each segment as react-router does, keeping an encoded slash", () => {
    expect(routerPath("/%70ositions")).toEqual({ path: "/positions", malformed: false });
    expect(routerPath("/option%2Dchain")).toEqual({ path: "/option-chain", malformed: false });
    expect(routerPath("/charts/NIFTY%2050")).toEqual({ path: "/charts/NIFTY 50", malformed: false });
    expect(routerPath("/screener%2Fcustom")).toEqual({ path: "/screener%2Fcustom", malformed: false });
    expect(routerPath("/%E0%A4").malformed).toBe(true);
    expect(routerPath("/positions%").malformed).toBe(true);
  });

  it("gates encoded data paths, and fails closed on an address that cannot be decoded", () => {
    for (const p of ["/%70ositions", "/option%2Dchain", "/p%6Frtfolio", "/%63harts", "/%63harts/TCS", "/copilot/%6Darket-pulse", "/%E0%A4", "/positions%", "/charts/%E0"]) {
      expect(isGatedPath(p), p).toBe(true);
    }
    for (const p of ["/%73ettings", "/orders", "/settings%2Faccount"]) expect(isGatedPath(p), p).toBe(false);
  });

  it("names and forwards encoded addresses like the plain ones", () => {
    expect(shellKeyFromPath(routerPath("/%70ositions").path)).toBe("positions");
    expect(shellKeyFromPath(routerPath("/%63harts").path)).toBe("charts");
    expect(v2RedirectFor(routerPath("/%73canner").path, "", signedIn)).toBe("/screener/custom");
    expect(flagOffFallback("/screener/%63ustom")).toBe("/scanner");
    expect(flagOffFallback("/%E0")).toBeNull();
  });
});

describe("UI2-1 review M3: which shell, while the flags are not known yet", () => {
  const base = { v2: false, authLoading: false, signedIn: true, featuresLoaded: true, pathname: "/option-chain" };
  it("v2 once known on; today's shell once known off - exactly as before", () => {
    expect(shellMode({ ...base, v2: true })).toBe("v2");
    expect(shellMode(base)).toBe("today");
    expect(shellMode({ ...base, signedIn: false })).toBe("today");
  });

  it("waits on a neutral screen on a data page until it is known (never today's page with sample data)", () => {
    expect(shellMode({ ...base, featuresLoaded: false })).toBe("wait");
    expect(shellMode({ ...base, authLoading: true, signedIn: false, featuresLoaded: false })).toBe("wait");
    expect(shellMode({ ...base, featuresLoaded: false, pathname: "/%70ositions" })).toBe("wait");
    expect(shellMode({ ...base, featuresLoaded: false, pathname: "/%E0" })).toBe("wait");
    // Not a data page: today's shell at once, as before.
    expect(shellMode({ ...base, featuresLoaded: false, pathname: "/settings" })).toBe("today");
    expect(shellMode({ ...base, authLoading: true, signedIn: false, featuresLoaded: false, pathname: "/orders" })).toBe("today");
  });

  it("a v2-only address goes back to today's address once the flag is known off", () => {
    expect(shellMode({ ...base, pathname: "/settings/account" })).toBe("fallback");
    expect(shellMode({ ...base, featuresLoaded: false, pathname: "/settings/account" })).toBe("wait");
  });
});

describe("UI2-1 review R2-1: the wait is bounded", () => {
  afterEach(() => { vi.useRealTimers(); });
  const pending = { v2: false, authLoading: false, signedIn: true, featuresLoaded: false, pathname: "/positions" };

  it("waits, then falls to today's shell after SHELL_WAIT_MS when the flags never answer", () => {
    vi.useFakeTimers();
    let capped = false;
    const cancel = startWaitCap(() => { capped = true; });
    expect(shellMode({ ...pending, capped })).toBe("wait");
    vi.advanceTimersByTime(SHELL_WAIT_MS - 1);
    expect(shellMode({ ...pending, capped })).toBe("wait");
    vi.advanceTimersByTime(1);
    expect(capped).toBe(true);
    expect(shellMode({ ...pending, capped })).toBe("today");                            // like a failed flags read
    expect(shellMode({ ...pending, authLoading: true, signedIn: false, capped })).toBe("today");   // auth hanging too
    expect(shellMode({ ...pending, pathname: "/settings/account", capped })).toBe("fallback");
    cancel();
  });

  it("flags that arrive before the cap give the v2 shell, and the cap never fires", () => {
    vi.useFakeTimers();
    let capped = false;
    const cancel = startWaitCap(() => { capped = true; });
    vi.advanceTimersByTime(1000);
    expect(shellMode({ ...pending, capped })).toBe("wait");
    cancel();                                                                          // the wait ended: flags known
    expect(shellMode({ ...pending, featuresLoaded: true, v2: true, capped })).toBe("v2");
    vi.advanceTimersByTime(SHELL_WAIT_MS * 2);
    expect(capped).toBe(false);
    expect(SHELL_WAIT_MS).toBe(3000);
  });

  it("the waiting screen shows a visible, neutral loading indicator and no page content", () => {
    const html = renderToStaticMarkup(<ShellWaitingV2 />);
    expect(html).toContain('data-shell="waiting"');
    expect(html).toContain('role="status"');
    expect(html).toContain("animate-spin");
    expect(html).toMatch(/>\s*Loading\s*</);
    expect(html).not.toContain("sr-only");
    expect(html).not.toMatch(/<main|<nav/);
  });
});
