import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import script from "../../public/theme-init.js?raw";
import { isDensity, loadAppearance } from "../theme";

/** UX-8: Comfortable / Compact density in Settings -> Appearance. */
afterEach(() => vi.unstubAllGlobals());

function initAttrs(saved: unknown): Record<string, string> {
  const attrs: Record<string, string> = {};
  vi.stubGlobal("document", { documentElement: { setAttribute: (k: string, v: string) => { attrs[k] = v; }, classList: { add() {}, remove() {} } } });
  vi.stubGlobal("localStorage", { getItem: (k: string) => (k === "atp_appearance" ? JSON.stringify(saved) : null) });
  vi.stubGlobal("window", { matchMedia: () => ({ matches: false }) });
  new Function(script)();
  return attrs;
}

describe("UX-8 density", () => {
  it("Comfortable unless Compact was saved; anything else reads as Comfortable", () => {
    expect(isDensity("compact")).toBe(true);
    expect(isDensity("tiny")).toBe(false);
    const store = (v: unknown) => vi.stubGlobal("localStorage", { getItem: () => JSON.stringify(v) });
    store({ density: "compact" });
    expect(loadAppearance().density).toBe("compact");
    store({ density: "tiny" });
    expect(loadAppearance().density).toBe("comfortable");
    store({});
    expect(loadAppearance().density).toBe("comfortable");
  });

  it("the first paint already has the saved density (no jump when the app starts)", () => {
    expect(initAttrs({ density: "compact" })["data-density"]).toBe("compact");
    expect(initAttrs({})["data-density"]).toBe("comfortable");
  });

  it("Compact tightens the space tokens and rows only; touch targets are not space tokens", () => {
    const css = readFileSync("src/styles/tokens.css", "utf8");
    const block = /:root\[data-density="compact"\] \{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(block).toMatch(/--space-4: 0\.75rem;/);
    expect(block).toMatch(/--row-h: 2rem;/);
    expect(block).not.toMatch(/--space-1:/);                 // the smallest gap stays
  });

  it("Settings -> Appearance offers it with the redesigned pages", async () => {
    vi.resetModules();
    vi.doMock("../ui/useFeatures", () => ({ useUiV2: () => true }));
    vi.doMock("../auth/AuthContext", () => ({ useOptionalAuth: () => null }));
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
    const { default: AppearanceCard } = await import("../components/AppearanceCard");
    const html = renderToStaticMarkup(<AppearanceCard />);
    expect(html).toContain('data-appearance="density"');
    expect(html).toMatch(/aria-pressed="true"[^>]*>Comfortable</);
    expect(html).toContain(">Compact<");
    vi.doUnmock("../ui/useFeatures");
    vi.doUnmock("../auth/AuthContext");
  });
});
