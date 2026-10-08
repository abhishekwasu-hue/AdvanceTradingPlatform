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
