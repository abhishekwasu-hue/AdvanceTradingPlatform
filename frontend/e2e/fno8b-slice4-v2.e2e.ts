import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi, type MockCalls, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading } from "./tradingMocks";

/**
 * F&O workspace 8b, slice 4 (owner order ATP_SESSION_FNO_WORKSPACE.md sections 8b d, i, k and 9), behind `ui_v2` with the
 * PAPER account on. Every reply is made-up data (round numbers; no real account, price, order or id).
 * - The risk line's other parts (8b-d): the events calendar up to the expiry, the strike's liquidity from the chain on
 *   screen and the margin beside the PAPER balance, as text on the ticket's / basket's confirm step - nothing reads them.
 * - The F&O desk (8b-i): on a desktop the cockpit with a chart of the underlying, the chain near his strikes and his
 *   basket on one screen; on a phone a quick switch. Read only; every Exit stays on the position rows.
 * - The journal's chart snapshot (8b-k): drawn on his tap from the underlying's candles (one read); nothing is stored.
 * FNO-8B-28: with his own liquidity limits on (Settings -> My trading defaults, OFF by default) the liquidity line also
 *   says whether the strike is inside them - words only. FNO-8B-31: the journal chart's "Download image" saves a PNG on
 *   his device with a plain file name; nothing is sent (the `fno8b4-journal-chart-v2-*` baselines show the button).
 * Baselines (NEW): `fno8b4-ticket-checks-v2-<360|390|1440>-<theme>.png`, `fno8b4-desk-v2-*` and `fno8b4-journal-chart-v2-*`.
 * After an intended change: `npx playwright test --project=phone e2e/fno8b-slice4-v2.e2e.ts --update-snapshots`; the
 * `fno8b4-journal-chart-v2-*` files are pinned in scripts/check_public_repo.py (ALLOWED_TEST_DATA: made-up trades).
 */
const THEMES = ["light", "dark"] as const;
const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const SIZES = [{ width: 360, height: 800, phone: true }, { width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }];
const api = (path: string) => new RegExp(`/api(?:/v1)?${path}`);
const FONT = [
  "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
  "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
].join("\n");

// --- the chain: "Trade this setup" -------------------------------------------------------------------------------------
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown> & { rows: unknown[] };
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, paper_account: true }, brokers: CONNECTED };
const LOT = 75;
const SPREAD_ITEM = { id: 7, settings: "Bull put spread · main strike 1 strike out of the money · wing 2 strikes away · nearest expiry", expiry: "2026-11-03",
  legs: [{ label: "Main strike", right: "PE", strike: 24400, ratio: 1, role: "short" }, { label: "Wing", right: "PE", strike: 24200, ratio: 1, role: "long" }],
  note: null, lots: 2, stop_pct: 50 };
const SINGLE_ITEM = { id: 8, settings: "Single call option · main strike at the money · nearest expiry", expiry: "2026-11-03",
  legs: [{ label: "Main strike", right: "CE", strike: 24500, ratio: 1, role: "long" }], note: null, lots: null, stop_pct: null };
const setup = (items: unknown[]) => ({ source: "your_settings", signal: "action:12", underlying: "NIFTY", setup: "Made-up setup", side: "bull_put",
  reference: { text: "the current price", price: 24480 }, message: null, items });
const CONTRACT = { underlying: "NIFTY", tradingsymbol: "NIFTY26N0324500CE", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03",
  strike: 24500, right: "CE", lot_size: LOT, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null };

type Leg = { underlying: string; expiry: string; strike: number; right: "CE" | "PE"; side: "BUY" | "SELL"; lots: number; price: number | null; stop_loss: number | null };
const label = (l: Leg) => `${l.underlying} 03 NOV ${l.strike} ${l.right}`;
const hedgeFirst = (legs: Leg[]) => legs.map((l, i) => ({ l, i })).sort((a, b) => (a.l.side === b.l.side ? a.i - b.i : a.l.side === "BUY" ? -1 : 1));

