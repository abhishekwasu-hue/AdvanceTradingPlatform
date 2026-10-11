import { describe, expect, it } from "vitest";
import css from "../styles/tokens.css?raw";   // vite.config.ts lets the unit tests read this one stylesheet

/** U5: the two new colours carry text (the survivor trail's numbers, the armed chip), so both meet WCAG AA (4.5:1) on
 * every surface they sit on, in every theme and in the colour-blind variant - read from tokens.css itself. */
function block(selector: string): Record<string, number[]> {
  const re = new RegExp(`${selector.replace(/[[\]"=]/g, (c) => `\\${c}`)}\\s*\\{([^}]*)\\}`, "g");
  const out: Record<string, number[]> = {};
  for (const m of css.matchAll(re)) {
    for (const [, name, value] of m[1].matchAll(/--([\w-]+):\s*([\d\s]+);/g)) out[name] = value.trim().split(/\s+/).map(Number);
  }
  return out;
}

function luminance([r, g, b]: number[]): number {
  const ch = (v: number) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
}
function contrast(a: number[], b: number[]): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("U5 tokens", () => {
  const dark = block(":root");
  const light = { ...dark, ...block(':root[data-theme="light"]') };
  const cvdDark = { ...dark, ...block(':root[data-cvd="on"]') };
  const cvdLight = { ...light, ...block(':root[data-theme="light"][data-cvd="on"]') };
  const themes = { dark, light, cvdDark, cvdLight };

  it.each(Object.entries(themes))("%s: signal and armed are AA on every surface, including the inset well", (_, t) => {
    for (const colour of ["signal", "armed"]) {
      for (const surface of ["surface", "surface-1", "surface-2", "surface-inset"]) {
        expect(t[colour], `${colour} defined`).toBeDefined();
        expect(contrast(t[colour], t[surface]), `${colour} on ${surface}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("keeps every meaning a distinct hue in the colour-blind variant (signal and armed move off the new up / down)", () => {
    for (const t of [cvdDark, cvdLight]) {
      const set = new Set(["up", "down", "warn", "signal", "armed"].map((k) => t[k].join(",")));
      expect(set.size).toBe(5);
    }
  });

  it("keeps the shape tokens the screener uses", () => {
    expect(css).toMatch(/--radius-control:\s*6px/);
    expect(css).toMatch(/--radius-panel:\s*10px/);
    expect(css).toMatch(/--stage-h:\s*44px/);
    expect(css).toMatch(/--row-h:\s*36px/);
  });
});
