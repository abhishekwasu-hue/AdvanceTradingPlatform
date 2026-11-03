import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { fixtureAbhiRead, fixtureAbhiResult, fixtureBookRead, fixtureBookResult, fixtureStrategies } from "./fixtures";
import { AbhiCard, BookCard, bookTitle, ERROR_TEXT, estimateText, LOADING_TEXT, range, REFUSED_TEXT, TWO_CHART_NOTE, usd, type ReadState } from "./ReaderCardsV2";
import { WITHHELD } from "../chartAnalyze/signalLanguage";

/** M86 W3: the chart reader's cards. Made-up neutral fixtures only (fixtures.ts). */
const noop = () => undefined;
const abhi = (state: ReadState, over: Partial<Parameters<typeof AbhiCard>[0]> = {}) =>
  renderToStaticMarkup(<AbhiCard state={state} estimateUsd={0.07} symbol="DEMO" onRead={noop} {...over} />);
const book = (state: ReadState, title = "A. Writer, Example Book One (1901) strategy") =>
  renderToStaticMarkup(<BookCard title={title} state={state} symbol="DEMO" onClose={noop} onRetry={noop} />);
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");
const BANNED = /\b(buy|buys|buying|sell|sells|selling|hold|holds|holding|target|targets|stop[- ]?loss|SL|strike|strikes|lots?)\b/i;

describe("the helpers", () => {
  it("costs, estimate, ranges and the book title", () => {
    expect(usd(0.068)).toBe("$0.07");
    expect(usd(0.004)).toBe("< $0.01");
    expect(usd(0)).toBe("$0.00");
    expect(estimateText(0.07)).toBe("est. $0.07 per read");
    expect(range(102.8, 101.2)).toBe("101.20 – 102.80");
    expect(range(null, null)).toBe("Not read");
    const s = fixtureStrategies().strategies;
    expect(bookTitle(s[1])).toBe("A. Writer, Example Book One (1901) strategy");
    expect(bookTitle({ name: "Plain", book: "", author: "", edition: "" })).toBe("Plain strategy");
    expect(bookTitle({ name: "x", book: "Q. Writer: Book (1900)", author: "Q. Writer", edition: "1900" })).toBe("Q. Writer: Book (1900) strategy");
  });
});