/** A made-up engine answer in the engine's shape (a flat payoff is enough here). */
function evaluation(legs: Leg[]) {
  const prices = Array.from({ length: 41 }, (_, i) => 23500 + i * 50);
  const net = legs.reduce((s, l) => s + (l.side === "SELL" ? 1 : -1) * (l.price ?? 0) * l.lots * LOT, 0);
  const y = prices.map((p) => legs.reduce((s, l) => {
    const v = l.right === "CE" ? Math.max(p - l.strike, 0) : Math.max(l.strike - p, 0);
    return s + (l.side === "BUY" ? v - (l.price ?? 0) : (l.price ?? 0) - v) * l.lots * LOT;
  }, 0));
  return {
    ok: true, errors: [], prices, at_expiry: y, today: y, on_date: y, days_forward: 0, iv_shift: 0, single_expiry: true,
    extremes: { max_profit: Math.max(...y), max_loss: Math.min(...y), unbounded_profit: false, unbounded_loss: false }, profitable: [], breakevens: [24300],
    summary: { expiry: "2026-11-03", t_years: 0.02, sigma: 0.12, pop: 0.6, expected_move: 400, expected_pnl: 100,
      greeks: { delta: 10, gamma: -0.002, theta: 300, vega: -400, rho: 1 }, method: "", model: "black-scholes" },
    legs: [], disclaimer: "Model estimates for research; not a forecast.", net_premium: net, reward_to_risk: null,
    margin: { value: -Math.min(...y), estimated: true, basis: "Defined risk: at least the max loss.", note: "",
      hedge: { with_hedge: { value: -Math.min(...y), estimated: true, basis: "" }, without_hedge: null, benefit: null, hedged: true, source: "estimate", note: "" } },
    costs: { to_open: { stt: 5, exch: 4, sebi: 0, stamp: 0, brokerage: 40, tax: 8, total: 57, other: 12 }, round_trip: 114, brokerage_per_order: 20, orders: legs.length, as_of: "2026-10-27", note: "" },
    lot_sizes: legs.map(() => LOT), labels: legs.map(label), order: hedgeFirst(legs).map((x) => x.i),
  };
}
function basketPreview(legs: Leg[]) {
  const ev = evaluation(legs);
  return {
    mode: "PAPER", practice: "Practice mode (PAPER)", product: "MIS", as_of: "2026-10-27T05:00:00+00:00", refusals: [], totals_errors: [],
    legs: hedgeFirst(legs).map(({ l, i }, k) => ({
      seq: k + 1, leg: i, role: l.side === "BUY" ? "hedge" : "short", mode: "PAPER", practice: "", contract: null, label: label(l), side: l.side, lots: l.lots,
      lot_size: LOT, units: l.lots * LOT, order_type: "MARKET", product: "MIS", price: l.price, price_source: "live", fill_price: l.price, fill_note: null,
      stop_loss: l.stop_loss, margin: null, max_loss: null, charges: null, risk: {}, errors: [], refusals: [], warnings: [], can_place: true, confirm_label: null, as_of: "" })),
    totals: { margin: ev.margin, extremes: ev.extremes, net_premium: ev.net_premium, costs: 57, breakevens: ev.breakevens },
    can_execute: true, confirm_label: `Place ${legs.length} PAPER orders, hedge legs first`,
  };
}
function ticketPreview(body: Record<string, unknown>) {
  const lots = Number(body.lots ?? 1);
  return {
    mode: "PAPER", practice: "Practice mode (PAPER): no real order is sent and no money moves.", contract: CONTRACT, label: CONTRACT.label,
    side: body.side, lots, lot_size: LOT, units: lots * LOT, order_type: body.order_type, product: body.product, price: 150, price_source: "live", fill_price: 150,
    fill_note: null, stop_loss: body.stop_loss ?? null, margin: { value: 150 * lots * LOT, basis: "", estimated: true },
    max_loss: { value: 150 * lots * LOT, basis: "" }, charges: { total: 42.5, estimated: true }, risk: {}, errors: [], refusals: [], warnings: [], can_place: true,
    confirm_label: null, as_of: "2026-10-27T05:00:00+00:00",
  };
}

