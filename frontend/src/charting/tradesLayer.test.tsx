import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { TradesLayer, TradesPanel } from "./TradesLayer";
import { TRADE_COLORS, TradesLoader, barAt, layerBars, toTradesLayer, type TradeMark, type TradesResponse, type TradesState } from "./tradesLayer";

const T0 = Date.parse("2026-09-01T04:00:00Z") / 1000;
const candles = Array.from({ length: 10 }, (_, i) => ({
  timestamp: new Date((T0 + i * 3600) * 1000).toISOString(), high: 105 + i, low: 95 + i,
}));
const bars = layerBars(candles);

const trade = (over: Partial<TradeMark> = {}): TradeMark => ({
  id: 1, symbol: "X", side: "long", mode: "PAPER", quantity: 10, on_price_axis: true, contract: null, strategy: "manual",
  entry: { time: T0 + 1800, price: 100 }, exit: null, open: true, stop_loss: 98, target: 104, pnl: null, label: "Buy 10 @ 100.00", ...over,
});
const resp = (trades: TradeMark[]): TradesResponse => ({
  schema: "trades/1", instrument: "NSE:X", at: "2026-09-02T00:00:00+00:00", start: "2026-09-01T00:00:00+00:00", mode: "PAPER",
  trades, open: trades.filter((t) => t.open).length, capped: false, note: "Your own PAPER trades, as recorded - for review only. The chart places no orders.",
});

describe("trades layer (N4 CH4a)", () => {
  it("puts a time in the bar it falls in, and nothing before the first bar", () => {
    expect(barAt(T0 + 1800, bars)?.time).toBe(T0);
    expect(barAt(T0 + 3600, bars)?.time).toBe(T0 + 3600);
    expect(barAt(T0 + 99 * 3600, bars)?.time).toBe(T0 + 9 * 3600);
    expect(barAt(T0 - 1, bars)).toBeNull();
  });

  it("draws an open trade at its own prices with its stop and target, and a closed one with an entry-to-exit segment", () => {
    const open = toTradesLayer(resp([trade()]), bars);
    expect(open.labels).toEqual([{ time: T0, price: 100, text: "▲ Buy 10 @ 100.00", color: TRADE_COLORS.long, position: "below" }]);
    expect(open.lines?.map((l) => [l.price, l.title])).toEqual([[98, "Stop 98.00"], [104, "Target 104.00"]]);
    const replay = toTradesLayer(resp([trade({ stop_loss: 97, stop_label: "initial stop" })]), bars);   // the past: not the trailed stop
    expect(replay.lines?.[0].title).toBe("Initial stop 97.00");
    const closed = toTradesLayer(resp([trade({ open: false, stop_loss: null, target: null, exit: { time: T0 + 3 * 3600 + 60, price: 102, reason: "TARGET" }, pnl: 20 })]), bars);
    expect(closed.labels?.[1]).toMatchObject({ time: T0 + 3 * 3600, price: 102, text: "✕ Exit @ 102.00 (TARGET) · P&L +20.00" });
    expect(closed.segments).toEqual([{ t1: T0, p1: 100, t2: T0 + 3 * 3600, p2: 102, color: TRADE_COLORS.long, width: 1, style: "dashed" }]);
    expect(closed.lines).toEqual([]);
  });

  it("marks a derived contract at its times only (bar low / high), never its premium on this axis", () => {
    const opt = trade({ symbol: "X26OCT100CE", on_price_axis: false, stop_loss: null, target: null, open: false, entry: { time: T0 + 2 * 3600, price: 4.5 },
                        exit: { time: T0 + 5 * 3600, price: 6, reason: null }, label: "Buy 10 X26OCT100CE @ 4.50", pnl: 75 });
    const data = toTradesLayer(resp([opt]), bars);
    expect(data.labels?.map((l) => l.price)).toEqual([97, 110]);                        // the bars' low (entry) and high (exit)
    expect(data.labels?.[1].text).toBe("✕ Exit X26OCT100CE · P&L +75.00");
    expect(data.segments).toEqual([]);
    expect(data.lines).toEqual([]);
    const short = toTradesLayer(resp([trade({ side: "short", label: "Sell 10 @ 100.00" })]), bars);
    expect(short.labels?.[0]).toMatchObject({ text: "▼ Sell 10 @ 100.00", color: TRADE_COLORS.short, position: "above" });
    expect(toTradesLayer(resp([trade({ entry: { time: T0 - 3600, price: 100 } })]), bars).labels).toEqual([]);   // before the chart
  });

  it("the loader drops a late answer, says why a request failed and hides everything on 503", async () => {
    const pending: { resolve: (r: TradesResponse) => void; reject: (e: unknown) => void }[] = [];
    const states: TradesState[] = [];
    const last = () => states[states.length - 1];
    const loader = new TradesLoader(() => new Promise((resolve, reject) => pending.push({ resolve, reject })), (s) => states.push(s));
    loader.load("NSE:X", null, null);
    loader.load("NSE:X", "2026-09-01T10:00:00Z", null);
    pending[0].resolve(resp([trade()]));
    await Promise.resolve(); await Promise.resolve();
    expect(last().resp).toBeNull();
    pending[1].reject(new ApiError(500, "boom"));
    await Promise.resolve(); await Promise.resolve();
    expect(last()).toMatchObject({ resp: null, failed: "boom" });
    loader.load("NSE:X", null, null);
    pending[2].reject(new ApiError(503, "off"));
    await Promise.resolve(); await Promise.resolve();
    expect(last()).toEqual({ resp: null, failed: null, available: false });
  });

  it("the panel is read-only: counts, a show / hide toggle, and no order control", () => {
    const html = renderToStaticMarkup(<TradesPanel resp={resp([trade(), trade({ id: 2, open: false })])} shown onShown={() => undefined} />);
    expect(html).toContain("2 PAPER trades · 1 open");
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain("read-only · places no orders");
    expect(html).not.toMatch(/\b(buy|sell|order now|exit now|close position)\b/i);
    expect(renderToStaticMarkup(<TradesPanel resp={resp([])} shown={false} onShown={() => undefined} />)).toContain("none on this chart");
    expect(renderToStaticMarkup(<TradesLayer engine={null} instrument="NSE:X" candles={candles} />)).toBe("");      // nothing until loaded
  });
});
