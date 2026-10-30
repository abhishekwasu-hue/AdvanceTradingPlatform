import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { expectPhoneLayout } from "./phoneLayout";
import { pickOption } from "./combobox";

/**
 * Trading Terminal T1 (/terminal) with `ui_v2`, `terminal` and the practice account on, on made-up answers only (no real
 * account, price or id): the made-up chain of e2e/fixtures/option_chain_v2.json, made-up candles (chartsMocks), and the
 * status, positions and orders below.
 * - 1440: the Option seller layout (the default for a new user), switching layouts (remembered on the server), saving
 *   his own, moving a panel with the keyboard, no sideways scroll; the chain fills the order panel (the one ticket).
 * - 390 and 360: the panels as tabs, one at a time, the short status bar, no sideways scroll, 44 px targets.
 * - Flag off: no navigation entry and "Coming soon" at the address.
 * - TERMINAL_SHOTS=<dir>: also writes the screenshots for the owner's comparison page (light and dark); no baselines are
 *   kept in the repository for this page yet (the flag stays off until the owner's approval).
 * - TERMINAL_MEASURE=1: prints the T8 measurements this browser can honestly make (layout shift while panels update, click
 *   to the order request leaving the page, chart pan frame rate with 5,000 bars).
 */
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as { expiries: unknown; live: Record<string, unknown> };
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON = { ui_v2: true, terminal: true, paper_account: true, data_hub: true, smart_execution: true };
const THEMES = ["light", "dark"] as const;
const SHOTS = process.env.TERMINAL_SHOTS;
const MEASURE = process.env.TERMINAL_MEASURE === "1";

const STATUS = {
  today: { mtm: 2650, booked: 1900, note: "Booked today plus your open positions' profit or loss since entry, at their last price." },
  open: { count: 2, priced: 2, mtm: 750, as_of: "2026-10-27T05:00:00Z" },
  funds: { balance: 1_001_900, available: 993_300, source: "practice", note: "Your practice balance minus the margin estimate." },
  margin: { used: 8600, estimated: true, not_estimated: 0, note: "ATP's estimate, not your broker's number." },
  mode: { mode: "PAPER", live_allowed: false, message: "Practice mode: no real order is sent and no money moves." },
  feed: { state: "live", message: "Live prices from Upstox.", broker_name: "Upstox" },
  kill_switch: { engaged: false, scope: null, message: "New orders are allowed." },
  as_of: "2026-10-27T05:00:00Z",
};
const NOW = "2026-10-27T04:45:00Z";
const POSITION = (id: number, symbol: string, direction: string, price: number) => ({
  id, mode: "PAPER", symbol, strategy_id: "manual_fno", direction, entry_time: NOW, entry_price: price, quantity: 75, stop_loss: direction === "LONG" ? 0.05 : price * 2,
  target1: null, target2: null, exit_time: null, exit_price: null, exit_reason: null, pnl: null, charges: 0, charges_source: "ESTIMATED",
  instrument_kind: "OPTION", exchange: "NFO", lot_size: 75, expiry: "2026-11-03", option_position: direction === "LONG" ? "BUY" : "WRITE", mark_price: price,
});
const POSITIONS = [POSITION(41, "NIFTY 25000 CE 03 NOV 26", "SHORT", 120), POSITION(42, "NIFTY 25100 CE 03 NOV 26", "LONG", 80)];
const ORDER = (id: number, symbol: string, direction: string, status: string, reasons: string[] = []) => ({
  id, mode: "PAPER", strategy_id: "manual_fno", symbol, direction, quantity: 75, status, idempotency_key: null, broker_order_id: null, algo_tag: null,
  trade_id: status === "POSITION_OPEN" ? id : null, reasons, created_at: NOW, updated_at: NOW,
});
const ORDERS = [ORDER(41, "NIFTY 25000 CE 03 NOV 26", "SHORT", "POSITION_OPEN"), ORDER(42, "NIFTY 25100 CE 03 NOV 26", "LONG", "POSITION_OPEN"),
  ORDER(43, "NIFTY 24000 PE 03 NOV 26", "LONG", "REJECTED", ["Today's loss limit is reached: no new entries today. Exits still work."])];