interface ChainSeen { posts: MockCalls["posts"] }

async function openChain(page: Page, theme: "light" | "dark", path: string, opts: { items?: unknown[]; basket?: unknown[]; fno?: unknown } = {}): Promise<ChainSeen> {
  await page.clock.install({ time: new Date("2026-10-27T10:30:00+05:30") });
  await page.addInitScript(([t, basket]) => {
    try {
      if (sessionStorage.getItem("e2e_seeded")) return;
      sessionStorage.setItem("e2e_seeded", "1");
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["option-chain"]));
      sessionStorage.setItem("atp_fno_basket", JSON.stringify(basket));
    } catch { /* storage unavailable */ }
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
  }, [theme, opts.basket ?? []] as const);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const calls = await mockApi(page, ON);
  const seen: ChainSeen = { posts: calls.posts };
  const sessions = { NSE: { is_open: true, reason: "made up", next_open: null } };
  await page.route(api("/system/status$"), (r) => r.fulfill({ json: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions } }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/expiries")) return route.fulfill({ json: DATA.expiries });
    return route.fulfill({ json: { ...DATA.live, expiry: url.searchParams.get("expiry") ?? DATA.live.expiry, market_open: true } });
  });
  await page.route(api("/me/prefs"), (r) => r.fulfill({ json: { prefs: { fno_baskets: { items: {} }, ...(opts.fno ? { fno: opts.fno } : {}) },
    versions: { fno_baskets: 0, fno: opts.fno ? 1 : 0 } } }));
  await page.route(/\/api(?:\/v1)?\/paper\/fno\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET") {
      if (url.pathname.endsWith("/contract")) return route.fulfill({ json: { ...CONTRACT, strike: Number(url.searchParams.get("strike")), right: url.searchParams.get("right") } });
      if (url.pathname.endsWith("/held")) return route.fulfill({ json: [] });
      if (url.pathname.endsWith("/risk-budget")) return route.fulfill({ json: { capital: 500000, per_trade: { amount: 5000, pct: 1 }, daily: { amount: 15000, pct: 3 }, source: "Your risk settings", paper_balance: { amount: 498000, source: "x", open_margin_counted: false }, blocks: false } });
      if (url.pathname.endsWith("/setup-strikes")) return route.fulfill({ json: setup(opts.items ?? [SPREAD_ITEM]) });
      if (url.pathname.endsWith("/basket/settings")) return route.fulfill({ json: { source: null, width_steps: 2, lots: 1, stop_pct: 50, note: "A neutral start is used. Edit any leg." } });
      return route.fulfill({ status: 404, json: { detail: "Not found" } });
    }
    const body = request.postDataJSON() as Record<string, unknown> & { legs: Leg[] };
    seen.posts.push({ url: url.pathname, method: request.method(), body });
    if (url.pathname.endsWith("/journal/link")) return route.fulfill({ json: { linked: body.trade_ids, message: null } });
    if (url.pathname.endsWith("/basket/evaluate")) return route.fulfill({ json: evaluation(body.legs) });
    if (url.pathname.endsWith("/basket/preview")) return route.fulfill({ json: basketPreview(body.legs) });
    if (url.pathname.endsWith("/basket/execute")) {
      return route.fulfill({ json: { mode: "PAPER", status: "PLACED", reasons: [], placed: body.legs.length, next_steps: [],
        legs: hedgeFirst(body.legs).map(({ l, i }, k) => ({ seq: k + 1, leg: i, label: label(l), side: l.side, lots: l.lots, status: "PLACED", reasons: [],
          filled: { price: l.price, quantity: l.lots * LOT, lots: l.lots }, order_id: 10 + k, trade_id: 20 + k })) } });
    }
    if (url.pathname.endsWith("/order/preview")) return route.fulfill({ json: ticketPreview(body) });
    if (url.pathname.endsWith("/order")) return route.fulfill({ json: { ...ticketPreview(body), status: "PLACED", reasons: [], order_id: 9, trade_id: 41, filled: { price: 150, quantity: 75, lots: 1 } } });
    return route.fulfill({ json: { available: false, why: "Not here." } });
  });
  await extraMocks(page);
  await page.goto(path);
  await page.addStyleTag({ content: FONT });
  await expect(page.locator("tr[data-atm]")).toBeVisible();
  return seen;
}

