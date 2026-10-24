import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import css from "../styles/tokens.css?raw";
import { PageTabs } from "./PageTabs";
import { PAGETAB_ACTIVE, PAGETAB_IDLE, SUBTAB_ACTIVE, SUBTAB_IDLE, SUBTAB_TRACK, SubTabs, fadeSides, nextKey, rowMode } from "./SubTabs";
import { TimeframeTabs, splitTimeframes } from "./TimeframeTabs";

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

  it("W0-1: never a wrapped row - up to 5 options equal widths, more than 5 one scrolling row", () => {
    expect(rowMode(5)).toBe("segments");
    expect(rowMode(6)).toBe("scroll");
    expect(rowMode(2, "scroll")).toBe("scroll");
    const five = renderToStaticMarkup(<SubTabs items={ITEMS} value="a" label="X" />);
    expect(five).not.toContain("flex-wrap");
    expect(five).toContain('data-mode="segments"');
    expect(five).toMatch(/data-subtab="a"[^>]*class="[^"]*flex-1 basis-0/);
    const six = renderToStaticMarkup(<SubTabs label="X" value="f" items={["a", "b", "c", "d", "e", "f"].map((k) => ({ key: k, label: k }))} />);
    expect(six).not.toContain("flex-wrap");
    expect(six).toContain('data-mode="scroll"');
    expect(six).toContain("overflow-x-auto");
    expect(six).toMatch(/data-subtab="f"[^>]*class="[^"]*shrink-0/);
    expect(SUBTAB_TRACK).toContain("bg-surface-2");
  });

  it("the edge fade shows on the side(s) that hide more tabs", () => {
    expect(fadeSides(0, 300, 300)).toEqual({ left: false, right: false });
    expect(fadeSides(0, 300, 500)).toEqual({ left: false, right: true });
    expect(fadeSides(100, 300, 500)).toEqual({ left: true, right: true });
    expect(fadeSides(200, 300, 500)).toEqual({ left: true, right: false });
  });

  it("W0-2: PageTabs (primary) is an underline bar, clearly different from the filled SubTabs", () => {
    const html = renderToStaticMarkup(<PageTabs kind="toggle" items={ITEMS} value="a" label="Show" />);
    expect(html).toContain('data-level="primary"');
    expect(html).toMatch(/aria-pressed="true"[^>]*class="[^"]*border-b-2[^"]*border-brand/);
    expect(html).not.toContain("bg-brand");
    expect(html).not.toContain("bg-surface-2");
    expect(PAGETAB_ACTIVE).toContain("border-brand");
    expect(PAGETAB_IDLE).toContain("text-fg-muted");
    expect(renderToStaticMarkup(<SubTabs items={ITEMS} value="a" label="X" />)).toContain('data-level="secondary"');
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

describe("UI-H W0-1: the timeframe row", () => {
  const ALL = ["1min", "3min", "5min", "15min", "30min", "60min", "day", "week", "month"].map((k) => ({ key: k, label: k.toUpperCase() }));
  it("the most-used five in the row, the rest under More", () => {
    const { row, more } = splitTimeframes(ALL);
    expect(row.map((o) => o.key)).toEqual(["1min", "5min", "15min", "60min", "day"]);
    expect(more.map((o) => o.key)).toEqual(["3min", "30min", "week", "month"]);
    expect(splitTimeframes(ALL.slice(0, 7)).more.map((o) => o.key)).toEqual(["3min", "30min"]);
  });
  it("a timeframe picked from More is shown on the More button, filled and pressed", () => {
    const html = renderToStaticMarkup(<TimeframeTabs options={ALL} value="week" onChange={() => undefined} />);
    expect(html).toMatch(/role="group" aria-label="Timeframe"/);
    expect(html).toMatch(/aria-haspopup="menu"[^>]*aria-pressed="true"[^>]*aria-label="WEEK"[^>]*class="[^"]*bg-brand/);
    expect(html).not.toContain(">More<");
    const plain = renderToStaticMarkup(<TimeframeTabs options={ALL} value="5min" onChange={() => undefined} />);
    expect(plain).toContain('aria-label="More timeframes"');
    expect(plain).toMatch(/aria-pressed="true"[^>]*data-subtab="5min"/);
  });
});

describe("UI-H H2: the active sub-tab contrast tokens", () => {
  const dark = block(":root");
  const light = { ...dark, ...block(':root[data-theme="light"]') };
  it.each([["dark", dark], ["light", light]])("%s: active text on the accent fill and muted text on the track are AA", (_, t) => {
    expect(ratio(t["on-brand"], t["brand-fill"])).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t["fg-muted"], t["surface-2"])).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t["fg"], t["surface-1"])).toBeGreaterThanOrEqual(4.5);          // the hovered inactive tab
  });
});
