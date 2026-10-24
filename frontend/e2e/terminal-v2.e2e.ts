import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

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

async function open(page: Page, theme: "light" | "dark", opts: { features?: Record<string, boolean>; bars?: number; vary?: boolean } = {}): Promise<Seen> {
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
  await page.route(/\/api(?:\/v1)?\/paper\/fno\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/contract")) return route.fulfill({ json: { ...CONTRACT, strike: Number(url.searchParams.get("strike")), right: url.searchParams.get("right") } });
    if (url.pathname.endsWith("/held")) return route.fulfill({ json: [] });
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
      await expect(panels).toHaveCount(6);
      for (const id of ["chart-1", "chain", "order", "positions", "basket", "orders"]) await expect(page.locator(`[data-panel='${id}']`)).toBeVisible();
      await expect(page.locator("[data-chain-ladder] [data-atm]")).toContainText("24,500");
      await expect(page.locator("[data-status='today']")).toContainText("+₹2,650");
      await expect(page.locator("[data-status='margin']")).toContainText("(estimate)");
      await expect(page.locator("[data-status='mode']")).toHaveText("Paper");
      await expect(page.locator("[data-status='feed']")).toHaveText("Live prices");
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
    await page.locator("[data-terminal='layout']").selectOption("quick_trade");
    await expect(page.locator("[data-page='terminal-v2']")).toHaveAttribute("data-layout", "quick_trade");
    await expect(page.locator("[data-terminal-grid] [data-panel]")).toHaveCount(4);
    await expect(page.locator("[data-panel='chain']")).toHaveCount(0);
    expect(seen.puts.at(-1)?.body).toMatchObject({ active: "quick_trade" });
    await shot(page, "terminal-1440-light-quick-trade");
    await page.locator("[data-terminal='layout']").selectOption("multi_chart");
    await expect(page.locator("[data-terminal-grid] [data-panel^='chart-']")).toHaveCount(4);
    await shot(page, "terminal-1440-light-multi-chart");
    // His own: back to Option seller, close the basket, save it under his name.
    await page.locator("[data-terminal='layout']").selectOption("option_seller");
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
    const keys = page.locator("[data-panel-keys='orders']");
    const before = await page.locator("[data-panel='orders']").boundingBox();
    await keys.focus();
    await page.keyboard.press("ArrowDown");
    const after = await page.locator("[data-panel='orders']").boundingBox();
    expect(after!.y).toBeGreaterThan(before!.y);
    await page.keyboard.press("ArrowLeft");                                    // onto the basket: refused
    const still = await page.locator("[data-panel='orders']").boundingBox();
    expect(still!.x).toBe(after!.x);
    await page.keyboard.press("Shift+ArrowDown");                              // taller
    const taller = await page.locator("[data-panel='orders']").boundingBox();
    expect(taller!.height).toBeGreaterThan(after!.height);
  });

  test("a price on the chain fills the order panel; the order goes through the one ticket (PAPER)", async ({ page }) => {
    const seen = await open(page, "dark");
    await page.locator("[data-cell='call-24500']").click();
    await page.locator("[data-chain-actions] [data-action='buy']").click();
    const order = page.locator("[data-panel='order']");
    await expect(order).toContainText("NIFTY 03 NOV 24500 CE");
    await expect(order.locator("[data-practice-frame]")).toBeVisible();
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

for (const size of [{ width: 360, height: 800 }, { width: 390, height: 844 }]) {
  test.describe(`v2 terminal ${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`tabs one at a time, the short status bar (${theme})`, async ({ page }) => {
        await open(page, theme);
        await expect(page.locator("[data-terminal-grid]")).toHaveCount(0);
        await expect(page.locator("[data-terminal-tabs] [role='tab']")).toHaveText([/^Chart$/, /^Chain$/, /^Order$/, /^Positions\s*2$/, /^Orders$/, /^Bots$/]);
        await expect(page.locator("[data-block='terminal-status'][data-compact]")).toBeVisible();
        await expect(page.locator("[data-status='margin']")).toHaveCount(0);
        await shot(page, `terminal-${size.width}-${theme}`);
        await page.locator("[data-tab='chain']").tap();
        await expect(page.locator("[data-phone-panel='chain'] [data-chain-ladder]")).toBeVisible();
        await shot(page, `terminal-${size.width}-${theme}-chain`);
        await page.locator("[data-tab='positions']").tap();
        await expect(page.locator("[data-phone-panel='positions'] [data-position]")).toHaveCount(2);
        await shot(page, `terminal-${size.width}-${theme}-positions`);
      });
    }

    test("no sideways scroll and 44 px targets on every tab; a chain price opens the Order tab", async ({ page }) => {
      await open(page, "light");
      for (const tab of ["chart", "chain", "order", "positions", "orders", "bots"]) {
        await page.locator(`[data-tab='${tab}']`).tap();
        await expect(page.locator(`[data-phone-panel='${tab}']`)).toBeVisible();
        await settle(page);
        await expectPhoneLayout(page);
      }
      await page.locator("[data-tab='chain']").tap();
      await page.locator("[data-cell='put-24500']").tap();
      await page.locator("[data-chain-actions] [data-action='sell']").tap();
      await expect(page.locator("[data-tab='order']")).toHaveAttribute("aria-selected", "true");
      await expect(page.locator("[data-phone-panel='order'] [data-ticket='review']")).toHaveText("Review Sell order");
      await expect(page.locator("[data-phone-panel='order']")).not.toContainText("Keys: B for Buy");
      await shot(page, `terminal-${size.width}-light-order`);
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
