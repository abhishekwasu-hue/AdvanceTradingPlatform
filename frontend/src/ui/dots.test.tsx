import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import script from "../../public/theme-init.js?raw";
import { loadAppearance } from "../theme";

/** UX-12 (owner, 27 Oct, "dots yes"): the soft purple dot grid behind the top of the v2 pages. */
afterEach(() => vi.unstubAllGlobals());

function initAttrs(saved: unknown): Record<string, string> {
  const attrs: Record<string, string> = {};
  vi.stubGlobal("document", { documentElement: { setAttribute: (k: string, v: string) => { attrs[k] = v; }, classList: { add() {}, remove() {} } } });
  vi.stubGlobal("localStorage", { getItem: (k: string) => (k === "atp_appearance" ? JSON.stringify(saved) : null) });
  vi.stubGlobal("window", { matchMedia: () => ({ matches: false }) });
  new Function(script)();
  return attrs;
}

describe("UX-12 dotted background", () => {
  it("on unless the trader turned it off", () => {
    const store = (v: unknown) => vi.stubGlobal("localStorage", { getItem: () => JSON.stringify(v) });
    store({});
    expect(loadAppearance().dots).toBe(true);
    store({ dots: false });
    expect(loadAppearance().dots).toBe(false);
    store({ dots: "nonsense" });
    expect(loadAppearance().dots).toBe(true);
  });

  it("the first paint already has it (no flash when the app starts)", () => {
    expect(initAttrs({})["data-dots"]).toBe("on");
    expect(initAttrs({ dots: false })["data-dots"]).toBe("off");
  });

  it("tokens only, hidden when off and in high contrast", () => {
    const css = readFileSync("src/styles/tokens.css", "utf8");
    expect(css).toMatch(/\.dot-backdrop \{[^}]*radial-gradient\(rgb\(var\(--accent-2\)/);
    expect(css).toMatch(/:root\[data-dots="off"\] \.dot-backdrop/);
    expect(css).toMatch(/:root\[data-contrast="high"\] \.dot-backdrop/);
  });

  it("Settings -> Appearance offers the switch with the redesigned pages, on by default", async () => {
    vi.resetModules();
    vi.doMock("../ui/useFeatures", () => ({ useUiV2: () => true }));
    vi.doMock("../auth/AuthContext", () => ({ useOptionalAuth: () => null }));
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
    const { default: AppearanceCard } = await import("../components/AppearanceCard");
    const html = renderToStaticMarkup(<AppearanceCard />);
    expect(html).toMatch(/data-testid="appearance-dots"[^>]*checked=""/);
    expect(html).toContain("Dotted background");
    vi.doUnmock("../ui/useFeatures");
    vi.doUnmock("../auth/AuthContext");
  });
});
