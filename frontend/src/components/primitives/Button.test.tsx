import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from "../../trading/PartsV2";
import { Button, buttonClass } from "./Button";

const set = (s: string) => new Set(s.split(/\s+/).filter(Boolean));

/** Chart + colour order Part 3 (review PR sequence item 8): the one Button, with `outline` and the v2 size. */
describe("Button", () => {
  it("keeps the P1.2 sizes and variants exactly as they were", () => {
    const html = renderToStaticMarkup(<Button variant="primary">Start</Button>);
    expect(html).toContain('class="inline-flex shrink-0 items-center justify-center rounded-md font-semibold transition-colors '
      + "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface "
      + 'disabled:cursor-not-allowed disabled:opacity-50 bg-brand text-on-brand hover:bg-brand-strong h-9 px-4 text-sm gap-2"');
    expect(renderToStaticMarkup(<Button size="sm">Undo</Button>)).toContain("border border-border bg-surface-2 text-fg hover:bg-surface-3 min-h-11 px-3 text-xs gap-1.5 md:min-h-0 md:h-8");   // UI-28 U3.6: 44 px on a phone
  });

  it("the trading parts' button classes are the Button's own, with the same look as the old copies", () => {
    // The class strings PartsV2.tsx held by hand before (UI2-9a); the cursor on a disabled button is the only addition.
    const OLD_SECONDARY = "inline-flex h-11 shrink-0 items-center justify-center gap-space-2 rounded-control border border-border bg-surface-1 px-space-4 text-t14 font-semibold text-fg transition-colors duration-fast hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-50 md:h-9";
    const OLD_PRIMARY = "inline-flex h-11 shrink-0 items-center justify-center gap-space-2 rounded-control bg-brand px-space-5 text-t14 font-semibold text-on-brand transition-colors duration-fast hover:bg-brand-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-50 md:h-9";
    expect(set(BUTTON_SECONDARY)).toEqual(new Set([...set(OLD_SECONDARY), "disabled:cursor-not-allowed"]));
    expect(set(BUTTON_PRIMARY)).toEqual(new Set([...set(OLD_PRIMARY), "disabled:cursor-not-allowed"]));
    expect(BUTTON_PRIMARY).toBe(buttonClass({ variant: "primary", size: "v2" }));
  });

  it("only primary is a solid brand fill; outline and ghost never are", () => {
    expect(buttonClass({ variant: "primary", size: "v2" })).toMatch(/(?<![:\w-])bg-brand(?![\w/-])/);
    for (const variant of ["outline", "ghost", "secondary", "danger"] as const) {
      expect(buttonClass({ variant, size: "v2" }), variant).not.toMatch(/(?<![:\w-])bg-brand(?![\w/-])/);
    }
    expect(buttonClass({ variant: "outline" })).toContain("border border-border bg-surface-1");
    expect(buttonClass({ variant: "ghost" })).not.toContain("border");
  });

  it("the v2 size is a finger on a phone and 36 px with a mouse, on the 4-pt grid", () => {
    const v2 = set(buttonClass({ variant: "outline", size: "v2" }));
    for (const c of ["h-11", "md:h-9", "rounded-control", "px-space-4", "text-t14", "gap-space-2", "duration-fast"]) expect(v2.has(c), c).toBe(true);
    expect(set(buttonClass({ variant: "primary", size: "v2" })).has("px-space-5")).toBe(true);
    expect(v2.has("rounded-md")).toBe(false);
  });

  it("passes extra classes through and keeps the loading state", () => {
    const html = renderToStaticMarkup(<Button variant="outline" size="v2" className="w-full" loading>Save</Button>);
    expect(html).toContain("w-full");
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain("disabled");
  });
});

describe("UI-28 U3.2 a disabled button says why", () => {
  it("with `reason` and disabled: one helper line under it, tied by aria-describedby, and the tooltip; enabled: nothing extra", () => {
    const off = renderToStaticMarkup(<Button disabled reason="Pick a list first">Run</Button>);
    expect(off).toContain('data-disabled-reason="true">Pick a list first</span>');
    expect(off).toMatch(/aria-describedby="([^"]+)"[^>]*title="Pick a list first"[\s\S]*id="\1"/);
    const on = renderToStaticMarkup(<Button reason="Pick a list first">Run</Button>);
    expect(on).not.toContain("data-disabled-reason");
    expect(renderToStaticMarkup(<Button disabled loading reason="Pick a list first">Run</Button>)).not.toContain("data-disabled-reason");
  });
});

import { isNumericColumn } from "./Table";
describe("UI-28 U3.9 numeric columns", () => {
  it("a column of numbers is right-aligned by default; text stays left; an explicit `numeric` wins", () => {
    const rows = [{ a: "₹1,200", b: "NIFTY", c: 3 }, { a: "−4.5%", b: "TCS", c: 12 }, { a: "–", b: "INFY", c: 0 }];
    expect(isNumericColumn({ cell: (r: typeof rows[number]) => r.a }, rows)).toBe(true);
    expect(isNumericColumn({ cell: (r: typeof rows[number]) => r.b }, rows)).toBe(false);
    expect(isNumericColumn({ cell: (r: typeof rows[number]) => r.c }, rows)).toBe(true);
    expect(isNumericColumn({ cell: (r: typeof rows[number]) => r.c, numeric: false }, rows)).toBe(false);
    expect(isNumericColumn({ cell: () => "–" }, rows)).toBe(false);
  });
});