/** Anything that would place or change an order (the engine's numbers and the journal note are not). */
const orderCalls = (seen: ChainSeen) => seen.posts.filter((p) => /\/paper\/fno\/(order|basket\/(preview|execute))/.test(p.url));
const FROM_SIGNAL = "/option-chain?underlying=NIFTY&signal=action:12";

// --- My positions: the cockpit and the journal -----------------------------------------------------------------------
const SHORT_CE = "NIFTY 24900 CE 27 OCT 26";
const LONG_CE = "NIFTY 25000 CE 27 OCT 26";
const SHORT_PE = "NIFTY 24500 PE 27 OCT 26";
const contract = (symbol: string, right: "CE" | "PE", strike: number) => ({
  symbol, exchange: "NFO", known: true, listed: true, underlying: "NIFTY", lot_size: 75, spot_symbol: "NIFTY 50", spot_exchange: "NSE",
  expired: false, days_left: 8, roll: null, roll_note: null, expiry: "2026-10-27", right, strike, label: `NIFTY 27 OCT ${strike} ${right}`,
});
const FACTS: Record<string, unknown> = { [SHORT_CE]: contract(SHORT_CE, "CE", 24900), [LONG_CE]: contract(LONG_CE, "CE", 25000), [SHORT_PE]: contract(SHORT_PE, "PE", 24500) };
const position = (id: number, symbol: string, direction: "LONG" | "SHORT", entry: number, stop: number) => ({
  id, mode: "PAPER", symbol, strategy_id: "manual_fno", direction, entry_time: "2026-10-19T04:30:00", entry_price: entry, quantity: 75, stop_loss: stop,
  target1: null, target2: null, exit_time: null, exit_price: null, exit_reason: null, pnl: null, charges: 0, lot_size: 75, exchange: "NFO",
  instrument_kind: "OPTION", option_position: direction === "LONG" ? "BUY" : "WRITE", underlying_symbol: "NIFTY 50", expiry: "2026-10-27",
});
const POSITIONS = [position(301, SHORT_CE, "SHORT", 80, 120), position(302, LONG_CE, "LONG", 45, 20), position(303, SHORT_PE, "SHORT", 60, 90)];
const LTP: Record<string, number> = { [SHORT_CE]: 72.5, [LONG_CE]: 41, [SHORT_PE]: 33 };
const EXPOSURE = (() => {
  const e = T.exposure as Record<string, unknown>;
  const rows = POSITIONS.map((p) => ({ symbol: p.symbol, positions: 1, quantity: 75, notional: 75 * LTP[p.symbol], pct_of_capital: 1, unrealised_pnl: 0,
    risk_at_stop: 0, strategies: ["manual_fno"] }));
  return { ...e, open_positions: 3, by_symbol: rows, priced_symbols: rows.map((r) => r.symbol) };
})();
const GREEKS = { as_of: "2026-10-19T05:30:00+00:00", spot: { "NIFTY 50": 24850 }, legs: [], groups: [], skipped: [],
  net: { net_delta: -18.25, net_gamma: -0.0042, net_theta: 412.5, net_vega: -930.75 } };
