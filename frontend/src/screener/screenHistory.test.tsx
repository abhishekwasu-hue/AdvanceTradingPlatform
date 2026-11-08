import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import ScreenHistoryPanel from "./ScreenHistoryPanel";
import { benchmarkLabel, isAsOf, isBacktest, isFitness, pctOf, rangeProblem, shareOf } from "./screenHistory";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const api = { asof: vi.fn(), fitness: vi.fn(), backtest: vi.fn() };
const props = { api, source: "RSI(14) > 60", symbols: ["TCS", "INFY"], scanTf: "1d", historyOn: true, backtestOn: true };

describe("FN2-j screen history", () => {
  it("words returns as percent and shares as whole percent", () => {
    expect(pctOf(0.0123)).toBe("+1.23%");
    expect(pctOf(-0.004)).toBe("−0.40%");
    expect(pctOf(null)).toBe("–");
    expect(shareOf(0.55)).toBe("55%");
    expect(shareOf(undefined)).toBe("–");
  });
  it("checks the date range", () => {
    expect(rangeProblem("", "2026-01-02")).toBe("Pick both dates.");
    expect(rangeProblem("2026-02-01", "2026-01-02")).toBe("The end date comes before the start date.");
    expect(rangeProblem("2026-01-01", "2026-01-02")).toBeNull();
  });
  it("guards replies and labels benchmarks", () => {
    expect(isAsOf({ matched: [], universe: 3 })).toBe(true);
    expect(isAsOf({})).toBe(false);
    expect(isFitness({ horizons: [] })).toBe(true);
    expect(isBacktest({ strategy: {}, equity: [] })).toBe(true);
    expect(isBacktest(null)).toBe(false);
    expect(benchmarkLabel("nifty_50")).toBe("NIFTY 50");
  });
  it("shows each section only while its flag is on", () => {
    const both = renderToStaticMarkup(<ScreenHistoryPanel {...props} />);
    expect(text(both)).toContain("When did this screen hit");
    expect(text(both)).toContain("Backtest");
    const hist = renderToStaticMarkup(<ScreenHistoryPanel {...props} backtestOn={false} />);
    expect(text(hist)).not.toContain("Run backtest");
    expect(renderToStaticMarkup(<ScreenHistoryPanel {...props} historyOn={false} backtestOn={false} />)).toBe("");
    expect(text(renderToStaticMarkup(<ScreenHistoryPanel {...props} historyOn={false} />))).not.toContain("Run backtest");
  });
  it("says why it cannot run: another timeframe or no condition", () => {
    expect(text(renderToStaticMarkup(<ScreenHistoryPanel {...props} scanTf="15m" />))).toContain("daily screens only");
    expect(text(renderToStaticMarkup(<ScreenHistoryPanel {...props} source=" " />))).toContain("Add a condition");
  });
});
