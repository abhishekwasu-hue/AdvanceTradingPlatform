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
const TINTS: SectionTint[] = ["markets", "trading", "insights", "account", "admin", "broker", "copilot"];

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
    expect(sectionTintOf("dashboard")).toBe("markets");
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

  it("CA6: every tab its section tint, the tab on screen strong + bold, More the Account tint; off as before", () => {
    const html = render(<BottomNavV2 current="charts" moreOpen={false} onMore={() => {}} />, true);
    const tints = [...html.matchAll(/data-nav-tint="([a-z]+)"/g)].map((m) => m[1]);
    expect(tints).toEqual([...BOTTOM_BAR_V2.map((b) => sectionTintOf(b.key) ?? "account"), "account"]);
    expect(html).toContain(navChipClass("markets", true));
    expect(html.match(/rounded-pill[^"]*bg-sec-[a-z]+-soft/g)).toHaveLength(4);
    expect(html).toContain(`${navWordClass("markets")} font-semibold`);
    // 44 px targets: every tab is at least 64 px tall and a fifth of the bar wide.
    expect(html.match(/min-h-16 flex-1/g)).toHaveLength(5);
    const more = render(<BottomNavV2 current="charts" moreOpen onMore={() => {}} />, true);
    expect(more).toMatch(/data-nav-pill="true" data-nav-tint="account"/);
    expect(render(<BottomNavV2 current="charts" moreOpen={false} onMore={() => {}} />, false)).not.toMatch(/data-nav-tint|bg-sec-/);
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
    expect(bar(true, "markets")).toContain("min-h-14");
  });

  const indexCss = readFileSync("src/styles/index.css", "utf8");

  it("CA8: the Today hero card's gradient outline is drawn thicker on a phone, the same gradient", () => {
    expect(indexCss).toMatch(/@media \(max-width: 767\.98px\) \{\s*\.accent-gradient-border \{\s*border-width: 2px;\s*border-top-width: 4px;/);
    expect(indexCss).toMatch(/\.accent-gradient-border \{[^}]*var\(--accent-gradient\) border-box/);
  });
});
