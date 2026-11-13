import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DEFAULT_REGISTRY } from "../inplace/panels";
import { NO_STOP, heldRows, ownStop, positionLevels } from "../trading/model";
import { trade } from "../trading/trading.fixture";
import { PositionChartStrip } from "./PositionChartV2";
import {
  fallsBack, noCandlesText, positionChartAreas, positionChartLevels, positionChartPath, positionChartTarget, underlyingExchange,
} from "./positionChart";

/** Owner bug report 13 Nov: a position's Chart button opens the contract held; made-up records ("DEMO ..." names). */
const SOLD_CE = trade({ id: 7, symbol: "DEMO 25150 CE 17 NOV 26", exchange: "NFO", instrument_kind: "OPTION", option_position: "WRITE",
  direction: "SHORT", entry_price: 174.6, quantity: 130, stop_loss: 0, target1: null, underlying_symbol: "DEMO IDX", expiry: "2026-11-17" });
const BOUGHT_CE = trade({ id: 8, symbol: "DEMO 25300 CE 17 NOV 26", exchange: "NFO", instrument_kind: "OPTION", option_position: "BUY",
  direction: "LONG", entry_price: 107.2, quantity: 130, stop_loss: 5, target1: null, underlying_symbol: "DEMO IDX", expiry: "2026-11-17" });
const FUT = trade({ id: 9, symbol: "DEMO 25 NOV FUT", exchange: "NFO", instrument_kind: "FUTURE", direction: "LONG", entry_price: 25_100,
  stop_loss: 24_950, target1: 25_400, underlying_symbol: "DEMO IDX" });
const STOCK = trade({ id: 3, symbol: "DEMO A", exchange: "NSE" });

describe("the Chart button opens the contract held", () => {
  it("an option: its own premium chart on its venue, with the position (not the underlying)", () => {
    expect(positionChartPath(SOLD_CE)).toBe("/charts/DEMO%2025150%20CE%2017%20NOV%2026?exchange=NFO&position=7");
    const [row] = heldRows([SOLD_CE], null);
    expect(row.chartSymbol).toBe("DEMO 25150 CE 17 NOV 26");
    expect(row.chartPath).toBe(positionChartPath(SOLD_CE));
  });

  it("a future: the future's own chart", () => {
    expect(positionChartTarget(FUT, "contract")).toEqual({ symbol: "DEMO 25 NOV FUT", exchange: "NFO", view: "contract" });
    expect(positionChartPath(FUT)).toBe("/charts/DEMO%2025%20NOV%20FUT?exchange=NFO&position=9");
  });

  it("a stock: the stock itself (it has no underlying; asking for one keeps the stock)", () => {
    expect(positionChartPath(STOCK)).toBe("/charts/DEMO%20A?position=3");
    expect(positionChartTarget(STOCK, "underlying")).toEqual({ symbol: "DEMO A", exchange: "NSE", view: "contract" });
  });

  it("the one-tap switch: the underlying on its own venue, and the label when the contract had no candles", () => {
    expect(positionChartPath(SOLD_CE, "underlying")).toBe("/charts/DEMO%20IDX?position=7&on=underlying");
    expect(positionChartPath(SOLD_CE, "underlying", true)).toBe("/charts/DEMO%20IDX?position=7&on=underlying&nocandles=1");
    expect(underlyingExchange("BFO")).toBe("BSE");
    expect(underlyingExchange("NFO")).toBe("NSE");
  });

  it("opens in place: the chart panel carries the position and writes the same address back", () => {
    const at = DEFAULT_REGISTRY.resolve(positionChartPath(SOLD_CE));
    expect(at).toEqual({ kind: "chart", params: { symbol: "DEMO 25150 CE 17 NOV 26", exchange: "NFO", position: "7" } });
    const under = DEFAULT_REGISTRY.resolve(positionChartPath(SOLD_CE, "underlying", true));
    expect(under?.params).toEqual({ symbol: "DEMO IDX", position: "7", on: "underlying", nocandles: "1" });
    expect(DEFAULT_REGISTRY.get("chart")!.href(under?.params ?? {})).toBe(positionChartPath(SOLD_CE, "underlying", true));
    // an ordinary chart address reads exactly as before
    expect(DEFAULT_REGISTRY.resolve("/charts/NIFTY%2050?on=underlying")?.params).toEqual({ symbol: "NIFTY 50" });
  });
});

describe("the position's lines on its chart", () => {
  it("the contract: the average and the stop he set on the premium; a stop he did not set draws nothing", () => {
    expect(positionChartLevels(SOLD_CE, "contract")).toEqual([{ price: 174.6, title: "Avg", tone: "neutral" }]);
    expect(positionChartLevels(BOUGHT_CE, "contract")).toEqual([
      { price: 107.2, title: "Avg", tone: "neutral" }, { price: 5, title: "Premium floor", tone: "down" }]);
    expect(positionChartLevels({ ...SOLD_CE, stop_loss: 210 }, "contract")[1]).toEqual({ price: 210, title: "Premium ceiling", tone: "down" });
  });

  it("a future and a stock: average, stop and targets on their own price", () => {
    expect(positionChartLevels(FUT, "contract").map((l) => `${l.title} ${l.price}`)).toEqual(["Avg 25100", "Stop 24950", "Target 25400"]);
    expect(positionChartLevels(STOCK, "contract").map((l) => l.title)).toEqual(["Avg", "Stop", "Target"]);
  });

  it("the underlying: its own stop / targets (never the premium) and the short strike's area as a band", () => {
    const withLevels = { ...SOLD_CE, underlying_stop_loss: 25_300, underlying_target1: 24_800, underlying_target2: 24_600 };
    expect(positionChartLevels(withLevels, "underlying").map((l) => `${l.title} ${l.price}`)).toEqual(["Stop 25300", "Target 1 24800", "Target 2 24600"]);
    expect(positionChartLevels({ ...SOLD_CE, underlying_stop_loss: 0 }, "underlying")).toEqual([]);
    const area = { low: 24_989.35, high: 25_141.3, state: "live", where: "below" as const, distance_pct: 0.03, range_text: "24,989.35-25,141.30", from: "strike" as const, change: null };
    expect(positionChartAreas(area, "underlying")).toEqual([{ low: 24_989.35, high: 25_141.3, role: "neutral", label: "Area" }]);
    expect(positionChartAreas(area, "contract")).toEqual([]);
  });
});