const RULES = [
  { id: 5, name: "NIFTY 50 at or above 24,935 (your breakeven)", kind: "instrument", screen_id: null, symbol: "NIFTY 50", condition: "close >= 24935",
    base_tf: "1m", priority: "normal", cooldown_minutes: 60, mode: "instant", digest_every: "hourly", status: "active", expires_at: null },
  { id: 6, name: "BANKNIFTY made-up rule", kind: "instrument", screen_id: null, symbol: "NIFTY BANK", condition: "close >= 1",
    base_tf: "1m", priority: "normal", cooldown_minutes: 60, mode: "instant", digest_every: "hourly", status: "active", expires_at: null },
];
const DRAFTS = { mode: "PAPER", days: 14, items: [{
  key: "basket:made-up", kind: "basket", title: "Basket · 2 legs", underlying: "NIFTY", trade_ids: [211, 212],
  opened_at: "2026-10-16T04:45:00+00:00", closed_at: "2026-10-16T08:50:00+00:00",
  legs: [
    { trade_id: 211, symbol: "NIFTY 24400 PE 20 OCT 26", side: "short", quantity: 150, lot_size: 75, entry_price: 136, entry_at: null, exit_price: 96, exit_at: null, exit_reason: "MANUAL_EXIT", pnl: 6000, charges: 40 },
    { trade_id: 212, symbol: "NIFTY 24200 PE 20 OCT 26", side: "long", quantity: 150, lot_size: 75, entry_price: 108, entry_at: null, exit_price: 80, exit_at: null, exit_reason: "MANUAL_EXIT", pnl: -4200, charges: 40 },
  ],
  pnl: 1800, charges: 80, placed_by: "you", strategy: null,
  signal: { setup: "Made-up setup", words: ["The pullback reached a seller area", "A handover is forming inside the area"] }, notes: null, status: "draft",
}] };

interface PosSeen { writes: { method: string; url: string; body: unknown }[] }

async function openPositions(page: Page, theme: "light" | "dark"): Promise<PosSeen> {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts", "positions"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ...(T.features as Record<string, boolean>), paper_account: true, screener_v2: true }, brokers: T.token_status as unknown[] });
  const writes = await mockTrading(page, { paperAccount: true });
  const seen: PosSeen = { writes: writes.calls };
  await page.route(api("/positions$"), (r) => r.fulfill({ json: POSITIONS }));
  await page.route(api("/portfolio/exposure"), (r) => r.fulfill({ json: EXPOSURE }));
  await page.route(api("/positions/greeks$"), (r) => r.fulfill({ json: GREEKS }));
  await page.route(api("/paper/fno/contracts$"), (r) => {
    const items = (r.request().postDataJSON() as { items: { symbol: string; exchange: string }[] }).items;
    return r.fulfill({ json: { items: items.map((i) => FACTS[i.symbol] ?? { symbol: i.symbol, exchange: i.exchange, known: false }) } });
  });
  await page.route(api("/paper/fno/positions/groups$"), (r) => r.fulfill({ json: { groups: [{ key: "basket:made-up", kind: "basket", name: "Basket", trade_ids: [301, 302] }] } }));
  await page.route(api("/paper/fno/risk-budget$"), (r) => r.fulfill({ json: { capital: 1_000_000, per_trade: { amount: 10_000, pct: 1 }, daily: { amount: 30_000, pct: 3 }, source: "Your risk settings", paper_balance: { amount: 498000, source: "x", open_margin_counted: false }, blocks: false } }));
  await page.route(api("/paper/fno/journal/drafts$"), (r) => r.fulfill({ json: DRAFTS }));
  await page.route(api("/alerts/rules$"), (r) => r.fulfill({ json: RULES }));
  await page.route(api("/trades/\\d+/journal$"), (r) => {
    seen.writes.push({ method: r.request().method(), url: r.request().url().replace(/^.*\/api(?:\/v1)?/, ""), body: r.request().postDataJSON() });
    return r.fulfill({ json: { id: 211 } });
  });
  await page.route(api("/market-data/quotes$"), (r) => {
    const items = (r.request().postDataJSON() as { items: { symbol: string; exchange: string }[] }).items;
    return r.fulfill({ json: { items: items.map((i) => ({ ...i, ltp: i.symbol === "NIFTY 50" ? 24850 : null, change: null, change_pct: null, missing: i.symbol !== "NIFTY 50" })) } });
  });
  await extraMocks(page, true);
  await page.goto("/positions");
  await page.addStyleTag({ content: FONT });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return seen;
}