const CONTRACT = { underlying: "NIFTY", tradingsymbol: "NIFTY26N0324500CE", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03",
  strike: 24500, right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null };

function preview(body: Record<string, unknown>) {
  const lots = Number(body.lots ?? 1);
  const fill = body.side === "SELL" ? 149.5 : 150.5;
  return {
    mode: "PAPER", practice: "Practice mode (PAPER): no real order is sent and no money moves.", contract: CONTRACT, label: CONTRACT.label,
    side: body.side, lots, lot_size: 75, units: lots * 75, order_type: body.order_type, product: body.product, price: 150, price_source: "live", fill_price: fill,
    fill_note: `Fills at the live price ${fill.toFixed(2)}`, stop_loss: body.stop_loss ?? null,
    margin: { value: fill * lots * 75, basis: "Long options only: the premium paid.", estimated: true },
    max_loss: { value: fill * lots * 75, basis: "The premium you pay, if the option expires worthless." }, charges: { total: 42.5, estimated: true },
    risk: null, errors: [], refusals: [], warnings: [], can_place: true, confirm_label: null, as_of: "2026-10-27T05:00:00+00:00",
  };
}

interface Seen { puts: { url: string; body: Record<string, unknown> }[]; orderPosts: { at: number; url: string }[] }

const INCOMPLETE = {
  group_id: "g1", underlying: "NIFTY", strategy: "Bear call spread", mode: "PAPER", state: "INCOMPLETE",
  message: "Incomplete: the hedge filled, the sold leg did not go through. Nothing more is sent until you choose.",
  failure: "The sold leg was not accepted.", deadline: null, seconds_left: null, on_no_answer: "keep", rule: null,
  filled: [{ trade_id: 42, symbol: "NIFTY 25100 CE 03 NOV 26", side: "BUY", role: "hedge", quantity: 75, entry_price: 80, open: true }],
  pending: [{ symbol: "NIFTY 25000 CE 03 NOV 26", side: "SELL", role: "sold", quantity: 75 }], actions: ["complete_rest", "close_filled", "keep"],
};
const EVALUATION = {
  ok: true, errors: [], prices: [], today: [], on_date: [], at_expiry: null, days_forward: 0, iv_shift: 0, single_expiry: true,
  extremes: { max_profit: 3000, max_loss: -4500, unbounded_profit: false, unbounded_loss: false }, profitable: null, breakevens: [25040],
  summary: { expiry: "2026-11-03", t_years: 0.02, sigma: 0.13, pop: 0.61, expected_move: 300, expected_pnl: 0, greeks: { delta: 0, gamma: 0, theta: 0, vega: 0 }, method: "", model: "" },
  legs: [], disclaimer: "", net_premium: 3000, reward_to_risk: null, costs: { total: 80, estimated: true },
  margin: { value: 32000, estimated: true, basis: "Estimate", note: "",
    hedge: { with_hedge: { value: 32000, estimated: true, basis: "With the hedge" }, without_hedge: { value: 145000, estimated: true, basis: "Sold leg alone" },
      benefit: 113000, hedged: true, source: "estimate", note: "" } },
  lot_sizes: [75, 75], labels: [], order: [1, 0],
};

