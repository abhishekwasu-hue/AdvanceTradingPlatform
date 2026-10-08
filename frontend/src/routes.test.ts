import { describe, expect, it } from "vitest";
import { NAV } from "./components/Sidebar";
import { PAGES, pageFromPath, pathFor } from "./routes";

describe("routes", () => {
  it("gives every sidebar entry and the account page a URL that maps back to it", () => {
    const pages = new Set([...NAV.map((n) => n.id), "account" as const]);
    for (const page of pages) {
      expect(PAGES).toContain(page);
      expect(pageFromPath(pathFor(page))).toBe(page);
    }
  });

  it("maps the root to the dashboard, sub-paths to their page and unknown paths to none", () => {
    expect(pathFor("dashboard")).toBe("/");
    expect(pageFromPath("/")).toBe("dashboard");
    expect(pageFromPath("/copilot/market-pulse")).toBe("copilot");
    expect(pageFromPath("/ai-copilot/today")).toBe("copilot");       // the old address, forwarded by the router
    expect(pageFromPath("/settings/")).toBe("settings");
    expect(pageFromPath("/no-such-page")).toBeNull();
    expect(pageFromPath("/Settings")).toBeNull();                    // case-sensitive, like the router
    expect(pageFromPath("/settings/foo")).toBeNull();
    expect(pageFromPath("/ai-copilot/today/x")).toBeNull();
    expect(pageFromPath("/copilot/ask/x")).toBeNull();
  });
});
