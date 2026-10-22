/**
 * UI2-0: the pages whose content is market data or the trader's broker-backed figures. UI2-1 wraps each of them in
 * <BrokerGate> when `ui_v2` is on, so none of them ever shows sample or demo data in place of real data (owner brief
 * ATP_SESSION_NO_SAMPLE_DATA: "the Connect your broker / Log in to <Broker> gate on every data page").
 *
 * Not listed - they work without a broker session and show only the trader's own records or settings: Strategy
 * library and builder, Autopilot, Orders, Performance, Journal, Coach, Risk, News and events, Company research,
 * Contract specs, Marketplace, Settings, Team, Notifications, Activity log, Admin, Account.
 */
export interface DataPage {
  /** The route as the router writes it (`/charts` arrives with UI2-1). */
  path: string;
  /** Why the page needs the broker session. */
  why: string;
}

export const DATA_PAGES: readonly DataPage[] = [
  // UI2-2: the v2 dashboard shows these broker states itself, per block (src/shell/pagesV2.ts OWN_BROKER_STATES).
  { path: "/", why: "Market strip, today's P&L and open positions at live prices" },
  { path: "/charts", why: "Candles and the live price from the trader's broker" },
  { path: "/option-chain", why: "The option chain, OI and Greeks from the broker" },
  { path: "/options-builder", why: "Strikes, premiums and expiries from the broker's chain" },
  { path: "/screener", why: "Screens run on the organisation's broker data" },
  { path: "/scanner", why: "Scans run on broker candles" },
  { path: "/signals", why: "Signals priced at live market prices" },
  { path: "/positions", why: "Open positions marked at live prices" },
  { path: "/portfolio", why: "Holdings and exposure at live prices" },
  { path: "/backtest", why: "Backtests run on broker candles" },
  { path: "/quant", why: "Factor studies run on broker candles" },
  { path: "/copilot", why: "Market pulse and strategy ideas read live market data" },
];

/** True when `pathname` is a data page or one of its sub-paths (`/copilot/market-pulse`, `/charts/NIFTY`). */
export function isDataPath(pathname: string): boolean {
  const clean = `/${pathname.replace(/^\/+|\/+$/g, "")}`;
  return DATA_PAGES.some(({ path }) => (path === "/" ? clean === "/" : clean === path || clean.startsWith(`${path}/`)));
}
