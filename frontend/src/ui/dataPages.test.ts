import { describe, expect, it } from "vitest";
import { PAGES, pathFor } from "../routes";
import { shellKeyFromPath } from "../shell/shellRoutes";
import { DATA_PAGES, isDataPath } from "./dataPages";

describe("UI2-0 data pages (the broker gate's list for UI2-1)", () => {
  it("names real routes only (/charts is the v2 shell's, UI2-1)", () => {
    const known = new Set(PAGES.map(pathFor));
    for (const { path, why } of DATA_PAGES) {
      expect(known.has(path) || shellKeyFromPath(path) !== null, path).toBe(true);
      expect(why.length, path).toBeGreaterThan(10);
    }
    expect(new Set(DATA_PAGES.map((p) => p.path)).size).toBe(DATA_PAGES.length);
  });

  it("covers the pages that show market data today", () => {
    for (const p of ["/", "/option-chain", "/screener", "/scanner", "/signals", "/positions", "/portfolio"]) expect(isDataPath(p), p).toBe(true);
  });

  it("matches sub-paths and trailing slashes, and leaves settings, account and the trader's own records alone", () => {
    expect(isDataPath("/copilot/market-pulse")).toBe(true);
    expect(isDataPath("/charts/NIFTY")).toBe(true);
    expect(isDataPath("/positions/")).toBe(true);
    for (const p of ["/settings", "/account", "/team", "/notifications", "/journal", "/orders", "/risk-management", "/legal/terms", "/status"]) {
      expect(isDataPath(p), p).toBe(false);
    }
    expect(isDataPath("/screener-old")).toBe(false);
  });
});