async function open(page: Page, theme: "light" | "dark", opts: { features?: Record<string, boolean>; bars?: number; vary?: boolean; incomplete?: boolean } = {}): Promise<Seen> {
  await page.clock.install({ time: new Date("2026-10-27T10:30:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["terminal"]));
      sessionStorage.removeItem("atp_fno_basket");
    } catch { /* storage unavailable */ }
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
    // T8: every layout shift from the first paint on (input-driven shifts excluded, as CLS does).
    const w = window as unknown as { __cls: number };
    w.__cls = 0;
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries() as unknown as { value: number; hadRecentInput: boolean }[]) if (!e.hadRecentInput) w.__cls += e.value;
      }).observe({ type: "layout-shift", buffered: true });
    } catch { /* no layout-shift entries in this browser */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: opts.features ?? ON, brokers: CONNECTED });
  await mockCharts(page);
  const seen: Seen = { puts: [], orderPosts: [] };
  if (opts.bars) {
    const n = opts.bars;
    await page.route(/\/api(?:\/v1)?\/market-data\/candles$/, (r) => {
      const body = r.request().postDataJSON() as { symbols: string[]; timeframe: string; before: string | null };
      const start = Date.parse("2026-01-01T03:45:00Z");
      const bars = body.before ? [] : Array.from({ length: n }, (_, i) => {
        const base = 24000 + Math.sin(i / 40) * 300 + (i % 7) * 3;
        return { timestamp: new Date(start + i * 15 * 60_000).toISOString(), open: base, high: base + 12, low: base - 12, close: base + 4, volume: 1000 + i };
      });
      return r.fulfill({ json: { source: { broker: "upstox", account_label: "primary" }, exchange: "NSE", timeframe: body.timeframe, base_interval: "1min",
        lookback_days: 30, before: body.before, fetched_at: NOW, warnings: [], note: "",
        symbols: { [body.symbols[0]]: { bars, count: bars.length, first: bars[0]?.timestamp ?? null, last: bars.at(-1)?.timestamp ?? null, error: null, volume_source: null } } } });
    });
  }
  let stored: Record<string, unknown> = { layouts: {}, active: null };
  let version = 0;
  await page.route(/\/api(?:\/v1)?\/me\/prefs(\/[a-z_]+)?(\?.*)?$/, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (req.method() === "PUT" && url.pathname.endsWith("/terminal")) {
      stored = req.postDataJSON() as Record<string, unknown>;
      seen.puts.push({ url: url.pathname + url.search, body: stored });
      version += 1;
      return route.fulfill({ json: { namespace: "terminal", value: stored, version } });
    }
    if (req.method() === "PUT") return route.fulfill({ json: { namespace: "chart", value: req.postDataJSON(), version: 2 } });
    return route.fulfill({ json: { prefs: { chart: {}, terminal: stored }, versions: { chart: 1, terminal: version } } });
  });
  let reads = 0;
  // TERM-2 step 1: the context bar's India VIX and the previous close (the Today strip).
  await page.route(/\/api(?:\/v1)?\/market\/strip$/, (r) => r.fulfill({ json: { indices: [
    { key: "NIFTY 50", label: "NIFTY 50", ltp: 24480, prev_close: 24400 }, { key: "INDIA VIX", label: "INDIA VIX", ltp: 13.4, prev_close: 13.1 },
  ] } }));
  await page.route(/\/api(?:\/v1)?\/terminal\/status$/, (r) => {
    reads += 1;
    // `vary`: every read brings new figures (a bigger and a smaller number in turn), as a moving market does.
    const k = opts.vary ? (reads % 2 ? 137 * reads : -9_871 * reads) : 0;
    return r.fulfill({ json: { ...STATUS, today: { ...STATUS.today, mtm: STATUS.today.mtm + k }, open: { ...STATUS.open, mtm: STATUS.open.mtm + k } } });
  });
  await page.route(/\/api(?:\/v1)?\/positions\?mine=true$/, (r) => r.fulfill({ json: POSITIONS }));
  await page.route(/\/api(?:\/v1)?\/orders\?mine=true/, (r) => r.fulfill({ json: ORDERS }));
  await page.route(/\/api(?:\/v1)?\/orders\/\d+\/events$/, (r) => r.fulfill({ json: [
    { id: 1, from_status: null, to_status: "CREATED", detail: "", created_at: NOW },
    { id: 2, from_status: "CREATED", to_status: "POSITION_OPEN", detail: "", created_at: NOW },
  ] }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/expiries")) return route.fulfill({ json: DATA.expiries });
    return route.fulfill({ json: { ...DATA.live, market_open: true } });
  });
  // T1-6: live prices for P&L on every card (the My trades read), the two legs as one basket group.
  await page.route(/\/api(?:\/v1)?\/portfolio\/exposure/, (r) => r.fulfill({ json: {
    price_source: "ltp", currency: "INR", open_positions: 2, unrealised_pnl: 750, realised_today: 1900,
    priced_symbols: POSITIONS.map((p) => p.symbol),
    by_symbol: [{ symbol: POSITIONS[0].symbol, notional: 110 * 75 }, { symbol: POSITIONS[1].symbol, notional: 90 * 75 }],
  } }));
  await page.route(/\/api(?:\/v1)?\/trading\/incomplete$/, (r) => r.fulfill({ json: { items: opts.incomplete ? [INCOMPLETE] : [], as_of: NOW } }));
  await page.route(/\/api(?:\/v1)?\/paper\/fno\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/positions/groups")) return route.fulfill({ json: { groups: [{ key: "basket:b1", kind: "basket", name: "Bear call spread", trade_ids: [41, 42], hedge_ids: [42] }] } });
    // CX6: no combined exit set here (its own spec: e2e/combined-exit-v2.e2e.ts).
    if (url.pathname.endsWith("/group-exits")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/contracts")) return route.fulfill({ json: { items: [] } });
    if (url.pathname.endsWith("/basket/settings")) return route.fulfill({ json: { source: null, width_steps: 2, lots: 1, stop_pct: 100, note: "" } });
    if (url.pathname.endsWith("/basket/evaluate")) return route.fulfill({ json: EVALUATION });
    if (url.pathname.endsWith("/contract")) return route.fulfill({ json: { ...CONTRACT, strike: Number(url.searchParams.get("strike")), right: url.searchParams.get("right") } });
    if (url.pathname.endsWith("/held")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/resting")) return route.fulfill({ json: [] });         // T2: no waiting orders here
    if (url.pathname.endsWith("/risk-budget") || url.pathname.endsWith("/event-risk")) return route.fulfill({ status: 404, json: { detail: "Not found" } });
    const body = request.postDataJSON() as Record<string, unknown>;
    if (url.pathname.endsWith("/order/preview")) return route.fulfill({ json: preview(body) });
    seen.orderPosts.push({ at: Date.now(), url: url.pathname });
    return route.fulfill({ json: { ...preview(body), status: "PLACED", reasons: [], order_id: 9, trade_id: 44, filled: { price: 150.5, quantity: 75, lots: 1 } } });
  });
  await page.goto("/terminal");
  await settle(page);
  return seen;
}

