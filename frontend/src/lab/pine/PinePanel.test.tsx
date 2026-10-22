import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import PinePanel from "./PinePanel";
import { inputWords, lineNumbers, lineRange, sortDiagnostics, statusLine, type PineApi, type PineTranslation } from "./pine";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, '"');

const SOURCE = `//@version=5
indicator("Cross")
len = input.int(9, "Fast EMA", minval=1)
w = ta.wma(close, 10)
alertcondition(ta.crossover(ta.ema(close, len), ta.ema(close, 21)), "cross")
alertcondition(close > w, "above wma")`;

const RESULT: PineTranslation = {
  ok: true, complete: false, refused: false, version: "5", script_kind: "indicator", title: "Cross", primary: 0,
  signals: [
    { index: 0, kind: "alert", label: "cross", line: 5, ok: true, text: "CrossAbove(EMA(close, $len), EMA(close, 21))", params: { len: 9 },
      words: "The EMA of the close over 9 bars crosses above the EMA of the close over 21 bars", problems: [] },
    { index: 1, kind: "alert", label: "above wma", line: 6, ok: false, text: null, params: {}, words: "",
      problems: ["not translated: 'w' on line 4 is outside the subset"] },
  ],
  params: { len: { name: "len", type: "int", default: 9, min: 1, max: null, title: "Fast EMA", line: 3 } },
  diagnostics: [
    { line: 1, col: 1, severity: "info", code: "version", message: "a note", alternative: "" },
    { line: 4, col: 5, severity: "error", code: "unsupported_function", message: "ta.wma() is not in the Pine subset",
      alternative: "ta.ema(src, len) or ta.sma(src, len) is the nearest supported moving average" },
  ],
  constructs: [], note: "Translated from the Pine Script subset; the script itself is never run.",
};

const API: PineApi = {
  templates: async () => ({ templates: [], note: "" }),
  translate: async () => RESULT,
  run: async () => { throw new Error("not in this test"); },
};

describe("Pine panel helpers (handover 11c-1)", () => {
  it("numbers lines and finds a line's range", () => {
    expect(lineNumbers("")).toEqual([1]);
    expect(lineNumbers("a\nb\nc")).toEqual([1, 2, 3]);
    expect(lineRange("ab\ncde\nf", 2)).toEqual([3, 6]);
    expect(lineRange("ab", 9)).toEqual([0, 2]);
  });

  it("orders diagnostics errors first and says what translated", () => {
    expect(sortDiagnostics(RESULT.diagnostics).map((d) => d.line)).toEqual([4, 1]);
    expect(statusLine(RESULT)).toBe("1 of 2 signal conditions translated. 1 line could not be translated.");
    expect(statusLine({ ...RESULT, refused: true, signals: [] })).toMatch(/look-ahead/);
    expect(inputWords(RESULT, RESULT.signals[0])).toEqual(["Fast EMA = 9"]);
  });
});

describe("Pine panel (handover 11c-1)", () => {
  it("shows the numbered source, line-numbered diagnostics with alternatives and the condition in plain words", () => {
    const html = renderToStaticMarkup(<PinePanel symbols={["INFY"]} baseTf="1d" api={API} initialSource={SOURCE} initialResult={RESULT} initialTemplates={[]} />);
    const t = text(html);
    expect(html).toContain('data-testid="pine-gutter"');
    expect(t).toContain("1 2 3 4 5 6");
    expect(t).toContain("Line 4");
    expect(t).toContain("Not translated");
    expect(t).toContain("Try: ta.ema(src, len) or ta.sma(src, len) is the nearest supported moving average");
    expect(t).toContain("crosses above the EMA of the close over 21 bars");
    expect(t).toContain("Inputs: Fast EMA = 9");
    expect(t).toContain("not translated: 'w' on line 4 is outside the subset");
    expect(html).toContain('data-testid="pine-run-0"');
    expect(html).not.toContain('data-testid="pine-run-1"');
    expect(t).toContain("never run");
    expect(t).not.toMatch(/\b(buy|sell|target|stop-loss|recommended)\b/i);
  });

  it("asks for symbols before a run and renders without a result", () => {
    const html = renderToStaticMarkup(<PinePanel symbols={[]} baseTf="1d" api={API} initialSource={SOURCE} initialResult={RESULT} initialTemplates={[]} />);
    expect(text(html)).toContain("Add symbols in the universe column");
    expect(html).toMatch(/data-testid="pine-run-0"[^>]*disabled|disabled[^>]*data-testid="pine-run-0"/);
    const empty = renderToStaticMarkup(<PinePanel symbols={[]} baseTf="1d" api={API} initialTemplates={[{ id: "ema_cross", name: "EMA cross", summary: "", source: "" }]} />);
    expect(text(empty)).toContain("EMA cross");
    expect(empty).not.toContain('data-testid="pine-status"');
  });
});
