import { describe, expect, it } from "vitest";
import css from "../styles/tokens.css?raw";   // vite.config.ts lets the unit tests read this one stylesheet
import { CANDLE_PRESETS } from "../chart/chartTheme";

/**
 * UI2-0 (UI v2 proposal §8 "Accessibility"): every text / background pair a v2 screen uses meets WCAG AA in both
 * themes and in the colour-blind variant, computed from tokens.css itself - so a later token change cannot break it
 * silently. Body text needs 4.5:1; large figures (20 px+ or 14 px+ bold) and icons need 3:1.
 */
function block(selector: string, source: string = css): Record<string, number[]> {
  // At a line start, so `[data-chart-theme="light"]` does not also read `:root[data-cvd="on"] [data-chart-theme="light"]`.
  const re = new RegExp(`(?:^|\\n)${selector.replace(/[[\]"=()]/g, (c) => `\\${c}`)}\\s*\\{([^}]*)\\}`, "g");
  const out: Record<string, number[]> = {};
  for (const m of source.matchAll(re)) {
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

const dark = block(":root");
const light = { ...dark, ...block(':root[data-theme="light"]') };
const cvdDark = { ...dark, ...block(':root[data-cvd="on"]') };
const cvdLight = { ...light, ...block(':root[data-theme="light"][data-cvd="on"]') };
const THEMES = { dark, light, cvdDark, cvdLight };

/** [text, background, minimum ratio]: the pairs v2 uses (proposal §8 colour table). */
const PAIRS: [string, string, number][] = [
  // Text and captions on every surface (fg-muted is "AA on every surface"), and on a warning note.
  ...["surface", "surface-1", "surface-2", "surface-3", "warn-tint"].flatMap((bg): [string, string, number][] => [
    ["fg", bg, 4.5], ["fg-muted", bg, 4.5],
  ]),
  // Meaning colours as body text (P&L, Long / Short, links, warnings) on the page and on cards.
  ...["surface", "surface-1"].flatMap((bg): [string, string, number][] => [
    ["brand", bg, 4.5], ["up", bg, 4.5], ["down", bg, 4.5], ["warn", bg, 4.5],
  ]),
  // On chips and hovered rows (surface-2) the meaning colours are used for large figures and icons only.
  ["brand", "surface-2", 3], ["up", "surface-2", 3], ["down", "surface-2", 3], ["warn", "surface-2", 3],
  // A warning note: amber text on its tint.
  ["warn", "warn-tint", 4.5],
  // Primary buttons: white on the brand fill, at rest and on hover.
  ["on-brand", "brand-fill", 4.5], ["on-brand", "brand-fill-strong", 4.5],
];

describe("UI2-0 v2 tokens", () => {
  it.each(Object.entries(THEMES))("%s: every v2 text / background pair meets WCAG AA", (_, t) => {
    for (const [fg, bg, min] of PAIRS) {
      expect(t[fg], `--${fg} defined`).toBeDefined();
      expect(t[bg], `--${bg} defined`).toBeDefined();
      expect(contrast(t[fg], t[bg]), `${fg} on ${bg}`).toBeGreaterThanOrEqual(min);
    }
  });

  it("adds border-subtle and warn-tint in both themes, without changing an existing token", () => {
    expect(dark["border-subtle"]).toEqual([26, 33, 48]);
    expect(light["border-subtle"]).toEqual([229, 233, 240]);
    expect(dark["warn-tint"]).toEqual([41, 30, 10]);
    expect(light["warn-tint"]).toEqual([255, 251, 235]);
    // The values the proposal's colour table reads as "existing" (§8): unchanged.
    expect(light.surface).toEqual([244, 246, 249]);
    expect(light["surface-1"]).toEqual([255, 255, 255]);
    expect(light.border).toEqual([211, 217, 227]);
    expect(light.fg).toEqual([15, 23, 42]);
    expect(dark.surface).toEqual([9, 12, 18]);
    expect(dark.brand).toEqual([59, 130, 246]);
  });

  it("the subtle outline is quieter than the input border, in both themes", () => {
    for (const t of [dark, light]) {
      expect(contrast(t["border-subtle"], t["surface-1"])).toBeLessThan(contrast(t.border, t["surface-1"]));
    }
  });

  it("defines the shape, spacing and motion tokens", () => {
    expect(css).toMatch(/--radius-pill:\s*999px/);
    expect(css).toMatch(/--shadow-raised:\s*0 8px 24px rgba\(0, 0, 0, 0\.5\)/);
    expect(css).toMatch(/--shadow-raised:\s*0 8px 24px rgba\(15, 23, 42, 0\.12\)/);
    for (const [n, px] of [[1, 4], [2, 8], [3, 12], [4, 16], [5, 20], [6, 24], [8, 32], [10, 40], [12, 48]]) {
      // item 1: in rem (the px at the Normal text size / 16), so spacing grows with the Text size setting
      expect(css).toMatch(new RegExp(`--space-${n}:\\s*${px / 16}rem;`));
    }
    expect(css).toMatch(/--dur-fast:\s*120ms/);
    expect(css).toMatch(/--dur-base:\s*200ms/);
    expect(css).toMatch(/--ease:\s*cubic-bezier\(0\.2, 0, 0, 1\)/);
  });

  it("turns motion off when the trader or the system reduces it", () => {
    expect(css).toMatch(/:root\[data-motion="reduce"\]\s*\{\s*--dur-fast:\s*0ms;\s*--dur-base:\s*0ms;\s*\}/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{\s*:root\s*\{\s*--dur-fast:\s*0ms;\s*--dur-base:\s*0ms;/);
  });
});

/** UI2-4b1 (Charts page plan §3): the chart tokens. A chart follows the app theme or carries its own
 * (`data-chart-theme`), so each chart theme is checked as the app's own and as a scoped one inside the other app theme,
 * each also in the colour-blind variant. Everything is measured against the chart's solid background. */
describe("UI2-4b1 chart tokens", () => {
  const scoped = (selector: string) => block(selector);
  const CHART_THEMES = {
    "app dark": dark,
    "app light": light,
    "app dark, colour-blind": cvdDark,
    "app light, colour-blind": cvdLight,
    "chart dark in a light app": { ...light, ...scoped('[data-chart-theme="dark"]') },
    "chart light in a dark app": { ...dark, ...scoped('[data-chart-theme="light"]') },
    "chart dark in a light app, colour-blind": { ...cvdLight, ...scoped('[data-chart-theme="dark"]'), ...scoped(':root[data-cvd="on"] [data-chart-theme="dark"]') },
    "chart light in a dark app, colour-blind": { ...cvdDark, ...scoped('[data-chart-theme="light"]'), ...scoped(':root[data-cvd="on"] [data-chart-theme="light"]') },
  };
  const WHITE = [255, 255, 255];
  /** [token, minimum ratio on --chart-bg]: axis text 4.5; lines, candles, overlays and the crosshair label 3. */
  const ON_BG: [string, number][] = [
    ["chart-text", 4.5],
    ...["chart-crosshair", "chart-crosshair-label", "chart-drawing", "chart-rsi", "chart-rsi-band", "chart-up", "chart-down",
        "chart-ov-1", "chart-ov-2", "chart-ov-3", "chart-ov-4"].map((t): [string, number] => [t, 3]),
  ];

  it.each(Object.entries(CHART_THEMES))("%s: every chart colour meets AA on the chart background", (_, t) => {
    for (const [token, min] of ON_BG) {
      expect(t[token], `--${token} defined`).toBeDefined();
      expect(contrast(t[token], t["chart-bg"]), `${token} on chart-bg`).toBeGreaterThanOrEqual(min);
    }
    // The crosshair's axis labels: white text on --chart-crosshair-label.
    expect(contrast(WHITE, t["chart-crosshair-label"]), "label text").toBeGreaterThanOrEqual(4.5);
  });

  it("a scoped chart theme repeats the app theme's values, so a chart looks the same either way", () => {
    for (const theme of ["dark", "light"]) {
      expect(Object.keys(scoped(`[data-chart-theme="${theme}"]`))).toHaveLength(15);      // every chart token
      expect(Object.keys(scoped(`:root[data-cvd="on"] [data-chart-theme="${theme}"]`))).toEqual(["chart-up", "chart-down"]);
    }
    expect({ ...dark, ...scoped('[data-chart-theme="dark"]') }).toEqual(dark);
    expect({ ...light, ...scoped('[data-chart-theme="light"]') }).toEqual(light);
    expect({ ...cvdDark, ...scoped('[data-chart-theme="dark"]'), ...scoped(':root[data-cvd="on"] [data-chart-theme="dark"]') }).toEqual(cvdDark);
    expect({ ...cvdLight, ...scoped('[data-chart-theme="light"]'), ...scoped(':root[data-cvd="on"] [data-chart-theme="light"]') }).toEqual(cvdLight);
  });

  it("candles use the meaning colours, the background is solid in both themes, and no existing token changes", () => {
    for (const t of [dark, light, cvdDark, cvdLight]) {
      expect(t["chart-up"]).toEqual(t.up);
      expect(t["chart-down"]).toEqual(t.down);
    }
    expect(dark["chart-bg"]).toEqual(dark.surface);
    expect(light["chart-bg"]).toEqual([255, 255, 255]);
    expect(dark["chart-grid"]).toEqual([26, 35, 51]);
    expect(light["chart-text"]).toEqual([71, 85, 105]);
  });

  it("every candle colour preset is AA (3 : 1) on the light and the dark chart background, up and down apart", () => {
    const hex = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    for (const preset of CANDLE_PRESETS) {
      if (!preset.colors) continue;                                      // the theme's own colours: checked above
      for (const bg of [dark["chart-bg"], light["chart-bg"]]) {
        expect(contrast(hex(preset.colors.up), bg), `${preset.id} up`).toBeGreaterThanOrEqual(3);
        expect(contrast(hex(preset.colors.down), bg), `${preset.id} down`).toBeGreaterThanOrEqual(3);
      }
      expect(preset.colors.up).not.toBe(preset.colors.down);
    }
  });
});

/** Chart + colour order, Part 3 (docs/design/CHART_COLOUR_REVIEW.md R5-R8, OPEN_QUESTIONS CC-1..CC-4): colour by
 * meaning. The new tokens, the v2-only navy dark surfaces and the colour-blind values, all read from tokens.css. */
describe("Part 3 colour tokens", () => {
  const V2_SCOPE = ':root[data-shell="v2"]:not([data-theme="light"])';
  const v2Dark = { ...dark, ...block(V2_SCOPE) };
  const v2DarkCvd = { ...cvdDark, ...block(V2_SCOPE) };
  const ALL = { ...THEMES, v2Dark, v2DarkCvd };
  const NEW = ["range", "range-track", "info-tint", "ai-tint"];

  /** [token, background, minimum]: text 4.5 : 1, UI parts (the range marker, icons on a raised chip) 3 : 1. */
  const NEW_PAIRS: [string, string, number][] = [
    // Information and AI text on the page and on cards, and on their own chip tint.
    ...["surface", "surface-1"].flatMap((bg): [string, string, number][] => [["info", bg, 4.5], ["ai", bg, 4.5]]),
    ["info", "info-tint", 4.5], ["ai", "ai-tint", 4.5], ["fg", "info-tint", 4.5], ["fg", "ai-tint", 4.5],
    ["info", "surface-2", 3], ["ai", "surface-2", 3],
    // The range marker is a UI part: 3 : 1 on every surface a card uses and on its own track.
    ...["surface", "surface-1", "surface-2", "range-track"].map((bg): [string, string, number] => ["range", bg, 3]),
  ];

  it.each(Object.entries(ALL))("%s: every new pair meets WCAG AA (text 4.5, UI parts 3)", (_, t) => {
    for (const [fg, bg, min] of NEW_PAIRS) {
      expect(t[fg], `--${fg} defined`).toBeDefined();
      expect(t[bg], `--${bg} defined`).toBeDefined();
      expect(contrast(t[fg], t[bg]), `${fg} on ${bg}`).toBeGreaterThanOrEqual(min);
    }
  });

  it.each([["v2Dark", v2Dark], ["v2DarkCvd", v2DarkCvd]])("%s: the navy surfaces keep every existing v2 pair AA", (_, t) => {
    for (const [fg, bg, min] of PAIRS) expect(contrast(t[fg], t[bg]), `${fg} on ${bg}`).toBeGreaterThanOrEqual(min);
    // Layered surfaces: each step is lighter than the one below it, so cards separate without a border.
    const steps = ["surface-inset", "surface", "surface-1", "surface-2", "surface-3"].map((k) => luminance(t[k]));
    for (let i = 1; i < steps.length; i += 1) expect(steps[i]).toBeGreaterThan(steps[i - 1]);
    expect(contrast(t["border-subtle"], t["surface-1"])).toBeLessThan(contrast(t.border, t["surface-1"]));
  });

  it("the navy surfaces are v2-only and dark-only: surfaces and outlines move, nothing else", () => {
    expect(Object.keys(block(V2_SCOPE)).sort()).toEqual(
      ["border", "border-subtle", "glass", "range-track", "surface", "surface-1", "surface-2", "surface-3", "surface-inset"]);
    expect(v2Dark.surface).not.toEqual(dark.surface);              // a real change for the v2 shell ...
    expect(v2Dark["chart-bg"]).toEqual(dark["chart-bg"]);          // ... but the chart waits for the comparison (CC-3)
    expect(v2Dark.brand).toEqual(dark.brand);                      // the brand accent stays ATP blue
    expect(css).not.toMatch(/:root\[data-shell="v2"\]\[data-theme="light"\]/);
  });

  it("adds the new tokens in both themes, and changes no existing value of the normal themes", () => {
    const before = css.split("/* Chart + colour order, Part 3")[0];
    const oldDark = block(":root", before);
    const oldLight = { ...oldDark, ...block(':root[data-theme="light"]', before) };
    for (const name of NEW) {
      expect(oldDark[name], `--${name} is new`).toBeUndefined();
      expect(dark[name], `--${name} dark`).toBeDefined();
      expect(block(':root[data-theme="light"]')[name], `--${name} light`).toBeDefined();
    }
    for (const [name, value] of Object.entries(oldDark)) expect(dark[name], name).toEqual(value);
    for (const [name, value] of Object.entries(oldLight)) expect(light[name], name).toEqual(value);
    // The brand accent stays ATP blue; PAPER gets no hue of its own (CC-2), and armed keeps its meaning.
    expect(dark.brand).toEqual([59, 130, 246]);
    expect(light.brand).toEqual([37, 99, 235]);
    expect(css).not.toMatch(/--paper\b/);
    // A range is not a warning: its own token, not an alias of warn.
    for (const t of [dark, light, cvdDark, cvdLight]) expect(t.range).not.toEqual(t.warn);
  });

  // "Every meaning keeps a distinct hue" (tokens.css; review, Part 3 notes). Measured as the OKLab distance; in the
  // colour-blind variants after a deuteranopia simulation (Viénot 1999), because that is how those traders see them.
  const linear = (v: number) => { const s = v / 255; return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  function oklab(rgb: number[]): number[] {
    const [r, g, b] = rgb.map(linear);
    const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
    const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
      0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
  }
  function deuteranopia(rgb: number[]): number[] {
    const [r, g, b] = rgb.map(linear);
    const out = [0.367322 * r + 0.860646 * g - 0.227968 * b, 0.280085 * r + 0.672501 * g + 0.047413 * b,
      -0.01182 * r + 0.04294 * g + 0.968881 * b];
    return out.map((x) => { const c = Math.min(1, Math.max(0, x)); return 255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055); });
  }
  const distance = (a: number[], b: number[]) => { const [p, q] = [oklab(a), oklab(b)]; return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]); };
  const MEANINGS = ["up", "down", "warn", "info", "ai"];
  const pairs = [
    ...MEANINGS.flatMap((a, i) => MEANINGS.slice(i + 1).map((b) => [a, b])),
    // The range marker is warn's family by design (the order groups "warn / range"), but never profit, loss, info or AI.
    ...["up", "down", "info", "ai"].map((b) => ["range", b]),
  ];
  it.each([["dark", dark, false], ["light", light, false], ["v2Dark", v2Dark, false],
    ["cvdDark", cvdDark, true], ["cvdLight", cvdLight, true], ["v2DarkCvd", v2DarkCvd, true]] as const)(
    "%s: every meaning keeps a distinct hue", (_, t, cvd) => {
      const see = cvd ? deuteranopia : (c: number[]) => c;
      const min = cvd ? 0.06 : 0.08;
      for (const [a, b] of pairs) expect(distance(see(t[a]), see(t[b])), `${a} vs ${b}`).toBeGreaterThanOrEqual(min);
    });

  it("the distance check can fail: the light warn and the colour-blind light loss were the same colour", () => {
    const deut = (c: number[]) => deuteranopia(c);
    expect(distance(deut([180, 83, 9]), deut(cvdLight.down))).toBeLessThan(0.01);   // the old light warn
    expect(distance(deut(cvdLight.warn), deut(cvdLight.down))).toBeGreaterThanOrEqual(0.06);
  });
});
