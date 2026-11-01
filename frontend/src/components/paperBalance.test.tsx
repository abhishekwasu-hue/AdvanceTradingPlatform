import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PaperBalanceTile } from "./PaperAccountCard";

// PAPER-NR (owner, 1 Nov): a short practice balance never refuses a PAPER entry, so the balance may go below zero.
describe("PAPER-NR practice balance below zero", () => {
  it("is red with 'below zero - practice only' on v2 pages", () => {
    const html = renderToStaticMarkup(<PaperBalanceTile balance={-1234.5} v2 />);
    expect(html).toContain("text-down");
    expect(html).toContain("below zero - practice only");
    expect(html).toContain('data-testid="paper-below-zero"');
  });
  it("a balance at or above zero is neutral, with no note", () => {
    const html = renderToStaticMarkup(<PaperBalanceTile balance={1000} v2 />);
    expect(html).not.toContain("text-down");
    expect(html).not.toContain("below zero");
  });
  it("v1 pages are unchanged", () => {
    expect(renderToStaticMarkup(<PaperBalanceTile balance={-5} v2={false} />)).not.toContain("below zero");
  });
});

describe("StatTile value size (owner rule D.3: nothing cut off at 390)", () => {
  it("steps a long value down below sm only, and always allows a wrap", async () => {
    const { StatTile, statValueSize } = await import("./ui");
    expect(statValueSize("3 / 3")).toBe("text-2xl");
    expect(statValueSize("12,900.00")).toBe("text-xl sm:text-2xl");
    expect(statValueSize("−12,900.00")).toBe("text-lg sm:text-2xl");
    expect(statValueSize("−1,23,45,678.90")).toBe("text-base sm:text-2xl");
    const html = renderToStaticMarkup(<StatTile label="Equity" value="−12,900.00" />);
    expect(html).toContain("text-lg sm:text-2xl");
    expect(html).toContain("[overflow-wrap:anywhere]");
  });
});