async function settle(page: Page) {
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true, animations: "disabled" });
}

async function noSidewaysScroll(page: Page) {
  const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(r.scroll).toBeLessThanOrEqual(r.width);
}

test.describe("v2 terminal 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`the Option seller layout, the status bar, no sideways scroll (${theme})`, async ({ page }) => {
      await open(page, theme);
      await expect(page.locator("[data-page='terminal-v2']")).toHaveAttribute("data-layout", "option_seller");
      const panels = page.locator("[data-terminal-grid] [data-panel]");
      // TERM-2 step 3: chart | chain | basket above the one bottom panel (Positions · Orders · Alerts).
      await expect(panels).toHaveCount(4);
      for (const id of ["chart-1", "chain", "basket", "positions"]) await expect(page.locator(`[data-panel='${id}']`)).toBeVisible();
      const account = page.locator("[data-panel='positions'] [data-panel-body='account']");
      await expect(account.getByRole("tab")).toHaveText([/^Positions\s*2$/, "Orders", "Alerts"]);
      await expect(page.locator("[data-terminal-chain] tr[data-atm]")).toContainText("24,500");
      await expect(page.locator("[data-status='today']")).toContainText("+₹2,650");
      await expect(page.locator("[data-status='margin']")).toContainText("(estimate)");
      // TERM-2 step 1: one PAPER badge, in the context bar (the status bar no longer repeats it).
      await expect(page.locator("[data-terminal='mode']")).toHaveText("PAPER");
      await expect(page.locator("[data-status='mode']")).toHaveCount(0);
      await expect(page.locator("[data-status='feed']")).toHaveCount(0);           // CT-1 C9: the shell / chart say freshness once
      await noSidewaysScroll(page);
      await shot(page, `terminal-1440-${theme}`);
      await page.locator("[data-status='mask']").click();
      await expect(page.locator("[data-status='today']")).toContainText("₹••••");
      await expect(page.locator("[data-status='funds']")).not.toContainText("993");
      if (theme === "light") await shot(page, "terminal-1440-light-masked");
    });
  }

  test("switching layouts is remembered on the server; Save as mine; Reset", async ({ page }) => {
    const seen = await open(page, "light");
    await pickOption(page, page.locator("[data-terminal=\'layout\']"), "quick_trade");
    await expect(page.locator("[data-page='terminal-v2']")).toHaveAttribute("data-layout", "quick_trade");
    await expect(page.locator("[data-terminal-grid] [data-panel]")).toHaveCount(3);       // chain | order, the bottom panel
    await expect(page.locator("[data-panel='chart-1']")).toHaveCount(0);
    expect(seen.puts.at(-1)?.body).toMatchObject({ active: "quick_trade" });
    await shot(page, "terminal-1440-light-quick-trade");
    // TERM-2 step 2: no Multi-chart layout in the Terminal (the Charts page owns multi-chart, CT-1 C2).
    await page.locator("[data-terminal='layout']").click();
    await expect(page.getByRole("option", { name: "Multi-chart" })).toHaveCount(0);
    await page.keyboard.press("Escape");
    // His own: back to Option seller, close the basket, save it under his name.
    await pickOption(page, page.locator("[data-terminal=\'layout\']"), "option_seller");
    await page.locator("[data-panel-close='basket']").click();
    await expect(page.locator("[data-panel='basket']")).toHaveCount(0);
    await page.locator("[data-terminal='save']").click();
    await page.locator("[data-terminal='name']").fill("Expiry desk");
    await page.locator("[data-terminal='ask-ok']").click();
    await expect(page.locator("[data-note='terminal-layout'], [data-testid='terminal-layout']").first()).toContainText("Saved as");
    const saved = seen.puts.at(-1)?.body as { layouts: Record<string, { name: string; panels: { id: string; open: boolean }[] }>; active: string };
    const id = saved.active;
    expect(saved.layouts[id].name).toBe("Expiry desk");
    expect(saved.layouts[id].panels.find((p) => p.id === "basket")?.open).toBe(false);
    await expect(page.locator("[data-terminal='count']")).toHaveText("1 of 10 saved");
    // Reset brings the preset's panels back (unsaved until Save).
    await page.locator("[data-terminal='reset']").click();
    await expect(page.locator("[data-panel='basket']")).toBeVisible();
  });

  test("a panel moves with the keyboard and never onto another one", async ({ page }) => {
    await open(page, "light");
    const keys = page.locator("[data-panel-keys='positions']");
    const before = await page.locator("[data-panel='positions']").boundingBox();
    await keys.focus();
    await page.keyboard.press("ArrowDown");
    const after = await page.locator("[data-panel='positions']").boundingBox();
    expect(after!.y).toBeGreaterThan(before!.y);
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("ArrowUp");                                      // onto the chart / chain: refused
    const still = await page.locator("[data-panel='positions']").boundingBox();
    expect(still!.y).toBe(before!.y);
    await page.keyboard.press("Shift+ArrowDown");                              // taller
    const taller = await page.locator("[data-panel='positions']").boundingBox();
    expect(taller!.height).toBeGreaterThan(after!.height);
  });

  test("owner fixes T1-3 / T1-6: chain columns and one spot; positions with P&L, plain words, a group exit and Exit all", async ({ page }) => {
    await open(page, "light");
    const chain = page.locator("[data-panel='chain']");
    // TERM-2 step 2: the chain page's own chain and summary strip (one spot card; Greeks from the columns menu).
    for (const col of ["call-oi", "call-oi_change", "call-iv", "call-ltp", "put-oi", "put-iv"]) await expect(chain.locator(`[data-col='${col}']`).first()).toBeVisible();
    await expect(chain.locator("[data-summary='spot']")).toContainText(/[\d,.]+/);
    await expect(chain.locator("[data-summary]")).not.toHaveCount(0);
    await expect(chain.locator("[data-strike-range]")).toBeVisible();
    const positions = page.locator("[data-panel='positions']");
    await expect(positions.locator("[data-col='pnl']").first()).toBeVisible();
    await expect(positions.locator("[data-col='pnl']")).toHaveCount(2);
    await expect(positions).not.toContainText("Manual fno");
    await expect(positions).not.toContainText("0.05");
    await expect(positions.locator("[data-testid='exit-group']")).toBeVisible();
    await expect(positions.locator("[data-terminal='exit-all']")).toHaveText("Exit all (2)");
    await expect(positions.locator("[data-terminal='exit-all']")).toBeEnabled();
    await shot(page, "terminal-1440-light-fixes");
  });

  test("TERM-2 step 2 (owner fix T1-4): the one basket in its panel - net, max loss, breakevens, margin; a broken leg alert", async ({ page }) => {
    await open(page, "light", { incomplete: true });
    for (const [cell, leg] of [["call-24600", "basket-buy"], ["call-24500", "basket-sell"]] as const) {
      await page.locator(`[data-cell='${cell}']`).click();
      await page.locator("[data-chain-actions] [data-action='basket']").click();
      await page.locator(`[data-chain-actions] [data-action='${leg}']`).click();
      await page.keyboard.press("Escape");                                  // the sheet stays open after "Add"; close it
      await expect(page.locator("[data-chain-actions]")).toHaveCount(0);
    }
    // TERM-2 step 2: the one basket placed in the panel - the server's figures rendered once (no spread-builder copy).
    const builder = page.locator("[data-panel='basket'] [data-basket-panel][data-embedded]");
    await expect(builder.locator("[data-basket-body]")).toBeVisible();
    await expect(builder.locator("[data-basket='legs'] > li")).toHaveCount(2);
    for (const f of ["net", "max-loss", "breakevens", "margin"]) await expect(builder.locator(`[data-figure='${f}']`)).toBeVisible();
    await expect(page.locator("[data-panel='basket'] [data-practice-frame]")).toHaveCount(0);     // one PAPER badge per screen
    const alert = page.locator("[data-terminal='incomplete'] [data-incomplete-action]");
    await expect(alert.first()).toHaveText("Complete the leg");
    await expect(alert.nth(1)).toHaveText("Exit the filled leg");
    await shot(page, "terminal-1440-light-spread-builder");
  });

  test("a price on the chain fills the order panel; the order goes through the one ticket (PAPER)", async ({ page }) => {
    const seen = await open(page, "dark");
    // TERM-2 step 3: Quick trade is chain | order panel; a price fills the order panel there.
    await pickOption(page, page.locator("[data-terminal='layout']"), "quick_trade");
    await page.locator("[data-cell='call-24500']").click();
    await page.locator("[data-chain-actions] [data-action='buy']").click();
    const order = page.locator("[data-panel='order']");
    await expect(order).toContainText("NIFTY 03 NOV 24500 CE");
    await expect(order.locator("[data-practice-frame]")).toHaveCount(0);            // TERM-2 step 5: the one PAPER badge is in the bar
    await expect(page.locator("[data-terminal='mode']")).toHaveText("PAPER");
    await order.locator("[data-ticket='product'] [data-option='NRML']").click();   // F1: no product saved - pick one before Review
    await order.locator("[data-ticket='review']").click();
    await expect(order.locator("[data-ticket='confirm']")).toBeVisible();
    await shot(page, "terminal-1440-dark-order-review");
    // T8: the click on Place (captured in the page) to the order request leaving the page (the fetch call is timed).
    await page.evaluate(() => {
      const w = window as unknown as { __clickAt: number; __sentAt: number };
      document.addEventListener("click", (e) => { if ((e.target as Element).closest("[data-ticket='place']")) w.__clickAt = performance.now(); }, true);
      const original = window.fetch.bind(window);
      window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        if (/\/paper\/fno\/order(\?|$)/.test(url)) w.__sentAt = performance.now();
        return original(input, init);
      };
    });
    const sent = page.waitForRequest((r) => r.url().includes("/paper/fno/order") && !r.url().includes("preview"));
    await order.locator("[data-ticket='place']").click();
    await sent;
    await expect(order.locator("[data-ticket='result']")).toBeVisible();
    expect(seen.orderPosts).toHaveLength(1);
    const ms = await page.evaluate(() => { const w = window as unknown as { __clickAt: number; __sentAt: number }; return w.__sentAt - w.__clickAt; });
    if (MEASURE) console.log(`T8 click on Place to the order request leaving the page: ${ms.toFixed(1)} ms`);
  });
});

