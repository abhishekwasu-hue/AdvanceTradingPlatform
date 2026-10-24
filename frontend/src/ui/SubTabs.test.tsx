import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import css from "../styles/tokens.css?raw";
import { SUBTAB_ACTIVE, SUBTAB_IDLE, SUBTAB_TRACK, SubTabs, nextKey } from "./SubTabs";

const ITEMS = [
  { key: "a", label: "Max pain", short: "Pain" },
  { key: "b", label: "PCR" },
  { key: "c", label: "Build-up" },
] as const;

describe("UI-H H2: SubTabs, the one tab / sub-tab control", () => {
  it("tabs: tablist + tab roles, aria-selected, roving focus, ids that link to the panels", () => {
    const html = renderToStaticMarkup(<SubTabs items={ITEMS} value="b" onChange={() => undefined} label="Analyses" idPrefix="oc" />);
    expect(html).toMatch(/role="tablist"[^>]*aria-label="Analyses"/);
    expect(html.match(/role="tab"/g)).toHaveLength(3);
    expect(html).toMatch(/aria-selected="true"[^>]*id="oc-tab-b"[^>]*aria-controls="oc-panel-b"[^>]*tabindex="0"/);
    expect(html).toMatch(/aria-selected="false"[^>]*id="oc-tab-a"[^>]*tabindex="-1"/);
  });

  it("the active tab is bold and filled with the accent; the others muted", () => {
    const html = renderToStaticMarkup(<SubTabs items={ITEMS} value="a" label="Analyses" />);
    const active = /<button[^>]*data-subtab="a"[^>]*class="([^"]*)"/.exec(html)?.[1] ?? "";
    const idle = /<button[^>]*data-subtab="b"[^>]*class="([^"]*)"/.exec(html)?.[1] ?? "";
    for (const c of SUBTAB_ACTIVE.split(" ")) expect(active.split(" ")).toContain(c);
    expect(active).toContain("bg-brand");
    expect(active).toContain("text-on-brand");
    expect(active).toContain("font-semibold");
    expect(idle).toContain("text-fg-muted");
    expect(idle).not.toContain("bg-brand");
    expect(SUBTAB_IDLE).not.toContain("bg-brand");
  });

  it("phones: the row wraps by default (nothing off-screen); scroll layout keeps one line with a visible scroller", () => {
    const wrap = renderToStaticMarkup(<SubTabs items={ITEMS} value="a" label="X" />);
    expect(wrap).toContain("flex-wrap");
    expect(wrap).not.toContain("overflow-x-auto");
    const scroll = renderToStaticMarkup(<SubTabs items={ITEMS} value="a" label="X" layout="scroll" />);
    expect(scroll).toContain("overflow-x-auto");
    expect(scroll).toContain("subtabs-scroll");
    expect(SUBTAB_TRACK).toContain("bg-surface-2");
  });

  it("short labels on phones, full labels from md", () => {
    const html = renderToStaticMarkup(<SubTabs items={ITEMS} value="a" label="X" />);
    expect(html).toContain('<span class="md:hidden">Pain</span><span class="hidden md:inline">Max pain</span>');
  });

  it("toggle: a group of aria-pressed buttons (timeframes keep their e2e semantics)", () => {
    const html = renderToStaticMarkup(<SubTabs kind="toggle" items={ITEMS} value="c" label="Timeframe" />);
    expect(html).toMatch(/role="group"[^>]*aria-label="Timeframe"/);
    expect(html).not.toContain('role="tab"');
    expect(html).toMatch(/aria-pressed="true"[^>]*data-subtab="c"/);
  });

  it("links: a nav of links, the current one aria-current=page", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter><SubTabs kind="links" label="My trades" value="b"
        items={[{ key: "a", label: "Positions", to: "/positions" }, { key: "b", label: "Orders", to: "/orders" }]} /></MemoryRouter>,
    );
    expect(html).toMatch(/<nav aria-label="My trades"/);
    expect(html).toMatch(/<a aria-current="page" data-subtab="b"[^>]*href="\/orders"/);
    expect(html).not.toMatch(/aria-current="page" data-subtab="a"/);
  });

  it("keyboard: arrows wrap round, Home / End jump, other keys do nothing", () => {
    const keys = ["a", "b", "c"];
    expect(nextKey(keys, "a", "ArrowRight")).toBe("b");
    expect(nextKey(keys, "c", "ArrowRight")).toBe("a");
    expect(nextKey(keys, "a", "ArrowLeft")).toBe("c");
    expect(nextKey(keys, "b", "Home")).toBe("a");
    expect(nextKey(keys, "a", "End")).toBe("c");
    expect(nextKey(keys, "a", "Enter")).toBeNull();
  });
});

// The active-tab colours, from tokens.css itself: white (on-brand) on the brand fill, and the muted text on the
// track (surface-2), meet WCAG AA in both themes.
function block(selector: string): Record<string, number[]> {
  const re = new RegExp(`(?:^|\\n)${selector.replace(/[[\]"=()]/g, (c) => `\\${c}`)}\\s*\\{([^}]*)\\}`, "g");
  const out: Record<string, number[]> = {};
  for (const m of css.matchAll(re)) {
    for (const [, name, value] of m[1].matchAll(/--([\w-]+):\s*([\d\s]+);/g)) out[name] = value.trim().split(/\s+/).map(Number);
  }
  return out;
}
const lum = ([r, g, b]: number[]) => {
  const ch = (v: number) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
};
const ratio = (a: number[], b: number[]) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };

describe("UI-H H2: the active sub-tab contrast tokens", () => {
  const dark = block(":root");
  const light = { ...dark, ...block(':root[data-theme="light"]') };
  it.each([["dark", dark], ["light", light]])("%s: active text on the accent fill and muted text on the track are AA", (_, t) => {
    expect(ratio(t["on-brand"], t["brand-fill"])).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t["fg-muted"], t["surface-2"])).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t["fg"], t["surface-1"])).toBeGreaterThanOrEqual(4.5);          // the hovered inactive tab
  });
});
