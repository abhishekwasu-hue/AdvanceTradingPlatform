import type { MarketGroup, MarketList, MarketsOverview, Mover, Period, PeriodValues } from "./model";

/**
 * MO4 slice 2: answers shaped like `GET /api/markets/overview` and `/api/markets/lists/{key}` for the unit tests and the
 * e2e screenshots only (never imported by the app). Made-up symbols on purpose.
 */
const pv = (day: number | null, week: number | null, month: number | null): PeriodValues => ({ day, week, month });
const mover = (symbol: string, change: number | null): Mover => ({ symbol, exchange: "NSE", name: `${symbol[0]}${symbol.slice(1).toLowerCase()} Ltd`, change });
const per = <T,>(v: T): Record<Period, T> => ({ day: v, week: v, month: v });

function g(over: Partial<MarketGroup> & Pick<MarketGroup, "kind" | "name" | "change">): MarketGroup {
  return {
    list_key: null, symbol: null, exchange: null, family: null, family_label: null, close: null, method: "", count: 0,
    vs_benchmark: pv(null, null, null), breadth: null, strongest: per([]), weakest: per([]), known: true, ...over,
  };
}

const breadth = (advances: number, declines: number, unchanged = 0) => per({ advances, declines, unchanged, counted: advances + declines + unchanged, missing: 0 });

export const DEMO_OVERVIEW: MarketsOverview = {
  state: "ok", message: null, as_of: "2026-10-21", stale: false,
  periods: { day: "2026-10-20", week: "2026-10-14", month: "2026-09-21" },
  benchmark: { name: "NIFTY 50", change: pv(0.45, 1.2, -0.8) },
  families: [{ key: "BROAD", label: "Broad market" }, { key: "SECTORAL", label: "Sector indices" }, { key: "BSE", label: "BSE" }],
  indices: [
    g({ kind: "index", name: "NIFTY 50", symbol: "NIFTY 50", exchange: "NSE", family: "BROAD", family_label: "Broad market", close: 24812.35,
      list_key: "index-nifty50", count: 50, change: pv(0.45, 1.2, -0.8), vs_benchmark: pv(0, 0, 0), breadth: breadth(31, 18, 1),
      strongest: per([mover("ALPHA", 3.1)]), weakest: per([mover("OMEGA", -2.4)]) }),
    g({ kind: "index", name: "NIFTY BANK", symbol: "NIFTY BANK", exchange: "NSE", family: "SECTORAL", family_label: "Sector indices",
      close: 51230.1, list_key: "index-niftybank", count: 12, change: pv(1.1, 2.3, 0.4), vs_benchmark: pv(0.65, 1.1, 1.2),
      breadth: breadth(9, 3), strongest: per([mover("KAPPA", 2.2)]), weakest: per([mover("SIGMA", -0.6)]) }),
    g({ kind: "index", name: "NIFTY IT", symbol: "NIFTY IT", exchange: "NSE", family: "SECTORAL", family_label: "Sector indices",
      change: pv(null, null, null), known: false }),
    g({ kind: "index", name: "SENSEX", symbol: "SENSEX", exchange: "BSE", family: "BSE", family_label: "BSE", change: pv(null, null, null), known: false }),
  ],
  sectors: [
    g({ kind: "sector", name: "Banking", list_key: "sector-banking", count: 40, change: pv(0.9, 2.0, 0.2), vs_benchmark: pv(0.45, 0.8, 1.0),
      breadth: breadth(28, 11, 1), strongest: per([mover("KAPPA", 4.2), mover("DELTA", 3.0)]), weakest: per([mover("SIGMA", -2.1)]) }),
    g({ kind: "sector", name: "IT", list_key: "sector-it", count: 30, change: pv(-1.3, -0.4, -3.1), vs_benchmark: pv(-1.75, -1.6, -2.3),
      breadth: breadth(6, 23, 1), strongest: per([mover("THETA", 1.4)]), weakest: per([mover("ZETA", -4.6)]) }),
    g({ kind: "sector", name: "Pharma", list_key: "sector-pharma", count: 25, change: pv(0.2, 0.6, 1.4), vs_benchmark: pv(-0.25, -0.6, 2.2),
      breadth: breadth(13, 11, 1), strongest: per([mover("GAMMA", 2.5)]), weakest: per([mover("RHO", -1.9)]) }),
    g({ kind: "sector", name: "Realty", list_key: "sector-realty", count: 10, change: pv(null, null, null), known: false }),
  ],
  source: "NSE end-of-day closes (the daily history every user shares)",
  note: "How each index and sector moved at the close, for your own reading.",
};

export const DEMO_LIST: MarketList = {
  state: "ok", message: null, list_key: "sector-it", kind: "sector", name: "IT", symbol: null, as_of: "2026-10-21", stale: false,
  periods: DEMO_OVERVIEW.periods, benchmark: DEMO_OVERVIEW.benchmark,
  summary: { ...DEMO_OVERVIEW.sectors[1], method: "The average move of the sector's stocks (each stock counts the same)." },
  members: [
    { symbol: "THETA", exchange: "NSE", name: "Theta Ltd", close: 1520.5, change: pv(1.4, 2.0, -1.0), vs_benchmark: pv(0.95, 0.8, -0.2) },
    { symbol: "ZETA", exchange: "NSE", name: "Zeta Ltd", close: 842.1, change: pv(-4.6, -6.0, -9.1), vs_benchmark: pv(-5.05, -7.2, -8.3) },
    { symbol: "NODATA", exchange: "NSE", name: "No Data Ltd", close: null, change: pv(null, null, null), vs_benchmark: pv(null, null, null) },
  ],
  source: DEMO_OVERVIEW.source, note: DEMO_OVERVIEW.note,
};