// --- slice 4 mocks: the events calendar, a made-up price history, the PAPER balance -----------------------------------
const EVENTS = { underlying: "NIFTY", from: "2026-10-27", until: "2026-11-03", clipped: false,
  events: [{ date: "2026-10-30", kind: "RBI_POLICY", name: "RBI policy", scope: "all indices", rule: "Your event rule that day: a smaller size.", source: "market" }],
  more: 0, calendar_empty: false, message: null, source: "Your events calendar", blocks: false };
async function extraMocks(page: Page, chain = false): Promise<void> {
  if (chain) await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/live/, (r) => r.fulfill({ json: { ...DATA.live, underlying: "NIFTY", expiry: "2026-10-27", market_open: true } }));
  await page.route(api("/paper/fno/event-risk"), (r) => r.fulfill({ json: EVENTS }));
  await page.route(api("/market-data/candles$"), (r) => {
    const body = r.request().postDataJSON() as { symbols: string[]; timeframe: string };
    const sym = body.symbols[0];
    const start = Date.parse("2026-10-16T03:00:00Z");
    const step = body.timeframe === "5min" ? 5 : body.timeframe === "15min" ? 15 : body.timeframe === "60min" ? 60 : 1440;
    const bars = Array.from({ length: 160 }, (_, i) => {
      const c = 24800 + Math.round(120 * Math.sin(i / 9) + i * 0.8);
      return { timestamp: new Date(start + i * step * 60_000).toISOString(), open: c - 6, high: c + 14, low: c - 18, close: c + (i % 3 === 0 ? -4 : 5), volume: 1000 };
    });
    return r.fulfill({ json: { source: { broker: "upstox", account_label: "primary" }, exchange: "NSE", timeframe: body.timeframe, base_interval: body.timeframe,
      lookback_days: 5, fetched_at: "2026-10-19T05:30:00Z", symbols: { [sym]: { bars, count: bars.length, first: null, last: null, error: null } }, warnings: [], note: "" } });
  });
}

// --- baselines --------------------------------------------------------------------------------------------------------
for (const size of SIZES) {
  test.describe(`8b slice 4 at ${size.width}`, () => {
    test.use(size.phone ? { viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size });
    for (const theme of THEMES) {
      test(`the ticket's risk parts look the same (${theme})`, async ({ page }) => {
        await openChain(page, theme, FROM_SIGNAL, { items: [SINGLE_ITEM] });
        await page.locator("[data-setup-item='8'] [data-setup='trade']").click();
        const ticket = page.locator("[data-order-ticket]");
        await ticket.locator("[data-ticket='review']").click();
        const checks = ticket.locator("[data-risk-checks='ticket']");
        await expect(checks).toContainText("RBI policy on 30 Oct 2026");
        await expect(checks).toContainText("Margin estimate");
        await checks.scrollIntoViewIfNeeded();
        await page.mouse.move(0, 0);
        await expect(checks).toHaveScreenshot(`fno8b4-ticket-checks-v2-${size.width}-${theme}.png`, SHOT);
      });

      test(`the desk and the journal chart look the same (${theme})`, async ({ page }) => {
        await openPositions(page, theme);
        const desk = page.locator("[data-block='fno-desk']");
        if (size.phone) await desk.locator("[data-desk-tab='chain']").tap();
        await expect(desk.locator("[data-desk='chain-rows']")).toBeVisible();
        if (!size.phone) await expect(desk.locator("[data-snapshot='desk'] svg")).toBeVisible();
        await page.mouse.move(0, 0);
        await expect(desk).toHaveScreenshot(`fno8b4-desk-v2-${size.width}-${theme}.png`, SHOT);
        const journal = page.locator("[data-block='fno-journal']");
        await journal.locator("[data-draft-chart='show']").click();
        await expect(journal.locator("[data-draft-chart='shown'] figure svg")).toBeVisible();
        await journal.scrollIntoViewIfNeeded();
        await expect(journal).toHaveScreenshot(`fno8b4-journal-chart-v2-${size.width}-${theme}.png`, SHOT);
      });
    }
    if (size.phone) {
      test("no sideways scroll, 44 px targets: the desk's quick switch and each panel", async ({ page }) => {
        await openPositions(page, "dark");
        for (const tab of ["chart", "chain", "basket", "glance"]) {
          await page.locator(`[data-desk-tab='${tab}']`).tap();
          await expectPhoneLayout(page);
        }
      });
    }
  });
}

