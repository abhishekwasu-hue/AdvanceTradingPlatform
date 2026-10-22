import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { fixtureResult, fixtureStrikes } from "./fixtures";
import { printTitle } from "./printResult";
import ResultViewV2 from "./ResultViewV2";
import { WITHHELD, guardDeep, guardResult, guardText, hasCallWords } from "./signalLanguage";
import StrikesSectionV2, { FROM_SETTINGS, NOT_PICKED, type StrikesState } from "./StrikesSectionV2";
import type { AnalysisResult, SetupStatus } from "./types";

/** Analyze a chart, the result screen (owner order 10): each block from a made-up fixture, its empty state, the
 * option strikes "from your settings", the PDF button, and the signal-language guard over everything shown. */

const noop = () => undefined;
const view = (result: AnalysisResult, extra: Partial<Parameters<typeof ResultViewV2>[0]> = {}) =>
  renderToStaticMarkup(<ResultViewV2 result={result} imageSrc="blob:fixture" onAlert={noop} onExport={noop} onDelete={noop} {...extra} />);
const visibleText = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&[a-z#0-9]+;/gi, " ");
const block = (html: string, id: string) => {
  const start = html.indexOf(`data-block="${id}"`);
  if (start < 0) return "";
  const end = html.indexOf("</section>", start);
  return html.slice(start, end);
};
const strikesView = (state: StrikesState) => renderToStaticMarkup(<StrikesSectionV2 state={state} />);

describe("the result screen, block by block", () => {
  it("the annotated chart: swings, areas with states, lines, divergence, commitment, change of character, setup lines", () => {
    const html = block(view(fixtureResult("forming")), "ca-annotated");
    expect(html).toContain('data-testid="annotated-chart"');
    for (const word of ["HL", "HH", "Buyer area · tested", "Seller area · fresh", "Commitment", "Lower-timeframe change", "Trigger", "Invalidation"]) expect(html, word).toContain(word);
    const formed = block(view(fixtureResult("formed")), "ca-annotated");
    expect(formed).toMatch(/divergence/i);
  });

  it("the annotated chart says so when the image is gone, and is left out with no image at all", () => {
    expect(view(fixtureResult("formed"), { imageSrc: null, imageNote: "The chart image is no longer stored." })).toContain("The chart image is no longer stored.");
    expect(view(fixtureResult("formed"), { imageSrc: null })).not.toContain('data-block="ca-annotated"');
  });

  it("the story, with the trend side, the current state and the RSI reading", () => {
    const html = block(view(fixtureResult("formed")), "ca-story");
    expect(html).toContain("Made-up story: the drawn candles rise");
    expect(html).toContain("Trend side");
    expect(html).toContain("RSI divergence");
  });

  it("the setup: side, label A / B, formed / forming / none, trigger, invalidation, next areas - in educational words", () => {
    const formed = block(view(fixtureResult("formed")), "ca-setup");
    expect(formed).toContain("Bull put side");
    expect(formed).toContain("Label A");
    expect(formed).toContain("Formed");
    for (const word of ["Trigger", "Invalidation", "Next areas", "≈ 120.5", "≈ 101", "≈ 140"]) expect(formed, word).toContain(word);
    expect(formed).toContain("not an instruction to trade");
    expect(block(view(fixtureResult("forming")), "ca-setup")).toContain("waiting for");
    const none = block(view(fixtureResult("none")), "ca-setup");
    expect(none).toContain("No setup");
    expect(none).not.toContain("Label ");
  });

  it("the evidence checklist marks each item present or absent, unscored", () => {
    const html = block(view(fixtureResult("forming")), "ca-evidence");
    expect(html.match(/aria-label="present"/g)?.length).toBe(2);
    expect(html.match(/aria-label="absent"/g)?.length).toBe(1);
    expect(html).not.toMatch(/\d+\s*%|score:/i);
  });

  it("the scenarios: primary, alternative, and where the primary is proven wrong", () => {
    const html = block(view(fixtureResult("formed")), "ca-scenarios");
    expect(html).toContain("Made-up primary scenario.");
    expect(html).toContain("Made-up alternative scenario.");
    expect(html).toContain("The primary is proven wrong");
  });

  it("what to wait for: an alert button for a price condition, a plain note otherwise, nothing once formed", () => {
    expect(block(view(fixtureResult("forming")), "ca-wait")).toContain("Alert me when this happens");
    expect(block(view(fixtureResult("none")), "ca-wait")).not.toContain("Alert me when this happens");
    expect(view(fixtureResult("formed"))).not.toContain('data-block="ca-wait"');
  });

  it("the higher-timeframe strip, and no strip when there is no higher-timeframe context", () => {
    expect(block(view(fixtureResult("formed")), "ca-htf")).toContain("1h · Up");
    expect(view({ ...fixtureResult("formed"), htf_context: [] })).not.toContain('data-block="ca-htf"');
  });

  it("the key levels table, and no table without levels", () => {
    const html = block(view(fixtureResult("formed")), "ca-levels");
    expect(html).toContain("Made-up buyer area");
    expect(html).toContain("≈ 98 – 101");
    expect(view({ ...fixtureResult("formed"), key_levels: [] })).not.toContain('data-block="ca-levels"');
  });

  it("PNG and PDF: both buttons, and the action buttons are left off the printed copy", () => {
    const html = view(fixtureResult("forming"), { onPdf: noop });
    expect(html).toContain("Download / share the result image");
    expect(html).toContain("Download PDF");
    expect(html).toContain("data-print-hide");
    expect(view(fixtureResult("forming"))).not.toContain("Download PDF");     // no PDF button without a handler
    expect(printTitle("DEMO", "15m", new Date(2026, 9, 20))).toBe("Chart analysis - DEMO 15m - 20 Oct 2026");
    expect(printTitle(null, null, new Date(2026, 9, 20))).toBe("Chart analysis - 20 Oct 2026");
  });
});

describe("option strikes - from your settings", () => {
  it("is flagged 'From your settings' and lists each setting with its worked-out strikes", () => {
    const html = strikesView({ status: "ready", data: fixtureStrikes() });
    expect(html).toContain(FROM_SETTINGS);
    expect(html).toContain(NOT_PICKED.replace(/'/g, "&#x27;"));
    expect(html).toContain("Bull put spread · main strike 1 strike out of the money · wing 2 strikes away · nearest expiry");
    expect(html).toContain("Main strike");
    expect(html).toContain("Wing");
    expect(html).toContain("≈ 115");
    expect(html).toContain("≈ 105");
    expect(html).toContain("Worked out at the trigger level");
    expect(html).toContain("approximate");
    expect(html).not.toMatch(/<button[^>]*>[^<]*(order|place)/i);
  });

  it("is part of the result screen when the page passes it in", () => {
    expect(view(fixtureResult("formed"), { strikes: { status: "ready", data: fixtureStrikes() } })).toContain('data-block="ca-strikes"');
    expect(view(fixtureResult("formed"))).not.toContain('data-block="ca-strikes"');
  });

  it("empty states: loading, an error, no fitting setting, a setting without a strike list, no symbol read", () => {
    expect(strikesView({ status: "loading" })).toContain("Working out the strikes");
    expect(strikesView({ status: "error", message: "Could not load." })).toContain("Could not load.");
    const none = strikesView({ status: "ready", data: { ...fixtureStrikes(), items: [], message: "None of your option deployments on this symbol fits this side." } });
    expect(none).toContain("None of your option deployments");
    expect(none).not.toContain("Worked out at");
    const noLadder = fixtureStrikes();
    noLadder.items[0] = { ...noLadder.items[0], legs: null, note: "The option list for this symbol is not loaded yet." };
    expect(strikesView({ status: "ready", data: noLadder })).toContain("The option list for this symbol is not loaded yet.");
    const noSymbol = strikesView({ status: "ready", data: { ...fixtureStrikes(), symbol: null, items: [], message: "The symbol could not be read." } });
    expect(noSymbol).toContain("Show strikes");
    expect(noSymbol).toContain(">Symbol<");
  });
});

describe("the signal-language guard", () => {
  it("finds the call words and passes the structure words", () => {
    for (const t of ["Buy here", "a sell zone", "the target", "targets above", "a stop below", "stop-loss", "stoploss", "SL at the low", "sold off"]) {
      expect(hasCallWords(t), t).toBe(true);
    }
    for (const t of ["the buyer area", "seller areas", "buyers held", "the trigger line", "invalidation", "slow", "a higher low"]) {
      expect(hasCallWords(t), t).toBe(false);
    }
    expect(guardText("a made-up target above")).toBe(WITHHELD);
    expect(guardText("the buyer area held")).toBe("the buyer area held");
    expect(guardDeep({ a: ["buy now", "ok"], b: { c: "stop" } })).toEqual({ a: [WITHHELD, "ok"], b: { c: WITHHELD } });
  });

  it("withholds an engine text with a call word wherever it would show, and keeps the fixed disclaimer", () => {
    const raw = fixtureResult("forming");
    const bad: AnalysisResult = {
      ...raw,
      story: ["Made-up: buy the dip here.", raw.story[1]],
      scenarios: { ...raw.scenarios, alternative: "Made-up: the next target is above." },
      evidence: [{ ...raw.evidence[0], reason: "made-up: stop below the low" }, ...raw.evidence.slice(1)],
      setup: { ...raw.setup, trigger: { text: "sell below the line", price: 120.5 } },
      what_to_wait_for: { condition: "made-up: SL hit", alert: null },
      key_levels: [{ ...raw.key_levels[0], name: "made-up target zone" }],
    };
    expect(guardResult(bad).disclaimer).toBe(raw.disclaimer);
    const html = view(bad);
    expect(html.split(WITHHELD).length - 1).toBeGreaterThanOrEqual(6);
    // Everything on screen except the disclaimer (which says the result is NOT a buy / sell call) is free of call words.
    const shown = visibleText(html.replace(/<p[^>]*data-testid="disclaimer"[^>]*>.*?<\/p>/s, ""));
    expect(hasCallWords(shown), shown.match(/\b(buy|sell|target|stop|SL)\w*/gi)?.join(", ")).toBe(false);
  });

  for (const status of ["formed", "forming", "none"] as SetupStatus[]) {
    it(`a ${status} result with strikes shows no call words outside the disclaimer`, () => {
      const html = view(fixtureResult(status), { onPdf: noop, strikes: { status: "ready", data: fixtureStrikes() } });
      const shown = visibleText(html.replace(/<p[^>]*data-testid="disclaimer"[^>]*>.*?<\/p>/s, ""));
      expect(hasCallWords(shown), shown.match(/\b(buy|sell|target|stop|SL)\w*/gi)?.join(", ")).toBe(false);
    });
  }

  it("guards the strikes answer too", () => {
    const data = fixtureStrikes();
    data.items[0] = { ...data.items[0], settings: "made-up: sell the put" };
    expect(strikesView({ status: "ready", data })).toContain(WITHHELD);
  });
});
