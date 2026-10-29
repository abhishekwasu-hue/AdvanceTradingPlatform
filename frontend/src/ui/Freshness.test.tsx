import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Freshness, freshnessText } from "./Freshness";
import { Skeleton } from "../components/primitives";
import { stripAsOf } from "../dashboard/MarketStripV2";
import type { MarketStrip, StripIndex } from "../dashboard/types";

const NOW = new Date("2026-10-29T09:00:00Z");          // 14:30 IST

describe("UI-28 U3.8: Freshness", () => {
  it("reads 'as of HH:MM' today and the date on another day; nothing without a time", () => {
    expect(freshnessText("2026-10-29T08:58:00Z", false, NOW)).toBe("as of 14:28 IST");
    expect(freshnessText("2026-10-28T10:00:00Z", false, NOW)).toMatch(/^as of 28 Oct 2026/);
    expect(freshnessText(null, false, NOW)).toBe("");
    expect(renderToStaticMarkup(<Freshness at={null} now={NOW} />)).toBe("");
  });
  it("a stale source reads 'Delayed' in an amber chip; a fresh one is quiet muted text", () => {
    const stale = renderToStaticMarkup(<Freshness at="2026-10-29T08:00:00Z" stale now={NOW} />);
    expect(stale).toContain('data-freshness="delayed"');
    expect(stale).toContain("Delayed · as of 13:30 IST");
    const fresh = renderToStaticMarkup(<Freshness at="2026-10-29T08:58:00Z" now={NOW} />);
    expect(fresh).toContain('data-freshness="fresh"');
    expect(fresh).toContain("text-fg-muted");
  });
  it("the Today strip's time is its newest index price, the request time only when none has one", () => {
    const idx = (as_of: string | null) => ({ as_of } as StripIndex);
    const strip = (indices: StripIndex[]) => ({ indices, fetched_at: "2026-10-29T09:00:05Z" } as MarketStrip);
    expect(stripAsOf(strip([idx("2026-10-29T08:59:00Z"), idx("2026-10-29T08:59:30Z"), idx(null)]))).toBe("2026-10-29T08:59:30Z");
    expect(stripAsOf(strip([idx(null)]))).toBe("2026-10-29T09:00:05Z");
  });
});

describe("UI-28 U3.8: Skeleton", () => {
  it("is hidden from readers by default and the busy region when labelled", () => {
    expect(renderToStaticMarkup(<Skeleton className="h-6" />)).toContain('aria-hidden="true"');
    const busy = renderToStaticMarkup(<Skeleton className="h-40" panel label="Loading signals" />);
    expect(busy).toContain('role="status"');
    expect(busy).toContain('aria-label="Loading signals"');
    expect(busy).toContain("rounded-panel");
  });
});