// --- flows ------------------------------------------------------------------------------------------------------------
test.describe("8b slice 4 flows", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("the risk parts are text only: the ticket still places on his own tap, PAPER", async ({ page }) => {
    const seen = await openChain(page, "light", FROM_SIGNAL, { items: [SINGLE_ITEM] });
    await page.locator("[data-setup-item='8'] [data-setup='trade']").tap();
    const ticket = page.locator("[data-order-ticket]");
    await ticket.locator("[data-ticket='review']").tap();
    const checks = ticket.locator("[data-risk-checks='ticket']");
    await expect(checks).toContainText("Liquidity: bid-ask gap");
    await expect(checks).toContainText("Information only: none of this holds back the order or any exit.");
    await expect(checks.locator("button, a, input")).toHaveCount(0);
    expect(orderCalls(seen).filter((p) => p.url.endsWith("/paper/fno/order"))).toEqual([]);
    await ticket.locator("[data-ticket='place']").tap();
    await expect(ticket.locator("[data-ticket='result']")).toContainText("Placed in practice mode");
    expect(seen.posts.filter((p) => p.url.endsWith("/paper/fno/order"))).toHaveLength(1);
  });

  test("FNO-8B-28: his own liquidity limits, only when on, add whether the strike is inside them; the ticket still places on his tap", async ({ page }) => {
    const seen = await openChain(page, "light", FROM_SIGNAL, { items: [SINGLE_ITEM], fno: { liquidity: { on: true, max_spread_pct: 0.5, min_oi: 500000 } } });
    await page.locator("[data-setup-item='8'] [data-setup='trade']").tap();
    const ticket = page.locator("[data-order-ticket]");
    await ticket.locator("[data-ticket='review']").tap();
    const checks = ticket.locator("[data-risk-checks='ticket']");
    await expect(checks).toContainText("Liquidity: bid-ask gap");
    await expect(checks).toContainText("Outside your own limits: bid-ask gap 0.7% is above your 0.5%; open interest 4,00,000 is below your 5,00,000.");
    await expect(checks).toContainText("Information only: none of this holds back the order or any exit.");
    await expect(checks.locator("button, a, input")).toHaveCount(0);
    await expect(ticket.locator("[data-ticket='place']")).toBeEnabled();
    await ticket.locator("[data-ticket='place']").tap();
    await expect(ticket.locator("[data-ticket='result']")).toContainText("Placed in practice mode");
    expect(seen.posts.filter((p) => p.url.endsWith("/paper/fno/order"))).toHaveLength(1);
  });

  test("FNO-8B-28: inside his own limits, said plainly; off (the default) the line is the chain comparison alone", async ({ page }) => {
    await openChain(page, "light", FROM_SIGNAL, { items: [SINGLE_ITEM], fno: { liquidity: { on: true, max_spread_pct: 1, min_oi: 100000 } } });
    await page.locator("[data-setup-item='8'] [data-setup='trade']").tap();
    const ticket = page.locator("[data-order-ticket]");
    await ticket.locator("[data-ticket='review']").tap();
    await expect(ticket.locator("[data-risk-checks='ticket']")).toContainText("Inside your own limits (bid-ask gap up to 1%, open interest at least 1,00,000).");
  });

  test("FNO-8B-28: off by default, the confirm step never mentions his own limits", async ({ page }) => {
    await openChain(page, "light", FROM_SIGNAL, { items: [SINGLE_ITEM] });
    await page.locator("[data-setup-item='8'] [data-setup='trade']").tap();
    const ticket = page.locator("[data-order-ticket]");
    await ticket.locator("[data-ticket='review']").tap();
    const checks = ticket.locator("[data-risk-checks='ticket']");
    await expect(checks).toContainText("Liquidity: bid-ask gap");
    await expect(checks).not.toContainText("your own limits");
  });

  test("the basket's numbers carry the events and each leg's liquidity", async ({ page }) => {
    const seen = await openChain(page, "light", `${FROM_SIGNAL}&trade=1`);
    const panel = page.locator("[data-basket-panel]");
    const checks = panel.locator("[data-risk-checks='basket']");
    await expect(checks).toContainText("RBI policy on 30 Oct 2026");
    await expect(checks).toContainText("Liquidity:");
    expect(orderCalls(seen)).toEqual([]);
  });

  test("the desk reads and never trades; the phone switch shows one panel; his journal chart is one read on his tap", async ({ page }) => {
    const seen = await openPositions(page, "light");
    const desk = page.locator("[data-block='fno-desk']");
    await expect(desk.locator("[data-desk-panel='chain']")).toBeHidden();
    await desk.locator("[data-desk-tab='chain']").tap();
    await expect(desk.locator("[data-desk-panel='chain']")).toBeVisible();
    await expect(desk.locator("[data-desk-panel='glance']")).toBeHidden();
    await expect(desk.locator("[data-desk-strike='24900'] td").first()).toHaveClass(/text-brand/);
    await desk.locator("[data-desk-tab='basket']").tap();
    await expect(desk.locator("[data-desk-panel='basket']")).toContainText("No legs on NIFTY in your basket");
    const reads: string[] = [];
    page.on("request", (r) => { if (/\/market-data\/candles$/.test(r.url())) reads.push(r.method()); });
    const journal = page.locator("[data-block='fno-journal']");
    await journal.locator("[data-draft-chart='show']").tap();
    await expect(journal.locator("[data-draft-chart='shown']")).toContainText("the lines mark when the trade opened and closed");
    expect(reads).toEqual(["POST"]);                                     // the one candle read (the chart page's own)
    const sent = seen.writes.filter((w) => !/\/auth\/refresh$|\/paper\/fno\/contracts$|\/market-data\/(quotes|candles)$/.test(w.url));
    expect(sent).toEqual([]);
    await expect(page.locator("[data-position='301']").getByTestId("exit-now")).toBeEnabled();
  });

  test("FNO-8B-31: Download image saves the drawn chart as a PNG with a plain name; nothing is sent", async ({ page }) => {
    const seen = await openPositions(page, "dark");
    const journal = page.locator("[data-block='fno-journal']");
    await journal.locator("[data-draft-chart='show']").tap();
    await expect(journal.locator("[data-draft-chart='shown'] figure svg")).toBeVisible();
    const sent: string[] = [];
    page.on("request", (r) => { if (r.method() !== "GET" && /\/api\//.test(r.url())) sent.push(r.url()); });
    const [file] = await Promise.all([page.waitForEvent("download"), journal.locator("[data-draft-chart='download']").tap()]);
    expect(file.suggestedFilename()).toBe("nifty-trade-chart-2026-10-16.png");
    const path = await file.path();
    const bytes = readFileSync(path);
    expect(bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");             // a PNG
    expect(bytes.readUInt32BE(16)).toBeGreaterThan(500);                                  // drawn at twice the chart's width
    await expect(journal.locator("[data-draft-chart='download-failed']")).toHaveCount(0);
    expect(sent.filter((u) => !/\/auth\/refresh$|\/paper\/fno\/contracts$|\/market-data\/(quotes|candles)$/.test(u))).toEqual([]);
    expect(seen.writes.filter((w) => /journal|upload|image/.test(w.url))).toEqual([]);
  });
});
