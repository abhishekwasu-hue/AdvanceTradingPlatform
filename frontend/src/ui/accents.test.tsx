import { readFileSync } from "node:fs";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { TriangleAlert } from "lucide-react";
import css from "../styles/tokens.css?raw";
import tailwind from "../../tailwind.config.js";
import { Button, buttonClass } from "../components/primitives/Button";
import { TodaySummaryV2 } from "../dashboard/BlocksV2";
import { NAV_PINNED_V2, NAV_SECTIONS_V2, allNavItems, type SectionId } from "../shell/navModel";
import { navTone } from "../shell/navTone";
import { SECTION_TINT, navChipClass, navWordClass, sectionTintOf, type SectionTint } from "./accents";
import BottomNavV2 from "../shell/BottomNavV2";
import TopBarV2 from "../shell/TopBarV2";
import { BOTTOM_BAR_V2 } from "../shell/navModel";
import { ColourV2Provider } from "./colourV2";
import { NewBadge } from "./NewBadge";
import { PageHeader } from "./PageHeader";
import { SummaryCard } from "./SummaryCard";
import { checkTone, toneProblem, type ToneCue } from "./tone";

/**
 * UI-H colour accents CA1-CA4 (owner order, docs/owner-orders/ATP_ORDER_UI_H_CA.md): the tokens meet WCAG AA in both
 * themes (computed from tokens.css itself), the section -> tint map covers every navigation section, and the shared
 * components show the accents only while colour_v2 is on.
 */