describe("Abhi's method card", () => {
  it("before a read: the action and the estimate, nothing spent", () => {
    const html = abhi({ phase: "idle" });
    expect(html).toContain("Abhi&#x27;s method");
    expect(html).toContain("Read with Abhi&#x27;s method");
    expect(html).toContain("est. $0.07 per read");
    expect(html).not.toContain('data-testid="abhi-read"');
    expect(html).not.toContain("Cost");
  });

  it("a read: trend chip, the areas as ranges, correction, commitment, the sentences, label, cost", () => {
    const html = abhi({ phase: "done", result: fixtureAbhiResult() });
    expect(html).toContain('data-chip="trend-up"');
    expect(html).toContain('data-chip="area-buyer"');
    expect(html).toContain('data-chip="area-seller"');
    expect(html).toContain("101.20 – 102.80");
    expect(html).toContain("109.10 – 110.40");
    expect(html).toContain('data-chip="correction-yes"');
    expect(html).toContain('data-chip="momentum-yes"');
    expect(html).toContain('data-chip="commitment-no"');
    expect(html).toContain("Example area of interest");
    expect(html).toContain("Watch next");
    expect(html).toContain("Advanced analysis (code+AI)");
    expect(html).toContain("$0.07");
    expect(html).toContain("Read again");
    expect(html).not.toContain('data-chip="reader-cached"');
    expect(html).not.toContain("reader-cannot-read");
    // the evidence and the cleaning counts are behind (?), not on the card
    expect(html).not.toContain("Placeholder: each swing");
    expect(html).not.toContain("Sentences removed");
    expect(html).toContain("Help: About this read");
    expect(text(html)).not.toMatch(BANNED);
  });

  it("down / sideways / unclear trend, cached (cost zero), AI only, cannot_read", () => {
    for (const [direction, chip] of [["down", "trend-down"], ["sideways", "trend-sideways"], ["unclear", "trend-unclear"]] as const) {
      const result = fixtureAbhiResult({ read: fixtureAbhiRead({ trend: { direction, evidence: "" } }) });
      expect(abhi({ phase: "done", result })).toContain(`data-chip="${chip}"`);
    }
    const html = abhi({ phase: "done", result: fixtureAbhiResult({ cached: true, label: "AI only", read: fixtureAbhiRead({ cannot_read: "The example axis is cut off." }) }) });
    expect(html).toContain('data-chip="reader-cached"');
    expect(html).toContain("AI only");
    expect(html).toMatch(/Cost <span[^>]*>\$0\.00<\/span> · cached/);
    expect(html).toContain("The example axis is cut off.");
  });

  it("loading, refused, error and a failed call in plain words", () => {
    expect(abhi({ phase: "loading" })).toContain(LOADING_TEXT);
    const refused = abhi({ phase: "done", result: fixtureAbhiResult({ status: "refused", read: null }) });
    expect(refused).toContain(REFUSED_TEXT);
    expect(refused).toContain("Try again");
    expect(abhi({ phase: "done", result: fixtureAbhiResult({ status: "error", read: null, error: "Example limit reached." }) })).toContain("Example limit reached.");
    expect(abhi({ phase: "failed", message: "Network problem" })).toContain(ERROR_TEXT);
  });

  it("a text with a call word is withheld on render", () => {
    const result = fixtureAbhiResult({ read: fixtureAbhiRead({ watch_next: "Example: buy here." }) });
    const html = abhi({ phase: "done", result });
    expect(html).toContain(WITHHELD.replace(/'/g, "&#x27;").slice(0, 20));
    expect(html).not.toContain("buy here");
  });
});

describe("a book strategy card", () => {
  it("its own title, close, condition chips with ids, side, trigger + bar, invalidated, statement", () => {
    const html = book({ phase: "done", result: fixtureBookResult() });
    expect(html).toContain("A. Writer, Example Book One (1901) strategy");
    expect(html).toContain('aria-label="Close A. Writer, Example Book One (1901) strategy"');
    expect(html).toContain('data-chip="condition-met"');
    expect(html).toContain('data-chip="condition-not_met"');
    expect(html).toContain('data-chip="condition-unclear"');
    expect(html).toContain("c1_example_trend");
    expect(html).toContain("1 met · 1 not met · 1 unclear");
    expect(html).toContain('data-chip="side-long"');
    expect(html).toContain('data-chip="trigger-yes"');
    expect(html).toContain("Bar 42");
    expect(html).toContain('data-chip="invalidated-no"');
    expect(html).toContain("104.50 – 105.25");
    expect(html).toContain("Example statement");
    expect(html).not.toContain("Placeholder evidence");                    // behind (?)
    expect(html).not.toContain("reader-two-chart-note");
    expect(html).not.toContain("Abhi");                                    // never merged into Abhi's card
    expect(text(html)).not.toMatch(BANNED);
  });

  it("a two-chart strategy without its second chart says so", () => {
    const html = book({ phase: "done", result: fixtureBookResult("DEMO_TWO") });
    expect(html).toContain(TWO_CHART_NOTE);
    const short = book({ phase: "done", result: fixtureBookResult("DEMO_TWO", { read: fixtureBookRead({ side_scored: "short" }) }) });
    expect(short).toContain('data-chip="side-short"');
  });

  it("loading and refused states", () => {
    expect(book({ phase: "loading" })).toContain(LOADING_TEXT);
    expect(book({ phase: "done", result: fixtureBookResult("DEMO_ONE", { status: "refused", read: null }) })).toContain(REFUSED_TEXT);
  });
});
