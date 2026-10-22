import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { filterHistory, HISTORY_FILTERS } from "./ChartAnalyzePageV2";
import { fixtureItem, fixtureResult, fixtureSharedResult } from "./fixtures";
import ResultViewV2 from "./ResultViewV2";
import { SHARE_EXPLAINER, shareUrl } from "./ShareDialogV2";
import { LINK_PROBLEM } from "./SharedAnalysisV2";

/** Analyze a chart PR-A4: the share link (organisation only, structure only) and the history filters. Made-up fixtures. */

const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&[a-z#0-9]+;/gi, " ");
const shared = () => renderToStaticMarkup(<ResultViewV2 shared result={fixtureSharedResult()} imageSrc={null} />);

describe("the shared copy", () => {
  it("shows the structure only: no name, no price, no picture, no buttons", () => {
    const html = shared();
    const visible = text(html);
    expect(html).toContain('data-shared="true"');
    expect(visible).toContain("The reading");
    expect(html).toContain('data-testid="shared-note"');
    expect(html).toContain('data-testid="structure-sketch"');
    expect(html).not.toContain("<img");
    expect(visible).not.toMatch(/DEMO|NSE|15m|≈/);
    expect(visible).not.toMatch(/\d{2,}/);                     // no price, no date (the shape counts are attributes, not text)
    expect(html).not.toContain("<button");
    expect(visible).not.toContain("Image-only analysis");      // the approximate-prices label has nothing to qualify
    for (const block of ["ca-story", "ca-setup", "ca-evidence", "ca-scenarios", "ca-wait", "ca-htf", "ca-levels"]) expect(html, block).toContain(`data-block="${block}"`);
    expect(visible).toContain("Higher timeframe · Up");
    expect(visible).toContain("Educational only");
  });

  it("the owner's own result keeps its prices and gets the Share button only when offered", () => {
    const own = (extra = {}) => renderToStaticMarkup(<ResultViewV2 result={fixtureResult("forming")} imageSrc="blob:x" onAlert={noop} onExport={noop} onDelete={noop} {...extra} />);
    expect(own()).not.toContain("Share with my organisation");
    expect(own({ onShare: noop })).toContain("Share with my organisation");
    expect(own({ onShare: noop, shareLive: true })).toContain("Shared · manage link");
    expect(text(own())).toContain("≈ 120.5");
  });

  it("the link opens this page with the link's token, and says who can open it and what is left out", () => {
    expect(shareUrl("abc.def", "https://example.test")).toBe("https://example.test/chart-analyze?shared=abc.def");
    expect(SHARE_EXPLAINER).toMatch(/organisation/);
    expect(SHARE_EXPLAINER).toMatch(/name.*prices.*dates.*picture/);
    expect(LINK_PROBLEM.link_gone).toMatch(/stopped/);
    expect(LINK_PROBLEM.link_expired).toMatch(/expired/);
  });
});

describe("the history filters", () => {
  const items = [fixtureItem(1, "formed"), fixtureItem(2, "forming"), fixtureItem(3, "none"), fixtureItem(4, null), fixtureItem(5, "forming")];
  it("filter by what the method saw, and the not-analysed ones", () => {
    expect(filterHistory(items, "all")).toHaveLength(5);
    expect(filterHistory(items, "formed").map((i) => i.id)).toEqual([1]);
    expect(filterHistory(items, "forming").map((i) => i.id)).toEqual([2, 5]);
    expect(filterHistory(items, "none").map((i) => i.id)).toEqual([3]);
    expect(filterHistory(items, "open").map((i) => i.id)).toEqual([4]);
    expect(HISTORY_FILTERS.map((f) => f.label)).toEqual(["All", "Formed", "Forming", "No setup", "Not analysed"]);
  });
});