describe("a contract without candles: its underlying, said plainly", () => {
  const read = (extra = {}) => ({ key: "NFO:DEMO 25150 CE 17 NOV 26", loading: false, count: 0, empty: "No candles yet for this contract.", error: null, ...extra });
  const KEY = "NFO:DEMO 25150 CE 17 NOV 26";

  it("falls back only when THIS contract's read finished with no bars", () => {
    expect(fallsBack(SOLD_CE, "contract", read(), KEY)).toBe(true);
    expect(fallsBack(SOLD_CE, "contract", read({ empty: null, error: "Log in to your broker." }), KEY)).toBe(true);
    expect(fallsBack(SOLD_CE, "contract", read({ loading: true }), KEY)).toBe(false);
    expect(fallsBack(SOLD_CE, "contract", read({ count: 40 }), KEY)).toBe(false);
    expect(fallsBack(SOLD_CE, "contract", read({ empty: null }), KEY)).toBe(false);
    expect(fallsBack(SOLD_CE, "contract", read({ key: "NSE:DEMO IDX" }), KEY)).toBe(false);   // the previous chart's read
    expect(fallsBack(SOLD_CE, "underlying", read(), KEY)).toBe(false);
    expect(fallsBack(STOCK, "contract", read(), KEY)).toBe(false);                         // a stock has nothing to fall back to
  });

  it("the label names the contract and the underlying, with a way back; the switch shows both charts", () => {
    expect(noCandlesText(SOLD_CE)).toBe("No candles for DEMO 25150 CE 17 NOV 26 from your broker. This chart is DEMO IDX, the underlying.");
    const html = renderToStaticMarkup(<PositionChartStrip position={SOLD_CE} view="underlying" noCandles ltp={123.1} onPick={() => undefined} />);
    expect(html).toContain("data-no-candles");
    expect(html).toContain(noCandlesText(SOLD_CE));
    expect(html).toContain("Try the option chart again");
    expect(html).toMatch(/aria-pressed="true"[^>]*>DEMO IDX</);
    const own = renderToStaticMarkup(<PositionChartStrip position={SOLD_CE} view="contract" noCandles={false} ltp={123.1} onPick={() => undefined} />);
    expect(own).not.toContain("data-no-candles");
    expect(own).toMatch(/aria-pressed="true"[^>]*>Option</);
    expect(own).toContain("174.60");
    expect(own).toContain("123.10");
    const under = renderToStaticMarkup(<PositionChartStrip position={SOLD_CE} view="underlying" noCandles={false} ltp={null} onPick={() => undefined} />);
    expect(under).toContain("DEMO IDX, the underlying of your DEMO 25150 CE position.");
  });
});

describe("the premium ceiling / floor chip shows only a stop he set", () => {
  const premium = (p: typeof SOLD_CE) => positionLevels(p).levels[2];

  it("a sold option stored with 0 (no stop, DEF-0 D7): the one Set chip, never 'Premium ceiling 0.00'", () => {
    expect(ownStop(SOLD_CE)).toBeNull();
    expect(premium(SOLD_CE)).toEqual({ label: "Premium ceiling", value: NO_STOP, tone: "neutral" });
    expect(premium({ ...SOLD_CE, stop_loss: 210 }).value).toBe("210.00");
  });

  it("a bought option: his own floor shows; the one-tick placeholder does not", () => {
    expect(premium(BOUGHT_CE)).toEqual({ label: "Premium floor", value: "5.00", tone: "neutral" });
    expect(premium({ ...BOUGHT_CE, stop_loss: 0.05 }).value).toBe(NO_STOP);
  });

  it("a future: a short's 0 and a long's one tick are no stop; a stock's 0 is none", () => {
    expect(positionLevels({ ...FUT, direction: "SHORT", stop_loss: 0 }).levels[2].value).toBe(NO_STOP);
    expect(positionLevels({ ...FUT, stop_loss: 0.1 }).levels[2].value).toBe(NO_STOP);
    expect(positionLevels(FUT).levels[2].value).toBe("24,950.00");
    expect(positionLevels({ ...STOCK, stop_loss: 0 }).levels[0].value).toBe(NO_STOP);
    expect(positionLevels(STOCK).levels[0].value).toBe("95.00");
  });

  it("an underlying stop stored as 0 is no stop either", () => {
    expect(positionLevels({ ...SOLD_CE, underlying_stop_loss: 0 }).levels[0].value).toBe(NO_STOP);
  });
});
