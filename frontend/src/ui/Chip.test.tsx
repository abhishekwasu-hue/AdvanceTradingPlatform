import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Badge } from "../components/primitives";
import { ChipV2 } from "../dashboard/CardV2";
import { Chip, FilterChip, chipClass, type ChipTone } from "./Chip";

/** Chart + colour order Part 3 (review PR sequence item 8): one Chip behind the old ChipV2 and Badge. */
describe("Chip", () => {
  it("ChipV2 renders exactly what it rendered before, for every kind", () => {
    const base = "inline-flex h-5 shrink-0 items-center rounded-pill px-space-2 text-t12 font-medium";
    const OLD = {
      outline: `${base} border border-border text-fg-muted`, solid: `${base} bg-fg text-surface-1`,
      brand: `${base} bg-brand/10 text-brand`, warn: `${base} bg-warn-tint text-warn`,
    };
    for (const [kind, cls] of Object.entries(OLD)) {
      expect(renderToStaticMarkup(<ChipV2 kind={kind as keyof typeof OLD}>Paper</ChipV2>)).toBe(`<span class="${cls}">Paper</span>`);
    }
    expect(renderToStaticMarkup(<ChipV2>Paper</ChipV2>)).toBe(`<span class="${OLD.outline}">Paper</span>`);
  });

  it("Badge renders exactly what it rendered before, for every tone", () => {
    const base = "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-semibold";
    const OLD = {
      neutral: "border-border bg-surface-2 text-fg-muted", up: "border-up/40 bg-up/10 text-up", down: "border-down/40 bg-down/10 text-down",
      warn: "border-warn/40 bg-warn/10 text-warn", info: "border-info/40 bg-info/10 text-info", brand: "border-brand/40 bg-brand/10 text-brand",
    };
    for (const [tone, cls] of Object.entries(OLD)) {
      expect(renderToStaticMarkup(<Badge tone={tone as keyof typeof OLD}>x</Badge>)).toBe(`<span class="${base} ${cls}">x</span>`);
    }
    expect(renderToStaticMarkup(<Badge className="ml-2">x</Badge>)).toBe(`<span class="${base} ${OLD.neutral} ml-2">x</span>`);
  });

  it("each tone uses its own meaning token, and the AI and info chips their tints", () => {
    const tones: ChipTone[] = ["neutral", "brand", "up", "down", "warn", "info", "ai"];
    for (const tone of tones) {
      const cls = chipClass({ tone });
      if (tone === "neutral") expect(cls).toContain("text-fg-muted");
      else expect(cls).toContain(`text-${tone}`);
      for (const other of tones.filter((t) => t !== tone && t !== "neutral")) expect(cls).not.toMatch(new RegExp(`(?<![\\w-])(?:text|bg|border)-${other}(?![\\w-])`));
    }
    expect(chipClass({ tone: "info" })).toContain("bg-info-tint");
    expect(chipClass({ tone: "ai" })).toContain("bg-ai-tint");
    expect(chipClass({ tone: "warn", variant: "outline" })).toBe("inline-flex h-5 shrink-0 items-center rounded-pill px-space-2 text-t12 font-medium border border-warn/40 text-warn");
  });

  it("solid is always the text colour (Live), never a hue", () => {
    for (const tone of ["brand", "up", "warn", "ai"] as const) expect(chipClass({ tone, variant: "solid" })).toContain("bg-fg text-surface-1");
  });

  it("an icon is decorative and spaced; the words carry the meaning", () => {
    const html = renderToStaticMarkup(<Chip tone="info" icon={<svg />} name="tip">Tip</Chip>);
    expect(html).toContain('data-chip="tip"');
    expect(html).toContain("gap-space-1");
    expect(html).toContain('<span aria-hidden="true" class="inline-flex shrink-0"><svg></svg></span>Tip');
  });

  it("a filter chip is a toggle button: the selected one in the brand outline and tint, the others outlined", () => {
    const on = renderToStaticMarkup(<FilterChip selected onClick={() => undefined}>All</FilterChip>);
    const off = renderToStaticMarkup(<FilterChip selected={false} onClick={() => undefined}>Waiting</FilterChip>);
    expect(on).toContain('aria-pressed="true"');
    expect(on).toContain("border-brand bg-brand/10 text-brand");
    expect(off).toContain('aria-pressed="false"');
    expect(off).toContain("border-border text-fg-muted");
    expect(off).not.toMatch(/(?<![:\w-])bg-brand/);
    for (const html of [on, off]) { expect(html).toContain("h-11"); expect(html).toContain("md:h-8"); }
  });
});
