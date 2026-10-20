import { describe, expect, it } from "vitest";
import { DATA_PAGES } from "../ui/dataPages";
import { DEFAULT_SYMBOL, chartsSymbol } from "./ChartsPageV2";
import { V2_ALIASES, chartsPath, flagOffFallback, chartsSymbolFromPath, isGatedPath, shellKeyFromPath, shellTitle, v2RedirectFor } from "./shellRoutes";

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
