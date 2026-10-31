import type { Page } from "@playwright/test";
import { readFileSync } from "node:fs";

/**
 * UI2-9a: the replies My positions, My portfolio and Risk limits read, for the e2e specs (hand-written, made-up data in
 * fixtures/trading_v2.json: no real account, price, order or id). Registered after mockApi() and before page.goto(), so
 * they answer first; a write the specs do not stub falls through to mockApi. Every write is recorded for the asserts.
 */
export type Fixture = Record<string, unknown>;
export const T = JSON.parse(readFileSync(new URL("./fixtures/trading_v2.json", import.meta.url), "utf8")) as Fixture;
const api = (path: string) => new RegExp(`/api(?:/v1)?${path}`);

export interface TradingMocks {
  /** No open positions and no trades. */
  empty?: boolean;
  /** /positions answers 404 (the read fails). */
  positionsFail?: boolean;
  /** /portfolio/exposure answers 500 (no live prices). */
  exposureFail?: boolean;
  /** Answer /paper/account and /paper/ladder (the paper account card with "Pause new PAPER entries"); left out: 404. */
  paperAccount?: boolean;
  /** Replaces fields of the /risk-settings reply. */
  riskSettings?: Record<string, number | string | null>;
  /** CP6-15: replaces fields of the /risk-settings/loss-streak reply (default: not paused, "Pause until the next session"). */
  lossStreak?: Record<string, unknown>;
}

/** CP6-15: the losses-in-a-row pause as the risk engine counts it (made-up). */
export const LOSS_STREAK_CALM = {
  mode: "next_session", mode_label: "Pause until the next session", limit: 3, losses_in_a_row: 1, paused: false, can_resume: false, ends: null,
  resumed_at: null, exits_blocked: false,
};

export interface Writes { calls: { method: string; url: string; body: unknown }[] }

export async function mockTrading(page: Page, { empty = false, positionsFail = false, exposureFail = false, paperAccount = false, riskSettings, lossStreak }: TradingMocks = {}): Promise<Writes> {
  const writes: Writes = { calls: [] };
  const exposure = empty
    ? { ...(T.exposure as object), open_positions: 0, gross_notional: 0, net_notional: 0, long_notional: 0, short_notional: 0, gross_pct_of_capital: 0,
        unrealised_pnl: 0, realised_today: 0, risk_at_stops: 0, risk_pct_of_capital: 0, largest_symbol_pct: 0, by_symbol: [], priced_symbols: [] }
    : T.exposure;
  let events = [...(T.market_events as { id: number }[])];
  let streak: Record<string, unknown> = { ...LOSS_STREAK_CALM, ...lossStreak };
  const gets: [RegExp, () => unknown][] = [
    [api("/auth/me$"), () => T.me],
    [api("/positions$"), () => (empty ? [] : T.positions)],
    [api("/trades(\\?|$)"), () => (empty ? [] : T.trades)],   // REV-1 M3: /trades is read by the page (?limit=)
    [api("/portfolio/exposure"), () => exposure],
    [api("/risk-settings$"), () => ({ ...(T.risk_settings as object), ...riskSettings })],
    [api("/risk-settings/ceilings$"), () => T.ceilings],
    [api("/risk-settings/loss-streak$"), () => streak],
    [api("/risk-guardian/status$"), () => T.guardian],
    [api("/risk-guardian/events(\\?|$)"), () => events],
    [api("/paper/risk$"), () => T.paper_risk],
    [api("/risk/limits$"), () => T.risk_limits],
    [api("/risk/events(\\?|$)"), () => T.risk_events],
    ...(paperAccount ? [[api("/paper/account$"), () => T.paper_account], [api("/paper/ladder$"), () => T.paper_ladder]] as [RegExp, () => unknown][] : []),
  ];
  await page.route(/\/api(?:\/v1)?\//, async (route) => {
    const request = route.request();
    const method = request.method();
    const url = request.url();
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    if (method === "GET") {
      if (positionsFail && api("/positions$").test(path)) return route.fulfill({ status: 404, json: { detail: "Not found" } });
      if (exposureFail && api("/portfolio/exposure").test(path)) return route.fulfill({ status: 500, json: { detail: "Made-up failure" } });
      const hit = gets.find(([re]) => re.test(path));
      return hit ? route.fulfill({ json: hit[1]() }) : route.fallback();
    }
    let body: unknown = null;
    try { body = request.postDataJSON(); } catch { body = request.postData(); }
    writes.calls.push({ method, url: path.replace(/^\/api(?:\/v1)?/, ""), body });
    if (method === "POST" && /\/positions\/\d+\/exit-now$/.test(path)) return route.fulfill({ json: T.exit_now });
    // TT-8: "Exit this leg" / "Exit the whole group" on the legs of one basket (made-up closes).
    const groupExit = path.match(/\/paper\/fno\/positions\/(\d+)\/exit-(leg|group)$/);
    if (method === "POST" && groupExit) {
      return route.fulfill({ json: { status: "CLOSED", group: "made-up", legs: [{ trade_id: Number(groupExit[1]), symbol: "Made-up leg", closed: true, exit_price: 41, pnl: -300, warnings: [] }] } });
    }
    if (method === "POST" && /\/risk-settings\/loss-streak\/resume$/.test(path)) {
      streak = { ...streak, losses_in_a_row: 0, paused: false, can_resume: false, ends: null, resumed_at: "2026-10-19T05:31:00+00:00" };
      return route.fulfill({ json: streak });
    }
    const del = path.match(/\/risk-guardian\/events\/(\d+)$/);
    if (method === "DELETE" && del) { events = events.filter((e) => e.id !== Number(del[1])); return route.fulfill({ status: 204, body: "" }); }
    return route.fallback();
  });
  return writes;
}
