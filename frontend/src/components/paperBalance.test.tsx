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
