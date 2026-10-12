import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { EvidencePack } from "./evidence";
import evidenceSample from "./evidenceSample.json";
import { LearnPanel } from "./LearnPanel";
import { changeText, isLearn, type ExamplesResponse, type LearnResponse } from "./library";
import { ScreenGallery } from "./ScreenGallery";
import sample from "./taxonomySample.json";
import type { Taxonomy } from "./taxonomy";

const tax = sample as unknown as Taxonomy;
const pack = evidenceSample as unknown as EvidencePack;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, '"');

const LEARN: LearnResponse = {
  id: "starter.oversold", name: "Oversold", base_tf: "1d", direction: "long", mirror_of: null,
  method: { id: "indicator", label: "Indicator", definition: "Moving averages, oscillators, bands, channels and volatility measures." },
  setup: { id: "reversal", label: "Reversal", definition: "Exhaustion, divergence, a failed break or a sweep at a level." },
  page: null, page_note: "The method page (P1 §1.6) is linked here once the Learn pages are available.",
  provenance: { id: "starter.oversold", version: 1, changes: [{ version: 1, note: "Filed in the library (U9 TX1)." }],
    source_note: "The U5 funnel builder's own starting point; no external source.", sources: [], text: "RSI(14) <= 30",
    text_sha256: "f3c0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e" },
  note: "Education: past matches of this screen on the shared end-of-day history and what the close did after them. Three examples are illustrations - not a statistic, not a forecast and not advice.",
};
const EXAMPLES: ExamplesResponse = {
  id: "starter.oversold", name: "Oversold", base_tf: "1d", text: "RSI(14) <= 30", scanned: 2, matches_seen: 7, after_sessions: [5, 10, 20],
  examples: [{ symbol: "TCS", date: "2026-06-12", close: 3480.5, after: { "5": 2.34, "10": -1.2, "20": 4.05 }, pack },
             { symbol: "INFY", date: "2026-03-03", close: 1500, after: { "5": null, "10": 0, "20": -3.33 }, pack: null }],
  problems: ["XYZ: 0 daily bars in the shared history"], note: LEARN.note,
};

describe("U9 TX4 helpers", () => {
  it("formats a past change and guards the response", () => {
    expect(changeText(2.345)).toBe("+2.3%");
    expect(changeText(-1.2)).toBe("-1.2%");
    expect(changeText(0)).toBe("0.0%");
    expect(changeText(null)).toBe("–");
    expect(isLearn(LEARN)).toBe(true);
    expect(isLearn({ detail: "off" })).toBe(false);
  });
});

describe("ScreenGallery with learn mode (U9 TX4)", () => {
  it("shows each card's version and a Learn button only with learn mode on", () => {
    const on = renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} loadLearn={async () => LEARN} />);
    expect(on.match(/aria-label="Learn: /g)).toHaveLength(tax.screens.length);
    expect(text(on)).toContain("Oversold v1");
    const off = renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} />);
    expect(off).not.toContain("Learn: ");
  });

  it("opens the Learn panel in place of the grid", () => {
    const html = renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} loadLearn={async () => LEARN} initialLearn={LEARN} />);
    expect(html).toContain('data-testid="learn-panel"');
    expect(html).not.toContain("data-screen-id=");
  });
});

describe("LearnPanel (U9 TX4)", () => {
  it("shows the method, the provenance, the examples with their evidence and what happened after - as education", () => {
    const html = renderToStaticMarkup(<LearnPanel learn={LEARN} onBack={() => undefined} initialExamples={EXAMPLES} />);
    const t = text(html);
    expect(t).toContain("Oversold v1");
    expect(t).toContain("Indicator: Moving averages");
    expect(t).toContain("v1 · Filed in the library (U9 TX1).");
    expect(html).toContain("RSI(14) &lt;= 30");
    expect(t).toContain("sha256 f3c0a1b2c3d4e5f6…");
    expect(t).toContain("Fork to My screens");
    expect(t).toContain("TCS · matched on 2026-06-12 · close 3480.50");
    expect(t).toContain("5 sessions +2.3% · 10 sessions -1.2% · 20 sessions +4.0%");
    expect(t).toContain("5 sessions –");
    expect(html.match(/data-testid="evidence-chart"/g)).toHaveLength(1);                  // the example without a pack has none
    expect(t).toContain("Not used: XYZ");
    expect(t).toContain("not a statistic, not a forecast and not advice");
    expect(t).not.toMatch(/\b(buy|sell|recommended|best|top pick|target|profit)\b/i);
  });

  it("hides Fork when the user cannot save screens", () => {
    const html = renderToStaticMarkup(<LearnPanel learn={LEARN} onBack={() => undefined} canFork={false} />);
    expect(text(html)).not.toContain("Fork to My screens");
    expect(html).not.toMatch(/[ऀ-ॿ]/);
  });
});
