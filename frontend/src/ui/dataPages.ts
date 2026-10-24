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
  // UX3-9: Strategies › Test, the backtest's new address (`/positions/money`, the portfolio, is under /positions).
  { path: "/strategies/test", why: "Backtests run on broker candles" },
  { path: "/quant", why: "Factor studies run on broker candles" },
  { path: "/copilot", why: "Market pulse and strategy ideas read live market data" },
];

/**
 * Owner order 23 Oct (flag `backtest_stored_history`): the backtest pages run Daily backtests on ATP's stored history when
 * no broker is connected, so with the flag on they show the broker state themselves (the data bar: "No broker
 * connected: Daily backtests use ATP's stored end-of-day history ...") instead of the gate.
 */
export const STORED_HISTORY_PATHS: readonly string[] = ["/backtest", "/strategies/test"];

/**
 * Day-one fix 2 (README row 19, flag `backtest_stored_history`): pages whose Daily parts run on ATP's stored end-of-day
 * history before the day's broker login. While only the login (or the broker) is missing they show their content under
 * one line with the one-tap login (`StoredDailyNote`) instead of the full gate; the sentence says what still works.
 */
export const STORED_DAILY_PAGES: Readonly<Record<string, string>> = {
  "/screener": "Until then, Daily scans run on the closed Daily bars in ATP's stored end-of-day history; intraday scans need the login.",
  "/signals": "Until then, your Daily watch reads ATP's stored end-of-day history; your signals and approvals are all here.",
};

/** The stored-Daily sentence for a path (its sub-paths too), or null. */
export function storedDailyFor(pathname: string): string | null {
  const clean = `/${pathname.replace(/^\/+|\/+$/g, "")}`;
  const hit = Object.keys(STORED_DAILY_PAGES).find((p) => clean === p || clean.startsWith(`${p}/`));
  return hit ? STORED_DAILY_PAGES[hit] : null;
}

/**
 * UX3-9: sub-paths of a data page that are not data pages themselves - My trades › Orders (`/positions/orders`) is the
 * trader's own order record, which worked without a broker session at `/orders` and still does.
 */
export const NOT_DATA_SUBPATHS: readonly string[] = ["/positions/orders"];

/** True when `pathname` is a data page or one of its sub-paths (`/copilot/market-pulse`, `/charts/NIFTY`). */
export function isDataPath(pathname: string): boolean {
  const clean = `/${pathname.replace(/^\/+|\/+$/g, "")}`;
  if (NOT_DATA_SUBPATHS.includes(clean)) return false;
  return DATA_PAGES.some(({ path }) => (path === "/" ? clean === "/" : clean === path || clean.startsWith(`${path}/`)));
}