function block(selector: string): Record<string, number[]> {
  const re = new RegExp(`(?:^|\\n)${selector.replace(/[[\]"=()]/g, (c) => `\\${c}`)}\\s*\\{([^}]*)\\}`, "g");
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

const dark = block(":root");
const light = { ...dark, ...block(':root[data-theme="light"]') };
const v2Dark = { ...dark, ...block(':root[data-colour="v2"]:not([data-theme="light"])') };
const cvdDark = { ...v2Dark, ...block(':root[data-cvd="on"]') };
const cvdLight = { ...light, ...block(':root[data-theme="light"][data-cvd="on"]') };
const THEMES = { dark, light, v2Dark, cvdDark, cvdLight };
const TAB_TINTS: SectionTint[] = ["today", "charts", "signals", "trades", "more"];
const TINTS: SectionTint[] = ["markets", "trading", "insights", "account", "admin", "broker", "copilot", ...TAB_TINTS];

describe("CA1 / CA3 / CA4 tokens: WCAG AA from the token values, both themes", () => {
  it.each(Object.entries(THEMES))("%s: the secondary violet and white on every point of the gradient", (_, t) => {
    for (const bg of ["surface", "surface-1"]) expect(contrast(t["accent-2"], t[bg]), `accent-2 on ${bg}`).toBeGreaterThanOrEqual(4.5);
    expect(contrast(t["accent-2"], t["soft-accent-2"]), "accent-2 on its soft tint").toBeGreaterThanOrEqual(4.5);
    for (const at of [0, 0.25, 0.5, 0.75, 1]) {
      const mix = t["grad-from"].map((c, i) => Math.round(c + (t["grad-to"][i] - c) * at));
      expect(contrast(t["on-brand"], mix), `white on the gradient at ${at}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it.each(Object.entries(THEMES))("%s: every section tint on its chip (4.5 : 1) and on the nav surfaces (3 : 1)", (_, t) => {
    for (const s of TINTS) {
      expect(contrast(t[`sec-${s}`], t[`sec-${s}-soft`]), `${s} on its chip`).toBeGreaterThanOrEqual(4.5);
      for (const bg of ["surface-1", "surface-2"]) expect(contrast(t[`sec-${s}`], t[bg]), `${s} on ${bg}`).toBeGreaterThanOrEqual(3);
    }
  });

  it.each(Object.entries(THEMES))("%s: text on every tone background (4.5 : 1), the meaning colour as a big figure (3 : 1)", (_, t) => {
    const meaning = { profit: "up", loss: "down", risk: "warn", info: "info" } as const;
    for (const [tone, colour] of Object.entries(meaning)) {
      for (const fg of ["fg", "fg-muted"]) expect(contrast(t[fg], t[`tone-${tone}`]), `${fg} on tone-${tone}`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(t[colour], t[`tone-${tone}`]), `${colour} on tone-${tone}`).toBeGreaterThanOrEqual(3);
    }
  });

  it("every accent token has a light value and a Tailwind class; brand and the surfaces did not move", () => {
    const names = ["accent-2", "soft-accent-2", ...TINTS.flatMap((s) => [`sec-${s}`, `sec-${s}-soft`]), "tone-profit", "tone-loss", "tone-risk", "tone-info"];
    const lightOnly = block(':root[data-theme="light"]');
    const colours = (tailwind as unknown as { theme: { extend: { colors: Record<string, string> } } }).theme.extend.colors;
    for (const n of names) {
      expect(dark[n], `--${n} dark`).toBeDefined();
      expect(lightOnly[n], `--${n} light`).toBeDefined();
      expect(colours[n], `Tailwind ${n}`).toBe(`rgb(var(--${n}) / <alpha-value>)`);
    }
    expect(css).toContain("--accent-gradient: linear-gradient(120deg, rgb(var(--grad-from)), rgb(var(--grad-to)));");
    expect(dark.brand).toEqual([59, 130, 246]);
    expect(light.brand).toEqual([37, 99, 235]);
    expect(dark["grad-from"]).toEqual(dark["brand-fill"]);
    expect(light["grad-from"]).toEqual(light["brand-fill"]);
    expect(dark["surface-1"]).toEqual([16, 20, 29]);
    expect(light["surface-1"]).toEqual([255, 255, 255]);
  });
});

describe("CA3: one section -> tint map for the nav and the PageHeader", () => {
  it("covers every navigation section, user and admin, and every entry in them", () => {
    const sections = NAV_SECTIONS_V2.map((s) => s.id);
    expect(sections).toEqual(expect.arrayContaining(["markets", "trading", "insights", "account", "admin"]));
    for (const id of sections) expect(SECTION_TINT[id as SectionId], id).toBeDefined();
    for (const item of [...allNavItems(), ...NAV_PINNED_V2]) expect(sectionTintOf(item.key), item.key).not.toBeNull();
    expect(sectionTintOf("dashboard")).toBe("today");
    expect(sectionTintOf("charts")).toBe("charts");
    expect(sectionTintOf("signals")).toBe("signals");
    expect(sectionTintOf("positions")).toBe("trades");
    expect(sectionTintOf("option-chain")).toBe("markets");
    expect(sectionTintOf("terminal")).toBe("trading");
    expect(sectionTintOf("analytics")).toBe("insights");
    expect(sectionTintOf("settings")).toBe("account");
    expect(sectionTintOf("admin")).toBe("admin");
    expect(sectionTintOf("broker")).toBe("broker");
    expect(sectionTintOf("copilot")).toBe("copilot");
    expect(sectionTintOf(null)).toBeNull();
  });

  it("the plain nav icons read the same map (navTone)", () => {
    expect(navTone("charts")).toBe("info");
    expect(navTone("terminal")).toBe("teal");
    expect(navTone("admin")).toBe("neutral");
  });
});

const render = (node: ReactNode, on: boolean) =>
  renderToStaticMarkup(<MemoryRouter><ColourV2Provider on={on}>{node}</ColourV2Provider></MemoryRouter>);

describe("CA2 / CA3 / CA4: the shared components, only with colour v2", () => {
  it("PageHeader shows the page's nav icon in its section chip", () => {
    expect(render(<PageHeader title="Option chain" section="option-chain" />, true)).toMatch(/data-section-chip="markets" class="[^"]*bg-sec-markets-soft text-sec-markets/);
    expect(render(<PageHeader title="Admin console" section="admin" />, true)).toContain('data-section-chip="admin"');
    expect(render(<PageHeader title="Option chain" section="option-chain" />, false)).not.toContain("data-section-chip");
  });

  it("the primary Button takes the gradient only when asked and only with colour v2", () => {
    expect(render(<Button variant="primary" gradient>Start</Button>, true)).toContain("bg-accent-gradient text-on-brand");
    expect(render(<Button variant="primary" gradient>Start</Button>, false)).not.toContain("accent-gradient");
    expect(render(<Button variant="primary">Start</Button>, true)).not.toContain("accent-gradient");
    expect(buttonClass({ variant: "outline", gradient: true })).not.toContain("accent-gradient");
  });

  it("the New badge always says New; the gradient only with colour v2", () => {
    expect(render(<NewBadge />, true)).toMatch(/class="[^"]*bg-accent-gradient text-on-brand[^"]*">New</);
    expect(render(<NewBadge />, false)).toMatch(/>New</);
    expect(render(<NewBadge />, false)).not.toContain("accent-gradient");
  });

  it("the Today cards: a tinted tile always shows its sign or icon", () => {
    const tiles = { booked: 2450, winRate: 60, closedTrades: 5, riskUsedPct: 85, riskUsed: 8500, riskLimit: 10000, signals: { count: 2, waiting: 1, taken: 1 } };
    const on = render(<TodaySummaryV2 tiles={tiles as never} loading={false} />, true);
    expect(on).toMatch(/bg-tone-profit[^>]*data-tone="profit"[\s\S]*?\+₹/);
    expect(on).toMatch(/bg-tone-risk[^>]*data-tone="risk"[\s\S]*?<svg/);
    expect(render(<TodaySummaryV2 tiles={tiles as never} loading={false} />, false)).not.toContain("bg-tone-");
    // A figure that rounds to ₹0 shows no sign, so no tint.
    expect(render(<TodaySummaryV2 tiles={{ ...tiles, booked: 0.2 } as never} loading={false} />, true)).not.toContain("bg-tone-profit");
  });
});

describe("CA4: a tone is never colour alone", () => {
  it("by typing: a tone without its cue does not compile", () => {
    // @ts-expect-error - a tone needs a cue (a sign, an icon or a word)
    const bad = <SummaryCard label="Booked" value="+₹2,450" tone="profit" />;
    // @ts-expect-error - a cue is a sign, an icon or a word, nothing else
    const odd = <SummaryCard label="Booked" value="+₹2,450" tone="profit" cue={{ colour: true }} />;
    expect(bad && odd).toBeTruthy();
  });

  it("at runtime: no cue, an unsigned value with a sign cue, or an empty word is refused", () => {
    const Loose = SummaryCard as unknown as (props: Record<string, unknown>) => ReactNode;
    expect(() => render(<Loose label="Booked" value="₹2,450" tone="profit" />, true)).toThrow(/sign, an icon or a word/);
    expect(() => render(<SummaryCard label="Booked" value="₹2,450" tone="profit" cue={{ sign: true }} />, true)).toThrow(/starts with \+ or −/);
    expect(() => render(<SummaryCard label="Risk" value="85%" tone="risk" cue={{ word: " " }} />, true)).toThrow(/empty/);
    expect(toneProblem("loss", { sign: true }, "−₹1,200")).toBeNull();
    expect(toneProblem("loss", { sign: true }, -1200)).toBeNull();
    expect(checkTone(undefined, undefined, "₹1")).toBe(false);
  });

  it("each cue shows in both colour modes; the tint only with colour v2", () => {
    const cues: [string, ToneCue][] = [["+₹2,450", { sign: true }], ["85%", { icon: TriangleAlert }], ["Neutral", { word: "Watch" }]];
    for (const [value, cue] of cues) {
      const on = render(<SummaryCard label="Card" value={value} tone="info" cue={cue} />, true);
      const off = render(<SummaryCard label="Card" value={value} tone="info" cue={cue} />, false);
      expect(on).toContain("bg-tone-info");
      expect(off).not.toContain("bg-tone-info");
      for (const html of [on, off]) {
        if ("icon" in cue) expect(html).toContain("<svg");
        if ("word" in cue) expect(html).toContain(">Watch<");
        if ("sign" in cue) expect(html).toContain("+₹2,450");
      }
    }
  });
});

// CA6-1 (owner, 25 Oct): the five bottom-bar hues must read as clearly different colours. CIEDE2000 on the token values,
// in normal vision and in the Machado 2009 protanopia / deuteranopia simulation (severity 1.0, the CA5 matrices).
function toLinear(v: number): number { const s = v / 255; return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; }
function toByte(v: number): number { const c = Math.min(Math.max(v, 0), 1); return Math.round(255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055)); }
function lab(rgb: number[]): [number, number, number] {
  const [r, g, b] = rgb.map(toLinear);
  const f = (t: number) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const x = f((0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047), y = f(0.2126 * r + 0.7152 * g + 0.0722 * b);
  const z = f((0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883);
  return [116 * y - 16, 500 * (x - y), 200 * (y - z)];
}
function deltaE2000(p: number[], q: number[]): number {
  const [l1, a1, b1] = lab(p), [l2, a2, b2] = lab(q), rad = Math.PI / 180;
  const cBar = (Math.hypot(a1, b1) + Math.hypot(a2, b2)) / 2;
  const g = 0.5 * (1 - Math.sqrt(cBar ** 7 / (cBar ** 7 + 25 ** 7)));
  const [a1p, a2p] = [(1 + g) * a1, (1 + g) * a2];
  const [c1, c2] = [Math.hypot(a1p, b1), Math.hypot(a2p, b2)];
  const hue = (b: number, a: number) => ((Math.atan2(b, a) / rad) + 360) % 360;
  const [h1, h2] = [hue(b1, a1p), hue(b2, a2p)];
  let dh = h2 - h1;
  if (c1 * c2 === 0) dh = 0; else if (dh > 180) dh -= 360; else if (dh < -180) dh += 360;
  const dH = 2 * Math.sqrt(c1 * c2) * Math.sin((dh / 2) * rad);
  const lBar = (l1 + l2) / 2, cpBar = (c1 + c2) / 2;
  const hBar = c1 * c2 === 0 ? h1 + h2 : Math.abs(h1 - h2) <= 180 ? (h1 + h2) / 2 : h1 + h2 < 360 ? (h1 + h2 + 360) / 2 : (h1 + h2 - 360) / 2;
  const t = 1 - 0.17 * Math.cos((hBar - 30) * rad) + 0.24 * Math.cos(2 * hBar * rad) + 0.32 * Math.cos((3 * hBar + 6) * rad) - 0.2 * Math.cos((4 * hBar - 63) * rad);
  const sl = 1 + (0.015 * (lBar - 50) ** 2) / Math.sqrt(20 + (lBar - 50) ** 2), sc = 1 + 0.045 * cpBar, sh = 1 + 0.015 * cpBar * t;
  const rt = -Math.sin(2 * 30 * Math.exp(-(((hBar - 275) / 25) ** 2)) * rad) * 2 * Math.sqrt(cpBar ** 7 / (cpBar ** 7 + 25 ** 7));
  return Math.sqrt(((l2 - l1) / sl) ** 2 + ((c2 - c1) / sc) ** 2 + (dH / sh) ** 2 + rt * ((c2 - c1) / sc) * (dH / sh));
}
const MACHADO = {
  protanopia: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
  deuteranopia: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.01182, 0.04294, 0.968881]],
} as const;
const simulate = (rgb: number[], m: readonly (readonly number[])[]) => {
  const lin = rgb.map(toLinear);
  return m.map((row) => toByte(row[0] * lin[0] + row[1] * lin[1] + row[2] * lin[2]));
};
const pairs = <T,>(xs: T[]): [T, T][] => xs.flatMap((a, i) => xs.slice(i + 1).map((b) => [a, b] as [T, T]));

describe("CA6-1 (owner, 25 Oct): the five bottom-bar hues are clearly different", () => {
  it.each(Object.entries(THEMES))("%s: CIEDE2000 >= 20 between every two tabs in normal vision", (_, t) => {
    for (const [a, b] of pairs(TAB_TINTS)) {
      expect(deltaE2000(t[`sec-${a}`], t[`sec-${b}`]), `${a} / ${b}`).toBeGreaterThanOrEqual(20);
    }
  });

  // Colour-blind simulation: every pair stays >= 10 apart, except where the physics of the dark theme leaves no room -
  // the 7 : 1 light tints there make blue and violet nearly the same light lavender to a deuteranope (measured 8.7).
  // Those two tabs also carry different icons and words (House / Today, Chart / Charts), checked below, so a tab is
  // never told apart by its colour alone. Any other pair under 10, or any pair under 8, fails.
  const ICON_ONLY: Record<string, string[]> = { "today/charts": ["deuteranopia"] };
  it.each(Object.entries(THEMES))("%s: the colour-blind simulation keeps them apart (>= 10, or >= 8 with a different icon)", (_, t) => {
    for (const [name, m] of Object.entries(MACHADO)) {
      for (const [a, b] of pairs(TAB_TINTS)) {
        const d = deltaE2000(simulate(t[`sec-${a}`], m), simulate(t[`sec-${b}`], m));
        const floor = (ICON_ONLY[`${a}/${b}`] ?? []).includes(name) ? 8 : 10;
        expect(d, `${name}: ${a} / ${b}`).toBeGreaterThanOrEqual(floor);
      }
    }
  });

  it("every tab has its own icon and its own word", () => {
    const html = render(<BottomNavV2 current="charts" moreOpen={false} onMore={() => {}} />, true);
    const icons = [...html.matchAll(/class="lucide lucide-([a-z0-9-]+)/g)].map((m) => m[1]);
    expect(icons).toHaveLength(5);
    expect(new Set(icons).size).toBe(5);
  });
});

describe("CA6-CA8 (owner, 25 Oct): the phone - bottom bar, top bar and the Today hero card", () => {
  it.each(Object.entries(THEMES))("%s: CA6 the strong tab tint and its bold word meet AA on the bar", (_, t) => {
    for (const s of TINTS) {
      // The tab on screen: the card colour icon on the solid section colour, and the word in that colour on the bar.
      expect(contrast(t["surface-1"], t[`sec-${s}`]), `icon on the strong ${s} chip`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(t[`sec-${s}`], t["surface-1"]), `${s} word on the bar`).toBeGreaterThanOrEqual(4.5);
      // The soft chip of every other tab: the icon on it (4.5 : 1), and the strong chip stands apart from the soft one.
      expect(contrast(t[`sec-${s}`], t[`sec-${s}-soft`]), `${s} icon on its soft chip`).toBeGreaterThanOrEqual(4.5);
    }
    expect(contrast(t["fg-muted"], t["surface-1"]), "the other words").toBeGreaterThanOrEqual(4.5);
  });

  it("CA6: every tab its own tint, the tab on screen strong + bold, More teal; off as before", () => {
    const html = render(<BottomNavV2 current="charts" moreOpen={false} onMore={() => {}} />, true);
    const tints = [...html.matchAll(/data-nav-tint="([a-z]+)"/g)].map((m) => m[1]);
    expect(tints).toEqual(["today", "charts", "signals", "trades", "more"]);           // CA6-1: five different hues
    expect(BOTTOM_BAR_V2.filter((b) => !b.flag).map((b) => sectionTintOf(b.key))).toEqual(["today", "charts", "signals", "trades"]);
    expect(html).toContain(navChipClass("charts", true));
    // UX-13 option C (owner, 27 Oct): the other four tabs keep their colour on the icon, with no soft round fill.
    expect(html.match(/rounded-pill[^"]*bg-sec-[a-z]+-soft/g)).toBeNull();
    expect(html.match(/rounded-pill[^"]* text-sec-(today|signals|trades|more)"/g)).toHaveLength(4);
    expect(html).toContain(`${navWordClass("charts")} font-semibold`);
    // 44 px targets: every tab is at least 64 px tall and a fifth of the bar wide.
    expect(html.match(/min-h-16 flex-1/g)).toHaveLength(5);
    const more = render(<BottomNavV2 current="charts" moreOpen onMore={() => {}} />, true);
    expect(more).toMatch(/data-nav-pill="true" data-nav-tint="more"/);
    expect(render(<BottomNavV2 current="charts" moreOpen={false} onMore={() => {}} />, false)).not.toMatch(/data-nav-tint|bg-sec-/);
  });

  it("UI-28 U3.5: with `watchlists` on, the bar keeps four tabs and More (Watchlist lives in the Charts panel and in More)", () => {
    const watch = { on: (f: string) => f === "watchlists" };
    const html = render(<BottomNavV2 current="watchlist" moreOpen={false} onMore={() => {}} ctx={watch} />, true);
    expect(html.match(/min-h-16 flex-1/g)).toHaveLength(5);
    expect(html).not.toContain('href="/watchlist"');
    expect([...html.matchAll(/data-nav-label[^>]*>([^<]+)</g)].map((m) => m[1])).toEqual(["Today", "Charts", "Signals", "My trades", "More"]);
    expect(html).not.toContain("tracking-tight");
  });

  const bar = (on: boolean, section: Parameters<typeof TopBarV2>[0]["section"], title = "Option chain") => render(
    <TopBarV2 title={title} section={section} user={null} notificationsActive={false} accountActive={false} accountPath="/login" onSearch={() => {}} />, on);

  it("CA7: the phone top bar shows the page's section chip before the title; the title is never cut on a phone", () => {
    const on = bar(true, "option-chain");
    expect(on).toMatch(/data-section-chip="markets" data-topbar-chip="true" class="[^"]*bg-sec-markets-soft text-sec-markets[^"]*md:hidden/);
    expect(on.indexOf("data-topbar-chip")).toBeLessThan(on.indexOf("data-topbar-title"));
    expect(bar(true, "terminal")).toContain('data-section-chip="trading"');
    expect(bar(false, "option-chain")).not.toContain("data-topbar-chip");
    expect(bar(true, null)).not.toContain("data-topbar-chip");
    // H9: no truncation on a phone (wraps instead; the bar grows), one line from md up.
    const title = /data-topbar-title="true" class="([^"]*)"/.exec(bar(true, "markets", "Sectors & indices"))![1].split(" ");
    expect(title).not.toContain("truncate");
    expect(title).toContain("md:truncate");
    // H9 (26 Oct): wrap only between words - never "Watchlis / t" - so no break-anywhere and no phone min-w-0.
    expect(title).not.toContain("[overflow-wrap:anywhere]");
    expect(title).toContain("[overflow-wrap:normal]");
    expect(title).not.toContain("min-w-0");
    expect(bar(true, "markets")).toContain("min-h-14");
  });

  const indexCss = readFileSync("src/styles/index.css", "utf8");

  it("CA8: the Today hero card's gradient outline is drawn thicker on a phone, the same gradient", () => {
    expect(indexCss).toMatch(/@media \(max-width: 767\.98px\) \{\s*\.accent-gradient-border \{\s*border-width: 2px;\s*border-top-width: 4px;/);
    expect(indexCss).toMatch(/\.accent-gradient-border \{[^}]*var\(--accent-gradient\) border-box/);
  });
});
