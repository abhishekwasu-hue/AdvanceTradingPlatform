import { afterEach, describe, expect, it, vi } from "vitest";
import script from "../public/theme-init.js?raw";
import { chartColors, resolveChartColor } from "./theme";

// The tests run without a DOM: stub just what theme.ts reads.
function stubTokens(tokens: Record<string, string>) {
  vi.stubGlobal("document", { documentElement: {} });
  vi.stubGlobal("getComputedStyle", () => ({ getPropertyValue: (n: string) => tokens[n] ?? "" }));
}

afterEach(() => vi.unstubAllGlobals());

describe("chart colours follow the theme tokens", () => {
  it("resolves a meaning to the current token, so a colour-blind change recolours overlays", () => {
    stubTokens({ "--up": "86 180 233", "--down": "249 115 22", "--fg": "15 23 42" });
    expect(resolveChartColor("up")).toBe("rgb(86, 180, 233)");
    expect(resolveChartColor("down")).toBe("rgb(249, 115, 22)");
    expect(resolveChartColor("fg")).toBe("rgb(15, 23, 42)");
    expect(chartColors().upSoft).toBe("rgba(86, 180, 233, 0.45)");
  });

  it("passes a literal colour through unchanged (strategy identity colours)", () => {
    stubTokens({});
    expect(resolveChartColor("#38bdf8")).toBe("#38bdf8");
  });

  it("never hands the chart an empty colour when the stylesheet is missing", () => {
    stubTokens({});
    for (const v of Object.values(chartColors())) expect(v).not.toBe("");
  });
});

describe("theme-init.js (runs before the first paint)", () => {
  function run(saved: string | null, prefersLight = false) {
    const attrs: Record<string, string> = {};
    const classes = new Set<string>();
    const root = { setAttribute: (k: string, v: string) => { attrs[k] = v; },
                   classList: { add: (c: string) => classes.add(c), remove: (c: string) => classes.delete(c) } };
    vi.stubGlobal("document", { documentElement: root });
    vi.stubGlobal("localStorage", { getItem: (k: string) => (k === "atp_appearance" ? saved : null) });
    vi.stubGlobal("window", { matchMedia: () => ({ matches: prefersLight }) });
    new Function(script)();
    return { theme: attrs["data-theme"], cvd: attrs["data-cvd"], dark: classes.has("dark") };
  }

  it("applies the saved light theme and colour-blind option", () => {
    expect(run(JSON.stringify({ theme: "light", colorBlind: true }))).toEqual({ theme: "light", cvd: "on", dark: false });
  });
  it("follows the device when asked to", () => {
    expect(run(JSON.stringify({ theme: "system" }), true)).toEqual({ theme: "light", cvd: "off", dark: false });
    expect(run(JSON.stringify({ theme: "system" }), false)).toEqual({ theme: "dark", cvd: "off", dark: true });
  });
  it("falls back to dark on nothing saved or a corrupt value", () => {
    expect(run(null)).toEqual({ theme: "dark", cvd: "off", dark: true });
    expect(run("{not json")).toEqual({ theme: "dark", cvd: "off", dark: true });
  });
});

describe("UI2-0 (UI-1): with UI v2 on, the theme follows the system, light when unknown", () => {
  function run(saved: string | null, opts: { v2: boolean; system: "dark" | "light" | "unknown" | "none" }) {
    const attrs: Record<string, string> = {};
    const root = { setAttribute: (k: string, v: string) => { attrs[k] = v; }, classList: { add: () => undefined, remove: () => undefined } };
    vi.stubGlobal("document", { documentElement: root });
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => (k === "atp_appearance" ? saved : k === "atp_ui_v2_theme" && opts.v2 ? "system" : null),
    });
    const matchMedia = (q: string) => ({ matches: opts.system !== "unknown" && q.includes(opts.system) });
    vi.stubGlobal("window", opts.system === "none" ? {} : { matchMedia });
    new Function(script)();
    return attrs["data-theme"];
  }

  it("theme-init.js: no saved theme follows the system, light when the system says nothing", () => {
    expect(run(null, { v2: true, system: "dark" })).toBe("dark");
    expect(run(null, { v2: true, system: "light" })).toBe("light");
    expect(run(null, { v2: true, system: "unknown" })).toBe("light");
    expect(run(null, { v2: true, system: "none" })).toBe("light");
    expect(run("{not json", { v2: true, system: "unknown" })).toBe("light");
  });

  it("theme-init.js: a theme the trader chose is kept", () => {
    expect(run(JSON.stringify({ theme: "dark" }), { v2: true, system: "light" })).toBe("dark");
    expect(run(JSON.stringify({ theme: "light" }), { v2: true, system: "dark" })).toBe("light");
  });

  it("theme-init.js: with the flag off (no hint) nothing changes - dark by default, the old 'system' rule", () => {
    expect(run(null, { v2: false, system: "light" })).toBe("dark");
    expect(run(null, { v2: false, system: "unknown" })).toBe("dark");
    expect(run(JSON.stringify({ theme: "system" }), { v2: false, system: "unknown" })).toBe("dark");
  });

  it("theme.ts: the shell's switch changes only the default, and only while it is on", async () => {
    const { loadAppearance, resolvedTheme, setV2ThemeDefault } = await import("./theme");
    let saved: string | null = null;
    const store: Record<string, string> = {};
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => (k === "atp_appearance" ? saved : store[k] ?? null),
      setItem: (k: string, v: string) => { store[k] = v; },
      removeItem: (k: string) => { delete store[k]; },
    });
    vi.stubGlobal("window", { matchMedia: () => ({ matches: false }) });    // the system says nothing
    expect(resolvedTheme(loadAppearance())).toBe("dark");
    setV2ThemeDefault(true);
    expect(store.atp_ui_v2_theme).toBe("system");
    expect(loadAppearance().theme).toBe("system");
    expect(resolvedTheme(loadAppearance())).toBe("light");
    saved = JSON.stringify({ theme: "dark" });
    expect(resolvedTheme(loadAppearance())).toBe("dark");                 // the trader's choice is kept
    saved = null;
    setV2ThemeDefault(false);
    expect(store.atp_ui_v2_theme).toBeUndefined();
    expect(resolvedTheme(loadAppearance())).toBe("dark");
  });
});