test.describe("v2 terminal context bar (TERM-2 step 1)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("one symbol + expiry: on an expiry day the next weekly; the figures; changing the expiry re-reads the chain", async ({ page }) => {
    const asked: string[] = [];
    page.on("request", (r) => { if (/option-chain\/[^/]+\/live/.test(r.url())) asked.push(new URL(r.url()).search); });
    await open(page, "light");
    const bar = page.locator("[data-block='terminal-context']");
    await expect(bar).toHaveAttribute("data-symbol", "NIFTY");
    await expect(bar).toHaveAttribute("data-expiry", "2026-11-03");            // 27 Oct is an expiry day: the next one opens
    await expect(bar.locator("[data-figure='pcr']")).toContainText("0.91");
    await expect(bar.locator("[data-figure='dte']")).toContainText("7");
    await expect(bar.locator("[data-figure='vix']")).toContainText("13.40");
    await expect(bar.locator("[data-figure='spot'] [data-signed]")).toBeVisible();
    await expect(bar.locator("[data-terminal='mode']")).toHaveText("PAPER");
    await page.locator("[data-terminal='expiry']").click();
    await page.getByRole("option", { name: /10 Nov 2026/ }).click();
    await expect(bar).toHaveAttribute("data-expiry", "2026-11-10");
    await expect.poll(() => asked.some((q) => q.includes("2026-11-10"))).toBe(true);
    await page.locator("[data-terminal='symbol']").click();
    await page.getByRole("option", { name: "BANKNIFTY", exact: true }).click();
    await expect(bar).toHaveAttribute("data-symbol", "BANKNIFTY");
    await expect(page.locator("[data-panel='chart-1']")).toContainText("NIFTY BANK");
  });
});

