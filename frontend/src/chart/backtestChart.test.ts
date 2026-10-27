import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { backtestChartPlan, backtestTimeframe } from "./backtestChart";
import backtestPageSource from "../pages/BacktestPage.tsx?raw";

vi.mock("lightweight-charts", () => import("./lightweightFake.fixture"));
const { default: BacktestChartV2 } = await import("./BacktestChartV2");
const { AuthProvider } = await import("../auth/AuthContext");
const render = (props: Parameters<typeof BacktestChartV2>[0]) =>
  renderToStaticMarkup(createElement(AuthProvider, null, createElement(BacktestChartV2, props)));

describe("Chart review PR 3 (CC-5): Test a strategy on the v2 chart", () => {
  it("no parameters: EMA 9 / 21 and volume, nothing named", () => {
    expect(backtestChartPlan(null)).toEqual({ overlays: ["ema_fast", "ema_slow"], panes: { volume: true, rsi: false }, named: [] });
  });

  it("lines at the v2 chart's periods are drawn; other periods and the indicators it has not got are named", () => {
    const plan = backtestChartPlan({ ema_fast: 9, ema_slow: 50, rsi_period: 14, bb_period: 20, bb_k: 2, st_period: 10, st_mult: 3, adx_period: 14 });
    expect(plan.overlays).toEqual(["ema_fast"]);
    expect(plan.panes).toEqual({ volume: true, rsi: true });
    expect(plan.named).toEqual(["EMA 50", "Bollinger Bands 20 / 2", "Supertrend 10 / 3", "ADX 14"]);
    expect(backtestChartPlan({ rsi_period: 7 }).named).toContain("RSI 7");
    expect(backtestChartPlan({ rsi_period: 7 }).panes.rsi).toBe(false);
    expect(backtestChartPlan({ __id: "vwap_reversion" }).overlays).toContain("vwap");
  });

  it("the strategy's timeframe, or the default for one the chart does not know", () => {
    expect(backtestTimeframe("15min")).toBe("15min");
    expect(backtestTimeframe("day")).toBe("day");
    expect(backtestTimeframe("7min")).toBe("5min");
    expect(backtestTimeframe(undefined)).toBe("5min");
  });

  it("the chart names what it does not draw, in plain words", () => {
    const html = render({ candles: [], symbol: "DEMO", timeframe: "5min", markers: [], strategyParams: { bb_period: 20, bb_k: 2 } });
    expect(html).toContain("data-backtest-chart");
    expect(html).toContain("This strategy also reads Bollinger Bands 20 / 2. The chart does not draw it.");
    expect(html).toContain("Run a test to see its trades on the chart.");
    const none = render({ candles: [], symbol: "DEMO", markers: [] });
    expect(none).not.toContain("data-backtest-named");
  });

  it("the page: the v2 chart under ui_v2, ProChart without it, both with the same trade markers", () => {
    expect(backtestPageSource).toContain("const uiV2 = useUiV2();");
    expect(backtestPageSource).toMatch(/\{uiV2\s*\?\s*<BacktestChartV2 [^>]*markers=\{tradeMarkers\}/);
    expect(backtestPageSource).toMatch(/:\s*<ProChart [^>]*markers=\{tradeMarkers\}/);
  });
});