test.describe("v2 terminal tablet 820 (CT-1 T3)", () => {
  test.use({ viewport: { width: 820, height: 1180 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`the phone tabs and no layout controls (${theme})`, async ({ page }) => {
      await open(page, theme);
      await shot(page, `terminal-820-${theme}`);
      await expect(page.locator("[data-terminal-tabs]")).toBeVisible();
      await expect(page.locator("[data-block='terminal-toolbar']")).toHaveCount(0);
      await expect(page.locator("[data-block='terminal-context']")).toBeVisible();
      await noSidewaysScroll(page);
    });
  }
});

for (const size of [{ width: 360, height: 800 }, { width: 390, height: 844 }]) {
  test.describe(`v2 terminal ${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`tabs one at a time, the short status bar (${theme})`, async ({ page }) => {
        await open(page, theme);
        await expect(page.locator("[data-terminal-grid]")).toHaveCount(0);
        await expect(page.locator("[data-terminal-tabs] [role='tab']")).toHaveText([/^Chart$/, /^Chain$/, /^Basket$/, /^Positions\s*2$/, /^Orders$/]);
        await expect(page.locator("[data-block='terminal-status'][data-compact]")).toBeVisible();
        await expect(page.locator("[data-status='margin']")).toHaveCount(0);
        await shot(page, `terminal-${size.width}-${theme}`);
        await page.locator("[data-tab='chain']").tap();
        await expect(page.locator("[data-phone-panel='chain'] [data-terminal-chain] [data-chain-scroll]")).toBeVisible();
        await shot(page, `terminal-${size.width}-${theme}-chain`);
        await page.locator("[data-tab='positions']").tap();
        await expect(page.locator("[data-phone-panel='positions'] [data-position]")).toHaveCount(2);
        await shot(page, `terminal-${size.width}-${theme}-positions`);
      });
    }

    test("no sideways scroll and 44 px targets on every tab; a chain price opens the one ticket as a sheet", async ({ page }) => {
      await open(page, "light");
      for (const tab of ["chart", "chain", "basket", "positions", "orders"]) {     // TERM-2 step 3 tabs
        await page.locator(`[data-tab='${tab}']`).tap();
        await expect(page.locator(`[data-phone-panel='${tab}']`)).toBeVisible();
        await settle(page);
        await expectPhoneLayout(page);
      }
      await page.locator("[data-tab='chain']").tap();
      await page.locator("[data-cell='put-24500']").tap();
      await page.locator("[data-chain-actions] [data-action='sell']").tap();
      // TERM-2 step 3: the one order ticket opens as a bottom sheet (no Order tab of the Terminal's own).
      const ticket = page.locator("[data-order-ticket]");
      await expect(ticket.locator("[data-ticket='review']")).toHaveText("Review Sell order");
      await expect(ticket.getByText("Keys: B for Buy")).toBeHidden();             // a desktop keyboard hint only
      const review = await ticket.locator("[data-ticket='review']").boundingBox();
      expect(review && review.height >= 44).toBe(true);
      await shot(page, `terminal-${size.width}-light-order`);
      await page.keyboard.press("Escape");
      await expect(page.locator("[data-order-ticket]")).toHaveCount(0);
      // T1-3: LTP + OI per side on a phone; a strike opens the rest.
      await page.locator("[data-tab='chain']").tap();
      await expect(page.locator("[data-phone-panel='chain'] [data-col='call-iv']").first()).toBeHidden();
      await page.locator("[data-phone-panel='chain'] tr[data-atm] th").tap();
      await expect(page.locator("[data-row-details]")).toBeVisible();
      await noSidewaysScroll(page);
      await shot(page, `terminal-${size.width}-light-chain-expanded`);
    });

  });
}

test.describe("v2 terminal with the flag off", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("no navigation entry; the address says Coming soon", async ({ page }) => {
    await open(page, "light", { features: { ui_v2: true, paper_account: true } });
    await expect(page.getByText("Coming soon").first()).toBeVisible();
    await expect(page.locator("nav a[href='/terminal']")).toHaveCount(0);
    await expect(page.locator("[data-page='terminal-v2']")).toHaveCount(0);
    await shot(page, "terminal-1440-light-flag-off");
  });
});

test.describe("T8 measurements", () => {
  test.skip(!MEASURE, "TERMINAL_MEASURE=1 only");
  test.use({ viewport: { width: 1440, height: 900 } });

  test("layout shift while the panels update, chart pan with 5,000 bars", async ({ page }) => {
    await open(page, "light", { bars: 5000, vary: true });
    // Layout shift: from the first paint, through four status reads with new figures and the list reads coming back.
    await page.clock.runFor(61_000);
    await page.waitForTimeout(1500);
    const cls = await page.evaluate(() => (window as unknown as { __cls: number }).__cls);
    // Chart pan: drag across the first chart's canvas for about a second and count animation frames.
    const canvas = page.locator("[data-panel='chart-1'] canvas").first();
    const box = await canvas.boundingBox();
    const frames = await page.evaluate(() => new Promise<number>((resolve) => {
      let n = 0;
      const t0 = performance.now();
      const tick = () => { n += 1; if (performance.now() - t0 < 1000) requestAnimationFrame(tick); else resolve(n); };
      requestAnimationFrame(tick);
    }).then((n) => n));
    const idle = frames;
    const drag = (async () => {
      await page.mouse.move(box!.x + box!.width * 0.8, box!.y + box!.height / 2);
      await page.mouse.down();
      for (let i = 0; i < 60; i += 1) await page.mouse.move(box!.x + box!.width * (0.8 - i * 0.01), box!.y + box!.height / 2);
      await page.mouse.up();
    })();
    const during = await page.evaluate(() => new Promise<number>((resolve) => {
      let n = 0;
      const t0 = performance.now();
      const tick = () => { n += 1; if (performance.now() - t0 < 1000) requestAnimationFrame(tick); else resolve(n); };
      requestAnimationFrame(tick);
    }));
    await drag;
    console.log(`T8 layout shift (CLS from the first paint, 61 s of polled reads with changing figures): ${cls.toFixed(4)}`);
    console.log(`T8 frames per second, idle: ${idle}; while panning 5,000 bars: ${during}`);
    expect(cls).toBeLessThan(0.1);
  });
});

/** UX-11 PR 4 (owner, 27 Oct): numbers first - every Terminal panel passes the text budget (one short visible line,
 * the rest in Details); the visible-word count is printed for the comparison page. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`v2 terminal text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every panel fits the text budget", async ({ page }) => {
      await open(page, "light");
      await settle(page);
      console.log(`[words] Terminal ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
