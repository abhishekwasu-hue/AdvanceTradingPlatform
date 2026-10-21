import { expect, test, type Page } from "@playwright/test";
import { mockCharts, type ChartsCalls, type ChartsMocks } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * UI2-4b2 (UI v2 proposal §6, Charts page plan §3): the v2 Charts page with the `ui_v2` flag on.
 * - Baselines `charts-v2-<width>-<theme>.png` at 1440 and 390, and the broker gate `charts-v2-gate-1440-light.png`.
 *   The old pages' baselines are untouched: with the flag off nothing changes (the last test, and mobile.e2e.ts).
 * - Every timeframe loads, full screen enters and leaves, no grid by default, the gate with no broker, the chart theme
 *   saved on the server and read back after a reload, and the chart built once (React wiring).
 * - UI2-4c: the watchlist (column `charts-v2-watchlist-1440-<theme>.png`, phone sheet `charts-v2-390-sheet-light.png`),
 *   versioned saves, the batch quotes, the drawing rail (a new drawing sends its timeframe; Delete all asks first) and
 *   the phone's drawing menu. The watchlist only with the `watchlists` flag on.
 * - UI2-4d: the Multi-chart tab (`charts-v2-multi-1440-<theme>.png`, `charts-v2-multi-390-<theme>.png`) - only with
 *   `chart_workspace` on, two charts for a new trader, grids, link groups, a saved layout's PUT read back, "Open in
 *   Chart tab", old `?workspace=1` links, the shared reads (one per symbol and timeframe) and the reads a hidden tab or
 *   an off-screen cell does not make.
 * - UI2-4e: an old `?chart=` window link opens the Chart tab under `ui_v2` (its timeframe and exchange; unchanged with
 *   the flag off), and the "Live" / "Price delayed" chip on the Chart tab and the Multi-chart cells (page.clock).
 * Every reply is made-up data (e2e/chartsMocks.ts): no real account, price or id.
 * After an intended change: `npx playwright test --project=phone e2e/charts-v2.e2e.ts --update-snapshots`, check the
 * new images, commit.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

interface Setup extends ChartsMocks {
  flag?: boolean; brokers?: unknown[]; path?: string; intro?: boolean;
  /** UI2-4c: the `watchlists` flag on as well. */
  watch?: boolean;
  /** UI2-4d: the `chart_workspace` flag (default on). */
  workspace?: boolean;
  /** More localStorage for the first load (e.g. this user's recent symbols). */
  storage?: Record<string, string>;
  /** More routes, registered after the mocks so they answer first. */
  before?: (page: Page) => Promise<void>;
  /** Wait for the network to settle after loading (off when a test holds a reply). */
  settle?: boolean;
}

async function open(page: Page, theme: "dark" | "light", setup: Setup = {}): Promise<ChartsCalls> {
  const { flag = true, brokers = CONNECTED, path = "/charts", intro = false, storage = {}, before, settle = true, watch = false, workspace = true, ...mocks } = setup;
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(([t, intro, storage]) => {
    try {
      // Only on the first load of a test: a reload keeps what the page itself stored.
      if (sessionStorage.getItem("e2e_seeded")) return;
      sessionStorage.setItem("e2e_seeded", "1");
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      if (!intro) localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts"]));
      for (const [k, v] of Object.entries(storage)) localStorage.setItem(k, v);
    } catch { /* storage unavailable */ }
  }, [theme, intro, storage] as const);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: flag ? { ui_v2: true, chart_workspace: workspace, ...(watch ? { watchlists: true } : {}) } : undefined, brokers });
  const calls = await mockCharts(page, mocks);
  await before?.(page);
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  if (settle) await page.waitForLoadState("networkidle");
  return calls;
}

/** The chart is drawn: the library's canvas is there and the bars came. */
async function chartReady(page: Page) {
  await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible();
  await expect(page.getByText("Loading prices…")).toHaveCount(0);
}

/** Distinct opaque colours in a band of the price pane's canvases, `rows` px from the top (no candles reach there). */
async function bandColours(page: Page): Promise<number> {
  return page.evaluate(() => {
    const cell = document.querySelector("[data-page='charts-v2'] .tv-lightweight-charts table tr td:nth-child(2)");
    const seen = new Set<string>();
    for (const c of cell ? [...cell.querySelectorAll("canvas")] : []) {
      const ctx = c.getContext("2d");
      if (!ctx || !c.width) continue;
      const { data } = ctx.getImageData(0, 4, c.width, 6);
      for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 200) seen.add(`${data[i]},${data[i + 1]},${data[i + 2]}`);
    }
    return seen.size;
  });
}

const THEMES = ["light", "dark"] as const;

/** Made-up lists (UI2-4c): index names with spaces and their exchange, and a second list. */
const LISTS = [
  { id: 1, name: "Indices", items: [{ symbol: "NIFTY 50", exchange: "NSE" }, { symbol: "NIFTY BANK", exchange: "NSE" }, { symbol: "FINNIFTY", exchange: "NSE" }, { symbol: "SENSEX", exchange: "BSE" }] },
  { id: 2, name: "Banks", items: [{ symbol: "HDFCBANK", exchange: "NSE" }, { symbol: "ICICIBANK", exchange: "NSE" }] },
];
const WATCH: Setup = { watch: true, watchlists: LISTS };
const symbols = (body: Record<string, unknown> | null) => (body?.items as { symbol: string }[]).map((i) => i.symbol);

/** The watchlist's rows, as shown. */
const rows = (page: Page) => page.getByRole("list", { name: /^Symbols in / }).locator("[data-row]");
const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };

test.describe("v2 Charts 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await chartReady(page);
      await page.mouse.move(0, 0);
      await expect(page).toHaveScreenshot(`charts-v2-1440-${theme}.png`, SHOT);
    });
  }

  test("the broker gate with no broker stored: the gate card, no chart, no candles asked for", async ({ page }) => {
    const calls = await open(page, "light", { brokers: [] });
    const gate = page.locator("[data-broker-gate='no-broker']");
    await expect(gate).toBeVisible();
    await expect(gate.getByRole("heading", { name: "Connect your broker" })).toBeVisible();
    await expect(page.locator("canvas")).toHaveCount(0);
    await expect(page.locator("[data-page='charts-v2']")).toHaveCount(0);
    expect(calls.candles).toEqual([]);
    await expect(page.getByText(/sample/i)).toHaveCount(0);
    await expect(page).toHaveScreenshot("charts-v2-gate-1440-light.png", SHOT);
  });

  test("every timeframe button loads its own history; day, week and month ask for the forming bar", async ({ page }) => {
    const calls = await open(page, "light");
    await chartReady(page);
    const group = page.getByRole("group", { name: "Timeframe" });
    await expect(group.getByRole("button")).toHaveText(["1m", "3m", "5m", "15m", "30m", "1H", "D", "W", "M"]);
    await expect(group.getByRole("button", { name: "5m", exact: true })).toHaveAttribute("aria-pressed", "true");
    const ids: Record<string, string> = { "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1H": "60min", D: "day", W: "week", M: "month" };
    for (const [label, tf] of Object.entries(ids)) {
      await group.getByRole("button", { name: label, exact: true }).click();
      await expect(group.getByRole("button", { name: label, exact: true })).toHaveAttribute("aria-pressed", "true");
      await expect.poll(() => calls.candles.some((c) => c.timeframe === tf && !c.before)).toBe(true);
      await chartReady(page);
      const first = calls.candles.find((c) => c.timeframe === tf && !c.before)!;
      expect(first.forming, tf).toBe(["day", "week", "month"].includes(tf));
      expect(first.volume_from_future).toBe(true);
    }
    const lookback = Object.fromEntries(calls.candles.filter((c) => !c.before).map((c) => [c.timeframe, c.lookback_days]));
    expect(lookback).toMatchObject({ "1min": 5, "5min": 10, "60min": 30, day: 730, week: 1826, month: 7300 });
  });

  test("week and month are offered only when the server supports them", async ({ page }) => {
    await open(page, "light", { timeframes: ["1min", "3min", "5min", "15min", "30min", "60min", "day"] });
    await chartReady(page);
    await expect(page.getByRole("group", { name: "Timeframe" }).getByRole("button")).toHaveText(["1m", "3m", "5m", "15m", "30m", "1H", "D"]);
  });

  test("older history: scrolling back asks once, and not again after the broker had none", async ({ page }) => {
    const calls = await open(page, "light");
    await chartReady(page);
    const plot = page.locator("[data-page='charts-v2'] .tv-lightweight-charts").first();
    const box = (await plot.boundingBox())!;
    for (let i = 0; i < 6; i += 1) {
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 3);
      await page.mouse.wheel(0, 400);                                          // zoom out: the oldest bars come near
    }
    const older = () => calls.candles.filter((c) => c.before).length;
    await expect.poll(older).toBe(1);
    const status = page.getByText("Loading older bars…");
    await expect(status).toHaveCount(0);                                       // answered: the broker has nothing older
    for (let i = 0; i < 4; i += 1) await page.mouse.wheel(0, 400);              // still at the oldest bar, still asking
    // Two frames: the chart has handled the scroll; a request let out would show "Loading older bars…" at once.
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null)))));
    await expect(status).toHaveCount(0);
    expect(older()).toBe(1);
  });

  test("full screen: the button and F enter, Escape leaves; F in the symbol field types an F", async ({ page }) => {
    await open(page, "light");
    await chartReady(page);
    const root = page.locator("[data-page='charts-v2']");
    await expect(root).toHaveAttribute("data-fullscreen", "off");
    await page.getByRole("button", { name: "Full screen", exact: true }).click();
    await expect(root).toHaveAttribute("data-fullscreen", "on");
    await expect(page.getByRole("toolbar", { name: "Chart controls" })).toBeVisible();          // the top bar comes along
    await page.keyboard.press("Escape");
    await expect(root).toHaveAttribute("data-fullscreen", "off");
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("f");
    await expect(root).toHaveAttribute("data-fullscreen", "on");
    await page.keyboard.press("f");
    await expect(root).toHaveAttribute("data-fullscreen", "off");
    await page.getByRole("combobox", { name: "Symbol" }).click();
    await page.keyboard.press("f");
    await expect(root).toHaveAttribute("data-fullscreen", "off");
    await expect(page.getByRole("combobox", { name: "Symbol" })).toHaveValue(/^f$/i);
  });

  test("no grid by default; the grid switch draws it in place", async ({ page }) => {
    await open(page, "dark");
    await chartReady(page);
    await page.mouse.move(0, 0);
    await expect(page.locator("[data-chart-grid]")).toHaveAttribute("data-chart-grid", "off");
    expect(await bandColours(page)).toBe(1);                                   // the solid background only
    await page.getByRole("button", { name: "Chart settings" }).click();
    await page.getByRole("switch", { name: "Grid lines" }).check();
    await expect(page.locator("[data-chart-grid]")).toHaveAttribute("data-chart-grid", "on");
    await expect.poll(() => bandColours(page)).toBeGreaterThan(1);
  });

  test("the chart theme is saved on the server and read back after a reload", async ({ page }) => {
    const calls = await open(page, "light");
    await chartReady(page);
    await expect(page.locator("[data-chart-theme]")).toHaveAttribute("data-chart-theme", "light");
    await page.getByRole("button", { name: "Chart settings" }).click();
    await page.getByRole("radio", { name: "Dark" }).click();
    await expect(page.locator("[data-chart-theme]")).toHaveAttribute("data-chart-theme", "dark");
    await expect.poll(() => calls.prefsPuts.length).toBe(1);
    expect(calls.prefsPuts[0]).toMatchObject({ version: "1", body: { theme: "dark", grid: false, indicators: { volume: true, rsi: true } } });
    // Not this browser's copy: the server's answer decides after the reload.
    await page.evaluate(() => { for (const k of Object.keys(localStorage)) if (k.startsWith("atp_chart_prefs:")) localStorage.removeItem(k); });
    await page.reload();
    await chartReady(page);
    await expect(page.locator("[data-chart-theme]")).toHaveAttribute("data-chart-theme", "dark");
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", "dark");          // the app stays light
  });

  test("React wiring: the chart is built once, a theme change and a pane change apply in place, leaving the page removes it", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.addInitScript(() => {
      const w = window as unknown as { __charts: number };
      w.__charts = 0;
      new MutationObserver((records) => {
        for (const r of records) for (const n of r.addedNodes) if (n instanceof HTMLElement && n.classList.contains("tv-lightweight-charts")) w.__charts += 1;
      }).observe(document, { childList: true, subtree: true });
    });
    await open(page, "light");
    await chartReady(page);
    const built = () => page.evaluate(() => (window as unknown as { __charts: number }).__charts);
    expect(await built()).toBe(1);
    await page.locator("[data-page='charts-v2'] .tv-lightweight-charts").evaluate((e) => { (e as HTMLElement).dataset.probe = "same"; });
    await page.getByRole("button", { name: "Chart settings" }).click();
    await page.getByRole("radio", { name: "Dark" }).click();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: /^Indicators/ }).click();
    await page.getByRole("switch", { name: /RSI/ }).click();
    await page.getByRole("switch", { name: /EMA 9/ }).click();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("list", { name: "Price lines" })).toContainText("EMA 9");
    await page.getByRole("group", { name: "Timeframe" }).getByRole("button", { name: "15m", exact: true }).click();
    await chartReady(page);
    expect(await built()).toBe(1);
    await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts")).toHaveAttribute("data-probe", "same");
    await page.getByRole("navigation").getByRole("link", { name: "Signals" }).first().click();
    await expect(page).toHaveURL(/\/signals$/);
    await expect(page.locator(".tv-lightweight-charts")).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test("the Indicators menu: three groups, search, a removed pane is gone and stays gone after a reload", async ({ page }) => {
    const calls = await open(page, "light");
    await chartReady(page);
    await page.getByRole("button", { name: /^Indicators/ }).click();
    const menu = page.getByRole("dialog", { name: "Indicators" });
    await expect(menu.locator("section")).toHaveCount(2);                      // no layer's flag is on
    await expect(menu.getByRole("region")).toHaveText([/On this chart/, /Popular/]);
    await expect(menu.getByRole("button", { name: "What is VWAP?" })).toBeVisible();
    await menu.getByRole("textbox", { name: "Search indicators" }).fill("average");
    await expect(menu.getByRole("switch")).toHaveText(["EMA 9", "EMA 21", "SMA 50", "VWAP"]);
    await menu.getByRole("textbox", { name: "Search indicators" }).fill("");
    await menu.getByRole("switch", { name: "Volume" }).click();
    await expect(menu.getByRole("switch", { name: "Volume" })).toHaveAttribute("aria-checked", "false");
    await expect.poll(() => calls.prefsPuts.at(-1)?.body).toMatchObject({ indicators: { volume: false, rsi: true } });
    await page.keyboard.press("Escape");                                       // closes, focus back on the button
    await expect(menu).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^Indicators/ })).toBeFocused();
    await expect(page.getByRole("button", { name: /^Indicators/ })).toHaveAttribute("aria-haspopup", "dialog");
    await page.getByRole("button", { name: "Chart settings" }).click();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Chart settings" })).toBeFocused();
    await page.reload();
    await chartReady(page);
    await page.getByRole("button", { name: /^Indicators/ }).click();
    await expect(page.getByRole("switch", { name: "Volume" })).toHaveAttribute("aria-checked", "false");
  });

  test("the market state chip and the symbol picker", async ({ page }) => {
    await open(page, "light", { market: "closed" });
    await chartReady(page);
    await expect(page.locator("[data-market]")).toHaveText("Closed · opens Tue 09:15");
    await page.getByRole("combobox", { name: "Symbol" }).fill("bank");
    const list = page.getByRole("listbox", { name: "Matching symbols" });
    await expect(list.getByRole("option")).toHaveCount(2);
    await expect(list.getByRole("option").first()).toContainText("NSE");                    // the exchange chip
    await list.getByRole("option", { name: /NIFTY BANK/ }).click();
    await expect(page).toHaveURL(/\/charts\/NIFTY%20BANK$/);
    await chartReady(page);
  });

  test("review: Enter right after typing opens the typed symbol, never a recent or earlier row", async ({ page }) => {
    // The search is held until after Enter, so Enter lands before any answer however slow the runner is.
    let release: () => void = () => undefined;
    const held = new Promise<void>((r) => { release = r; });
    await open(page, "light", {
      storage: { "atp_recent_symbols:2": JSON.stringify([{ symbol: "TCS", exchange: "NSE" }]) },
      before: (p) => p.route(/\/api(?:\/v1)?\/instrument-master\/search/, async (r) => {
        await held;
        await r.fulfill({ json: [{ tradingsymbol: "NIFTY BANK", exchange: "NSE", name: "Nifty Bank", instrument_type: "INDEX" }] });
      }),
    });
    await chartReady(page);
    const field = page.getByRole("combobox", { name: "Symbol" });
    await field.click();
    await expect(page.getByRole("listbox", { name: "Recent symbols" }).getByRole("option")).toHaveText([/TCS/]);
    await field.press("Enter");                                                // unchanged field: stays on NIFTY 50
    await expect(page).toHaveURL(/\/charts$/);
    await field.click();
    await field.fill("reliance");
    await expect(page.getByRole("listbox")).toHaveCount(0);                    // the recent rows are not shown as matches
    await field.press("Enter");                                                // before the search answers
    await expect(page).toHaveURL(/\/charts\/RELIANCE$/);
    release();
  });

  test("review: leaving the symbol field closes its list, so F works again", async ({ page }) => {
    await open(page, "light");
    await chartReady(page);
    await page.getByRole("combobox", { name: "Symbol" }).click();
    await expect(page.getByRole("listbox")).toHaveCount(0);                    // no recent symbols yet for this user
    await page.getByRole("combobox", { name: "Symbol" }).fill("bank");
    await expect(page.getByRole("listbox", { name: "Matching symbols" })).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("listbox")).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: "Symbol" })).toHaveValue("NIFTY 50");
    await page.keyboard.press("f");
    await expect(page.locator("[data-page='charts-v2']")).toHaveAttribute("data-fullscreen", "on");
  });

  test("review: a stored week waits for the server's timeframes instead of loading 5m first", async ({ page }) => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((r) => { release = r; });
    const calls = await open(page, "light", {
      storage: { "atp_charts_view:2": JSON.stringify({ timeframe: "week", type: "candles", overlays: [], layers: [] }) },
      settle: false,
      before: (p) => p.route(/\/api(?:\/v1)?\/market-data\/sources$/, async (r) => {
        await held;
        await r.fulfill({ json: { sources: [], usable: true, timeframes: ["1min", "3min", "5min", "15min", "30min", "60min", "day", "week", "month"] } });
      }),
    });
    await expect(page.getByText("Loading prices…")).toBeVisible();
    expect(calls.candles).toEqual([]);
    release();
    await chartReady(page);
    expect(calls.candles.filter((c) => !c.before).map((c) => c.timeframe)).toEqual(["week"]);
    await expect(page.getByRole("group", { name: "Timeframe" }).getByRole("button", { name: "W", exact: true })).toHaveAttribute("aria-pressed", "true");
  });

  test("the chart theme light on a dark app", async ({ page }) => {
    await open(page, "dark", { prefs: { theme: "light" } });
    await chartReady(page);
    await expect(page.locator("[data-chart-theme]")).toHaveAttribute("data-chart-theme", "light");
    await page.mouse.move(0, 0);
    await expect(page).toHaveScreenshot("charts-v2-chartlight-on-dark-1440.png", SHOT);
  });
});

test.describe("v2 Charts 1440: watchlist and drawing rail (UI2-4c)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`the watchlist column looks the same (${theme})`, async ({ page }) => {
      const calls = await open(page, theme, WATCH);
      await chartReady(page);
      await expect(rows(page)).toHaveCount(4);
      await expect(rows(page).first()).toContainText(/\d/);                  // the batch quote came
      expect(calls.quotes[0]).toEqual(LISTS[0].items);
      await page.mouse.move(0, 0);
      await expect(page).toHaveScreenshot(`charts-v2-watchlist-1440-${theme}.png`, SHOT);
    });
  }

  test("a watchlist row opens its symbol; the timeframe and a picked drawing tool stay (CHP-2)", async ({ page }) => {
    const calls = await open(page, "light", WATCH);
    await chartReady(page);
    await page.getByRole("group", { name: "Timeframe" }).getByRole("button", { name: "15m", exact: true }).click();
    await chartReady(page);
    const hline = page.getByRole("group", { name: "Drawing tools" }).getByRole("button", { name: "Horizontal line" });
    await hline.click();
    await expect(hline).toHaveAttribute("aria-pressed", "true");
    await rows(page).filter({ hasText: "NIFTY BANK" }).getByRole("button", { name: /^NIFTY BANK/ }).click();
    await expect(page).toHaveURL(/\/charts\/NIFTY%20BANK$/);
    await chartReady(page);
    await expect(page.getByRole("group", { name: "Timeframe" }).getByRole("button", { name: "15m", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(rows(page).filter({ hasText: "NIFTY BANK" }).getByRole("button", { name: /^NIFTY BANK/ })).toHaveAttribute("aria-current", "true");
    expect(calls.candles.at(-1)?.timeframe).toBe("15min");
    await expect(hline).toHaveAttribute("aria-pressed", "true");                   // still picked on the new symbol
    const plot = page.locator("[data-page='charts-v2'] .tv-lightweight-charts").first();
    const box = (await plot.boundingBox())!;
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 4);
    await expect.poll(() => calls.drawingPosts.map((d) => d.symbol)).toEqual(["NIFTY BANK"]);
    // A BSE row opens on its exchange.
    await rows(page).filter({ hasText: "SENSEX" }).getByRole("button", { name: /^SENSEX/ }).click();
    await expect(page).toHaveURL(/\/charts\/SENSEX\?exchange=BSE$/);
  });

  test("add, remove and reorder: each PUT carries the version it was made on; two lists", async ({ page }) => {
    const calls = await open(page, "light", WATCH);
    await chartReady(page);
    const add = page.getByRole("combobox", { name: "Add a symbol to Indices" });
    await add.fill("bank");
    await page.getByRole("listbox", { name: "Matching symbols" }).getByRole("option", { name: /BANKBEES/ }).click();
    await expect(rows(page)).toHaveCount(5);
    await expect(add).toHaveValue("");
    await rows(page).filter({ hasText: "FINNIFTY" }).getByRole("button", { name: "Remove FINNIFTY" }).click();
    await expect(rows(page)).toHaveCount(4);
    await rows(page).filter({ hasText: "SENSEX" }).getByRole("button", { name: "Move SENSEX", exact: true }).press("ArrowUp");
    await expect(rows(page).nth(1)).toContainText("SENSEX");
    await rows(page).filter({ hasText: "BANKBEES" }).getByRole("button", { name: /^BANKBEES/ }).press("Alt+ArrowUp");
    await expect.poll(() => calls.watchlistWrites.length).toBe(4);
    expect(calls.watchlistWrites.map((w) => [w.method, w.id, w.body?.version])).toEqual([["PUT", 1, 1], ["PUT", 1, 2], ["PUT", 1, 3], ["PUT", 1, 4]]);
    expect(symbols(calls.watchlistWrites[0].body)).toEqual(["NIFTY 50", "NIFTY BANK", "FINNIFTY", "SENSEX", "BANKBEES"]);
    expect(symbols(calls.watchlistWrites[1].body)).toEqual(["NIFTY 50", "NIFTY BANK", "SENSEX", "BANKBEES"]);
    expect(symbols(calls.watchlistWrites[2].body)).toEqual(["NIFTY 50", "SENSEX", "NIFTY BANK", "BANKBEES"]);
    expect(symbols(calls.watchlistWrites[3].body)).toEqual(["NIFTY 50", "SENSEX", "BANKBEES", "NIFTY BANK"]);
    // Review: the edits did not each ask for prices again (the route allows 30 a minute; one batch per 10 s).
    expect(calls.quotes).toHaveLength(1);
    // The other list.
    await page.getByRole("combobox", { name: "Watchlist" }).selectOption({ label: "Banks (2)" });
    await expect(rows(page)).toHaveText([/HDFCBANK/, /ICICIBANK/]);
  });

  test("a new list, renamed, then deleted after the question", async ({ page }) => {
    const calls = await open(page, "light", WATCH);
    await chartReady(page);
    await page.getByRole("button", { name: "New list" }).click();
    await page.getByRole("textbox", { name: "Name of the new list" }).fill("Swing ideas");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByRole("combobox", { name: "Watchlist" })).toHaveValue("100");
    await expect(page.getByText("This list is empty. Search a symbol above to add it.")).toBeVisible();
    await page.getByRole("button", { name: "Rename list" }).click();
    await page.getByRole("textbox", { name: "New name of the list" }).fill("Swing");
    await page.getByRole("button", { name: "Save" }).click();
    await page.getByRole("button", { name: "Delete list" }).click();
    const ask = page.getByRole("alertdialog");
    await expect(ask).toContainText('Delete the list "Swing"?');
    // Review: Escape typed in the add search leaves the question; Escape in it cancels and gives the focus back.
    await page.getByRole("combobox", { name: "Add a symbol to Swing" }).press("Escape");
    await expect(ask).toBeVisible();
    await ask.getByRole("button", { name: "Cancel" }).press("Escape");
    await expect(ask).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Delete list" })).toBeFocused();
    await page.getByRole("button", { name: "Delete list" }).click();
    await expect(ask).toContainText("this cannot be undone");
    await ask.getByRole("button", { name: "Delete list" }).click();
    await expect.poll(() => calls.watchlistWrites.map((w) => w.method)).toEqual(["POST", "PUT", "DELETE"]);
    expect(calls.watchlistWrites[1].body).toMatchObject({ name: "Swing", version: 1 });
    await expect(page.getByRole("combobox", { name: "Watchlist" })).toHaveValue("1");
  });

  test("the watchlist is hidden while the watchlists flag is off: no column, no button, no request", async ({ page }) => {
    const calls = await open(page, "light", { watchlists: LISTS });
    await chartReady(page);
    await expect(page.locator("[data-watchlist]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Watchlist" })).toHaveCount(0);
    expect(calls.watchlistReads).toBe(0);
    expect(calls.quotes).toEqual([]);
  });

  test("the column hides and comes back; it stays hidden after a reload", async ({ page }) => {
    await open(page, "light", WATCH);
    await chartReady(page);
    await page.getByRole("button", { name: "Hide watchlist" }).click();
    await expect(page.locator("[data-watchlist]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Watchlist" })).toHaveAttribute("aria-pressed", "false");
    await page.reload();
    await chartReady(page);
    await expect(page.locator("[data-watchlist]")).toHaveCount(0);
    await page.getByRole("button", { name: "Watchlist" }).click();
    await expect(rows(page)).toHaveCount(4);
  });

  test("the rail: pick the horizontal line, click the chart: the POST carries the timeframe; More holds the other kinds", async ({ page }) => {
    const calls = await open(page, "light");
    await chartReady(page);
    const rail = page.getByRole("group", { name: "Drawing tools" });
    expect(await rail.getByRole("button").evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")))).toEqual([
      "Trend line", "Horizontal line", "Ray", "Rectangle", "Text", "Measure", "More drawing tools", "Hide all drawings", "Delete all drawings"]);
    await page.getByRole("group", { name: "Timeframe" }).getByRole("button", { name: "15m", exact: true }).click();
    await chartReady(page);
    await rail.getByRole("button", { name: "Horizontal line" }).click();
    await expect(rail.getByRole("button", { name: "Horizontal line" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("Click the chart to place the horizontal line. Press Esc to cancel.")).toBeVisible();
    const plot = page.locator("[data-page='charts-v2'] .tv-lightweight-charts").first();
    const box = (await plot.boundingBox())!;
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 4);
    await expect.poll(() => calls.drawingPosts.length).toBe(1);
    expect(calls.drawingPosts[0]).toMatchObject({ symbol: "NIFTY 50", exchange: "NSE", timeframe: "15min", drawing: { kind: "hline" } });
    await expect(page.getByRole("button", { name: "Undo drawing" })).toBeEnabled();
    await rail.getByRole("button", { name: "More drawing tools" }).click();
    const more = page.getByRole("dialog", { name: "More drawing tools" });
    await expect(more.getByRole("button")).toHaveText(["Fib retracement", "Fib extension", "Parallel channel", "Vertical line", "Long position", "Short position"]);
    await more.getByRole("button", { name: "Vertical line" }).click();
    await expect(more).toHaveCount(0);
    await expect(rail.getByRole("button", { name: "More drawing tools" })).toHaveClass(/ring-brand/);
  });

  test("Delete all asks first, naming what goes; Cancel keeps them; then the existing endpoint with confirm=true", async ({ page }) => {
    const calls = await open(page, "light", { drawings: { "NIFTY 50": [24_100, 24_250, 24_400] }, locked: [24_400] });
    await chartReady(page);
    const rail = page.getByRole("group", { name: "Drawing tools" });
    await rail.getByRole("button", { name: "Delete all drawings" }).click();
    const ask = page.getByRole("alertdialog");
    await expect(ask).toContainText("Delete 2 drawings on NIFTY 50?");
    await expect(ask).toContainText("every timeframe of NIFTY 50, and Undo cannot bring them back. 1 locked drawing stays.");
    await expect(ask.getByRole("button", { name: "Cancel" })).toBeFocused();
    await ask.getByRole("button", { name: "Cancel" }).click();
    await expect(ask).toHaveCount(0);
    expect(calls.deleteAll).toEqual([]);
    await rail.getByRole("button", { name: "Delete all drawings" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete all" }).click();
    await expect.poll(() => calls.deleteAll).toEqual(["?symbol=NIFTY%2050&exchange=NSE&confirm=true"]);
    await expect(rail.getByRole("button", { name: "Delete all drawings" })).toBeDisabled();       // only the locked one is left
  });

  test("Hide all: the tools rest until shown again, and it is kept after a reload", async ({ page }) => {
    await open(page, "light", { drawings: { "NIFTY 50": [24_100] } });
    await chartReady(page);
    const rail = page.getByRole("group", { name: "Drawing tools" });
    await expect(rail.getByRole("button", { name: "Delete all drawings" })).toBeEnabled();
    await rail.getByRole("button", { name: "Hide all drawings" }).click();
    await expect(rail.getByRole("button", { name: "Hide all drawings" })).toHaveAttribute("aria-pressed", "true");
    await expect(rail.getByRole("button", { name: "Trend line" })).toBeDisabled();
    await page.reload();
    await chartReady(page);
    await expect(rail.getByRole("button", { name: "Hide all drawings" })).toHaveAttribute("aria-pressed", "true");
    await rail.getByRole("button", { name: "Hide all drawings" }).click();
    await expect(rail.getByRole("button", { name: "Trend line" })).toBeEnabled();
  });
});

// --- UI2-4d: the Multi-chart tab ---------------------------------------------------------------------------------------

/** A made-up `layout/1` with the given cells (symbol, timeframe, group). */
function layoutOf(cells: [string, string, string | null][], groups: Record<string, unknown> = {}) {
  return { schema: "layout/1", grid: cells.length, active: 0, groups,
           cells: cells.map(([symbol, timeframe, group]) => ({ symbol, exchange: "NSE", timeframe, chart_type: "candles", group })) };
}
const DRAFT = "atp_charts_multi_draft:2";                                       // the made-up user's id is 2
const cells = (page: Page) => page.locator("[data-multi-chart] section[data-cell]");
const cellNames = (page: Page) => cells(page).evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));

/** Every shown cell has drawn its chart. */
async function cellsReady(page: Page, n: number) {
  await expect(cells(page)).toHaveCount(n);
  await expect(page.locator("[data-multi-chart] .tv-lightweight-charts")).toHaveCount(n);
  await expect(page.getByText("Loading prices…")).toHaveCount(0);
}

test.describe("v2 Charts 1440: Multi-chart tab (UI2-4d)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`a new trader's two charts look the same (${theme})`, async ({ page }) => {
      await open(page, theme, { path: "/charts?tab=multi" });
      await cellsReady(page, 2);
      expect(await cellNames(page)).toEqual(["Chart 1: NIFTY 50 5m", "Chart 2: NIFTY 50 5m"]);
      await expect(page.getByRole("tab", { name: "Multi-chart" })).toHaveAttribute("aria-selected", "true");
      // One chart theme for every cell (CHP-11).
      await expect(page.locator("[data-multi-chart] [data-chart-theme]")).toHaveCount(2);
      for (const t of await page.locator("[data-multi-chart] [data-chart-theme]").evaluateAll((els) => els.map((e) => e.getAttribute("data-chart-theme")))) expect(t).toBe(theme);
      await page.mouse.move(0, 0);
      await expect(page).toHaveScreenshot(`charts-v2-multi-1440-${theme}.png`, SHOT);
    });
  }

  test("the tab shows only while chart_workspace is on", async ({ page }) => {
    await open(page, "light", { workspace: false, path: "/charts?tab=multi" });
    await chartReady(page);
    await expect(page.getByRole("tablist", { name: "Charts" })).toHaveCount(0);
    await expect(page.locator("[data-multi-chart]")).toHaveCount(0);
    await expect(page.getByText(/not enabled/)).toHaveCount(0);
  });

  test("the Chart tab reads nothing while the Multi-chart tab shows; grids 1, 2, 4, 6 and 8", async ({ page }) => {
    const calls = await open(page, "light");
    await chartReady(page);
    await page.getByRole("tab", { name: "Multi-chart" }).click();
    await expect(page).toHaveURL(/\?tab=multi$/);
    await cellsReady(page, 2);
    const grid = page.getByRole("group", { name: "Charts on screen" });
    await expect(grid.getByRole("button")).toHaveText(["1", "2", "4", "6", "8"]);
    await grid.getByRole("button", { name: "4 charts" }).click();
    await cellsReady(page, 4);
    await grid.getByRole("button", { name: "8 charts" }).click();
    await cellsReady(page, 8);
    // Eight charts of one symbol and timeframe: still one read of their history.
    expect(calls.candles.filter((c) => !c.before && c.timeframe === "5min" && c.lookback_days === 10)).toHaveLength(2);   // the Chart tab's, then the cells' one
    await grid.getByRole("button", { name: "1 chart" }).click();
    await cellsReady(page, 1);
  });

  test("link groups: a symbol change reaches its own group only", async ({ page }) => {
    await open(page, "light", { storage: { [DRAFT]: JSON.stringify(layoutOf([["NIFTY 50", "5min", null], ["NIFTY 50", "5min", null], ["NIFTY 50", "5min", null], ["NIFTY 50", "15min", null]])) },
                                path: "/charts?tab=multi" });
    await cellsReady(page, 4);
    await page.getByRole("button", { name: "Link group of chart 1: none" }).click();
    await page.getByRole("button", { name: "Link group of chart 2: none" }).click();
    await page.getByRole("button", { name: "Link group of chart 3: none" }).click();
    await page.getByRole("button", { name: "Link group of chart 3: A" }).click();
    await expect(page.getByRole("button", { name: "Link group of chart 3: B" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Link group A" }).getByRole("checkbox", { name: "Link symbol" })).toBeChecked();
    await expect(page.getByRole("group", { name: "Link group A" }).getByRole("checkbox", { name: "Link timeframe" })).not.toBeChecked();
    const box = page.getByRole("combobox", { name: "Symbol of chart 1" });
    await box.fill("NIFTY BANK");
    await box.press("Enter");
    await expect.poll(() => cellNames(page)).toEqual(["Chart 1: NIFTY BANK 5m", "Chart 2: NIFTY BANK 5m", "Chart 3: NIFTY 50 5m", "Chart 4: NIFTY 50 15m"]);
    // A linked timeframe: on for A, then chart 2 moves both of A's charts and no other.
    await page.getByRole("group", { name: "Link group A" }).getByRole("checkbox", { name: "Link timeframe" }).check();
    await page.getByLabel("Timeframe of chart 2").selectOption("week");
    await expect.poll(() => cellNames(page)).toEqual(["Chart 1: NIFTY BANK W", "Chart 2: NIFTY BANK W", "Chart 3: NIFTY 50 5m", "Chart 4: NIFTY 50 15m"]);
    await cellsReady(page, 4);
  });

  test("a saved layout: Save sends a PUT with its version, and it is read back after a reload", async ({ page }) => {
    const calls = await open(page, "light", { path: "/charts?tab=multi", storage: { atp_workspace_last: "7" },
      layouts: [{ id: 7, name: "Morning", layout: layoutOf([["NIFTY 50", "5min", "A"], ["NIFTY 50", "60min", "A"], ["NIFTY BANK", "day", null], ["NIFTY BANK", "week", null]], { A: { symbol: true, interval: false, crosshair: true } }) }] });
    await cellsReady(page, 4);
    await expect(page.getByLabel("Saved layouts")).toHaveValue("7");
    expect(await cellNames(page)).toEqual(["Chart 1: NIFTY 50 5m", "Chart 2: NIFTY 50 1H", "Chart 3: NIFTY BANK D", "Chart 4: NIFTY BANK W"]);
    await page.getByRole("group", { name: "Charts on screen" }).getByRole("button", { name: "2 charts" }).click();
    await page.getByLabel("Chart type of chart 2").selectOption("bars");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText('Saved "Morning".')).toBeVisible();
    expect(calls.layoutWrites).toHaveLength(1);
    expect(calls.layoutWrites[0]).toMatchObject({ method: "PUT", id: 7, body: { version: 1, layout: { grid: 2 } } });
    expect((calls.layoutWrites[0].body?.layout as { cells: { chart_type: string }[] }).cells.map((c) => c.chart_type)).toEqual(["candles", "bars"]);
    // Read back from the server: this browser's draft is cleared first.
    await page.evaluate((k) => localStorage.removeItem(k), DRAFT);
    await page.reload();
    await cellsReady(page, 2);
    await expect(page.getByLabel("Saved layouts")).toHaveValue("7");
    await expect(page.getByLabel("Chart type of chart 2")).toHaveValue("bars");
  });

  test("Save as names a new layout (POST); Delete asks first", async ({ page }) => {
    const calls = await open(page, "light", { path: "/charts?tab=multi" });
    await cellsReady(page, 2);
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await page.getByLabel("Name of the layout").fill("Two of NIFTY");
    await page.getByRole("button", { name: "Save layout" }).click();
    await expect(page.getByText('Saved "Two of NIFTY".')).toBeVisible();
    expect(calls.layoutWrites[0]).toMatchObject({ method: "POST", body: { name: "Two of NIFTY", layout: { schema: "layout/1", grid: 2 } } });
    await page.getByRole("button", { name: "Delete this saved layout" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete layout" }).click();
    await expect(page.getByText(/Deleted "Two of NIFTY"/)).toBeVisible();
    expect(calls.layoutWrites.at(-1)).toMatchObject({ method: "DELETE" });
    await expect(cells(page)).toHaveCount(2);
  });

  test("Open in Chart tab: the cell's symbol and timeframe on the Chart tab", async ({ page }) => {
    await open(page, "light", { path: "/charts?tab=multi", storage: { [DRAFT]: JSON.stringify(layoutOf([["NIFTY 50", "5min", null], ["NIFTY BANK", "15min", null]])) } });
    await cellsReady(page, 2);
    await page.getByRole("button", { name: "Open chart 2 in the Chart tab" }).click();
    await expect(page).toHaveURL(/\/charts\/NIFTY%20BANK$/);
    await expect(page.getByRole("tab", { name: "Chart", exact: true })).toHaveAttribute("aria-selected", "true");
    await chartReady(page);
    await expect(page.getByRole("group", { name: "Timeframe" }).getByRole("button", { name: "15m", exact: true })).toHaveAttribute("aria-pressed", "true");
  });

  test("week and month in a cell only while the server lists them", async ({ page }) => {
    await open(page, "light", { path: "/charts?tab=multi", timeframes: ["1min", "3min", "5min", "15min", "30min", "60min", "day"],
                                storage: { [DRAFT]: JSON.stringify(layoutOf([["NIFTY 50", "week", null], ["NIFTY 50", "day", null]])) } });
    await cellsReady(page, 2);
    expect(await cellNames(page)).toEqual(["Chart 1: NIFTY 50 5m", "Chart 2: NIFTY 50 D"]);
    await expect(page.getByLabel("Timeframe of chart 1").locator("option")).toHaveText(["1m", "3m", "5m", "15m", "30m", "1H", "D"]);
  });

  test("four charts on two symbols: one history read and one price poll per symbol", async ({ page }) => {
    const calls = await open(page, "light", { path: "/charts?tab=multi",
      storage: { [DRAFT]: JSON.stringify(layoutOf([["NIFTY 50", "5min", null], ["NIFTY 50", "5min", null], ["NIFTY BANK", "5min", null], ["NIFTY BANK", "5min", null]])) } });
    await cellsReady(page, 4);
    await expect.poll(() => [...new Set(calls.ltpSymbols)].sort()).toEqual(["NIFTY 50", "NIFTY BANK"]);
    const first = calls.candles.filter((c) => !c.before);
    expect(first.map((c) => c.symbol).sort()).toEqual(["NIFTY 50", "NIFTY BANK"]);
    expect(first.every((c) => c.timeframe === "5min" && c.lookback_days === 10)).toBe(true);
    expect(calls.ltpSymbols).toHaveLength(2);
  });

  test("tab switches keep both tabs' bars: a hidden tab reads nothing, then refreshes once", async ({ page }) => {
    const calls = await open(page, "light", { storage: { [DRAFT]: JSON.stringify(layoutOf([["NIFTY BANK", "15min", null], ["NIFTY BANK", "15min", null]])) } });
    await chartReady(page);
    // Newest-bar reads only (a chart at its oldest bar also asks once for older history: `before`).
    const chartTab = () => calls.candles.filter((c) => c.symbol === "NIFTY 50" && !c.before);
    const cellsRead = () => calls.candles.filter((c) => c.symbol === "NIFTY BANK" && !c.before);
    const bank = () => calls.ltpSymbols.filter((s) => s === "NIFTY BANK").length;
    expect(chartTab().map((c) => c.lookback_days)).toEqual([10]);
    await page.getByRole("tab", { name: "Multi-chart" }).click();
    await cellsReady(page, 2);
    expect(cellsRead().map((c) => c.lookback_days)).toEqual([20]);                         // one history read for two cells
    // Two minutes on the Multi-chart tab: the Chart tab reads nothing.
    await page.clock.runFor(125_000);
    expect(chartTab()).toHaveLength(1);
    // Back on the Chart tab: its bars are still there and one short refresh follows (no history read again).
    const polls = bank();
    await page.getByRole("tab", { name: "Chart", exact: true }).click();
    await chartReady(page);
    await expect(page.locator("[data-multi-chart]")).toBeHidden();                         // kept mounted, never on screen
    await expect.poll(() => chartTab().length).toBe(2);
    expect(chartTab()[1].lookback_days).toBe(2);
    // The hidden Multi-chart tab reads nothing either; switching back and forth reads no history again.
    const cellReads = cellsRead().length;
    await page.clock.runFor(125_000);
    expect(cellsRead()).toHaveLength(cellReads);
    expect(bank()).toBe(polls);
    const chartReads = chartTab().length;                                                   // its own refreshes while shown
    for (let i = 0; i < 3; i += 1) {
      await page.getByRole("tab", { name: "Multi-chart" }).click();
      await page.getByRole("tab", { name: "Chart", exact: true }).click();
    }
    await page.getByRole("tab", { name: "Multi-chart" }).click();
    await cellsReady(page, 2);
    await expect.poll(() => cellsRead().length).toBe(cellReads + 1);                       // one refresh for both cells
    expect(cellsRead().filter((c) => c.lookback_days === 20)).toHaveLength(1);
    expect(bank() - polls).toBeLessThanOrEqual(1);                                          // one price poll, not one per switch
    expect(chartTab()).toHaveLength(chartReads);                                            // brief visits: no extra reads
  });

  test("an old link with a saved layout used last: an unsaved layout, so Save names a new one (POST)", async ({ page }) => {
    const calls = await open(page, "light", { storage: { atp_workspace_last: "7" },
      layouts: [{ id: 7, name: "Morning", layout: layoutOf([["NIFTY 50", "5min", null], ["NIFTY 50", "60min", null], ["NIFTY 50", "day", null], ["NIFTY 50", "week", null]]) }] });
    await page.goto("/?workspace=1&chart=NIFTY%20BANK&tf=15min");
    await expect(page).toHaveURL(/\/charts\?tab=multi$/);
    await cellsReady(page, 2);
    await expect(page.getByLabel("Saved layouts")).toHaveValue("");
    await expect(page.getByLabel("Saved layouts").locator("option")).toHaveText(["Unsaved layout", "Morning"]);
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await page.getByLabel("Name of the layout").fill("Banks");
    await page.getByRole("button", { name: "Save layout" }).click();
    await expect(page.getByText('Saved "Banks".')).toBeVisible();
    expect(calls.layoutWrites.map((w) => w.method)).toEqual(["POST"]);
    // Escape in the name field closes it and puts the focus back on Save.
    await page.getByRole("button", { name: "Save as…" }).click();
    await page.getByLabel("Name of the layout").press("Escape");
    await expect(page.getByLabel("Name of the layout")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeFocused();
  });

  test("a layout changed in another window: Save says so, takes the newest version, and the next Save goes through", async ({ page }) => {
    const calls = await open(page, "light", { path: "/charts?tab=multi", storage: { atp_workspace_last: "7" },
      layouts: [{ id: 7, name: "Morning", layout: layoutOf([["NIFTY 50", "5min", null], ["NIFTY BANK", "5min", null]]) }] });
    await cellsReady(page, 2);
    // Another window saves first (version 1 -> 2).
    await page.evaluate(async (layout) => {
      await fetch("/api/v1/charts/layouts/7", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: 1, layout }) });
    }, layoutOf([["NIFTY 50", "5min", null]]));
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText(/changed in another window/)).toBeVisible();
    expect(await cellNames(page)).toEqual(["Chart 1: NIFTY 50 5m", "Chart 2: NIFTY BANK 5m"]);   // the screen stays
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText('Saved "Morning".')).toBeVisible();
    expect(calls.layoutWrites.map((w) => [w.method, w.body?.version])).toEqual([["PUT", 1], ["PUT", 1], ["PUT", 2]]);
  });
});

test.describe("old workspace links (UI2-4d)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("under ui_v2 ?workspace=1 opens the Multi-chart tab with its symbol and timeframe", async ({ page }) => {
    await open(page, "light");
    await page.goto("/?workspace=1&chart=NIFTY%20BANK&tf=15min");
    await expect(page).toHaveURL(/\/charts\?tab=multi$/);
    await cellsReady(page, 2);
    expect(await cellNames(page)).toEqual(["Chart 1: NIFTY BANK 15m", "Chart 2: NIFTY BANK 15m"]);
  });

  test("under ui_v2 with chart_workspace off: the link's symbol on the Chart tab, with its exchange", async ({ page }) => {
    const calls = await open(page, "light", { workspace: false });
    await chartReady(page);
    await page.goto("/?workspace=1&chart=SBIN&tf=15min&exchange=BSE");
    await expect(page).toHaveURL(/\/charts\/SBIN\?exchange=BSE$/);
    await chartReady(page);
    await expect(page.locator("[data-multi-chart]")).toHaveCount(0);
    expect(calls.candles.at(-1)?.symbol).toBe("SBIN");
  });

  test("with ui_v2 off ?workspace=1 is today's workspace, unchanged", async ({ page }) => {
    await open(page, "dark", { flag: false, path: "/?workspace=1&chart=NIFTY%2050",
      before: (p) => p.route(/\/api(?:\/v1)?\/system\/features$/, (r) => r.fulfill({ json: { chart_workspace: true } })) });
    await expect(page.getByText("Workspace", { exact: true })).toBeVisible();
    await expect(page).toHaveURL(/\/\?workspace=1&chart=NIFTY%2050$/);
    await expect(page.locator("[data-page='charts-v2']")).toHaveCount(0);
    await expect(page.getByRole("group", { name: "Charts on screen" }).getByRole("button")).toHaveText(["1", "2", "4", "6", "8"]);
    await expect(page.getByLabel("Chart type of chart 1").locator("option")).toHaveCount(7);   // no "bars" on the CH5 page
  });
});

test.describe("UI2-4e: old chart window links and the live chip", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  const chip = (page: Page) => page.locator("[data-page='charts-v2'] [data-live-chip]");

  test("under ui_v2 ?chart=NIFTY%2050&tf=15min opens the Chart tab on NIFTY 50 with 15m, and reads nothing first", async ({ page }) => {
    const calls = await open(page, "light", { path: "/?chart=NIFTY%2050&tf=15min" });
    await expect(page).toHaveURL(/\/charts\/NIFTY%2050$/);                               // the timeframe is taken, then leaves the address
    await chartReady(page);
    await expect(page.getByRole("group", { name: "Timeframe" }).getByRole("button", { name: "15m", exact: true })).toHaveAttribute("aria-pressed", "true");
    expect(calls.candles.filter((c) => !c.before).map((c) => `${c.symbol} ${c.timeframe}`)).toEqual(["NIFTY 50 15min"]);
    await expect(page.getByText(/refreshes every minute|candles via/)).toHaveCount(0);
  });

  test("an old link's exchange stays; its broker name goes", async ({ page }) => {
    const calls = await open(page, "light", { path: "/?chart=SBIN&tf=day&exchange=BSE&broker_name=upstox" });
    await expect(page).toHaveURL(/\/charts\/SBIN\?exchange=BSE$/);
    await chartReady(page);
    await expect(page.getByRole("group", { name: "Timeframe" }).getByRole("button", { name: "D", exact: true })).toHaveAttribute("aria-pressed", "true");
    expect(calls.candles.at(-1)?.symbol).toBe("SBIN");
  });

  test("Live while the price is fresh; Price delayed with its age once it is older than the staleness rule", async ({ page }) => {
    await open(page, "light");
    await chartReady(page);
    await expect(chip(page)).toHaveText("Live");
    await expect(chip(page)).toHaveAttribute("title", /^Updated (just now|\d+ s ago)$/);
    // The broker stops answering: after the server's two-minute rule the same price reads as delayed.
    await page.route(/\/api(?:\/v1)?\/market-data\/ltp/, (r) => r.fulfill({ status: 503, json: { detail: "unavailable" } }));
    await page.clock.runFor(130_000);
    await expect(chip(page)).toHaveText(/^Price delayed · 2 min$/);
    await expect(chip(page)).toHaveAttribute("data-live-chip", "delayed");
  });

  test("a quote the server marks stale reads Price delayed at once, with its age", async ({ page }) => {
    await open(page, "light", { before: (p) => p.route(/\/api(?:\/v1)?\/market-data\/ltp/, (r) => r.fulfill({ json: {
      symbol: "NIFTY 50", exchange: "NSE", source_broker: "upstox", fetched_at: "2026-10-19T05:30:00Z", ltp: 24_420.5, timestamp: "2026-10-19T05:25:00Z",
      age_seconds: 300, stale: true, stale_reason: "quote stale", source: "quote", bid: null, ask: null, volume: null } })) });
    await chartReady(page);
    await expect(chip(page)).toHaveText("Price delayed · 5 min");
  });

  test("the market closed: no chip, and no price poll", async ({ page }) => {
    const calls = await open(page, "light", { market: "closed" });
    await chartReady(page);
    await page.clock.runFor(10_000);
    await expect(chip(page)).toHaveCount(0);
    expect(calls.ltp).toBe(0);
  });

  test("a Multi-chart cell shows the chip too", async ({ page }) => {
    await open(page, "light", { path: "/charts?tab=multi" });
    await expect(page.locator("[data-cell] [data-live-chip]").first()).toHaveText("Live");
  });
});

test.describe("v2 Charts 390 phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await chartReady(page);
      await expect(page).toHaveScreenshot(`charts-v2-390-${theme}.png`, SHOT);
    });
  }

  test("no sideways scroll, 44 px targets, nothing hidden behind the bottom bar", async ({ page }) => {
    await open(page, "dark", { intro: true });
    await chartReady(page);
    await expectPhoneLayout(page);
  });

  test("UI2-4c: the watchlist sheet - opens from the top bar, a row opens its symbol and closes the sheet", async ({ page }) => {
    await open(page, "light", WATCH);
    await chartReady(page);
    await expect(page.locator("[data-drawing-rail]")).toBeHidden();             // the phone has the Draw menu instead
    await expectPhoneLayout(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.getByRole("button", { name: "Watchlist" }).click();
    const sheet = page.getByRole("dialog", { name: "Watchlist" });
    await expect(sheet).toBeVisible();
    await expect(rows(page)).toHaveCount(4);
    await expect(rows(page).first()).toContainText(/\d/);
    const small = await sheet.locator("button, select, input").evaluateAll((els) => els
      .filter((e) => e.getBoundingClientRect().height > 0 && e.getBoundingClientRect().height < 43.5 && !e.closest("[aria-label='Close']"))
      .map((e) => e.getAttribute("aria-label") ?? e.textContent));
    expect(small).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await expect(page).toHaveScreenshot("charts-v2-390-sheet-light.png", SHOT);
    await rows(page).filter({ hasText: "NIFTY BANK" }).getByRole("button", { name: /^NIFTY BANK/ }).click();
    await expect(sheet).toHaveCount(0);
    await expect(page).toHaveURL(/\/charts\/NIFTY%20BANK$/);
  });

  test("UI2-4c: the compact drawing menu - 44 px tools, picking one closes it and says how to place it", async ({ page }) => {
    const calls = await open(page, "light");
    await chartReady(page);
    await page.getByRole("button", { name: "Drawing tools" }).click();
    const menu = page.getByRole("dialog", { name: "Drawing tools" });
    await expect(menu.getByRole("button")).toHaveText(["Trend line", "Horizontal line", "Ray", "Rectangle", "Text", "Measure",
      "Fib retracement", "Fib extension", "Parallel channel", "Vertical line", "Long position", "Short position", "Hide all", "Delete all"]);
    for (const h of await menu.getByRole("button").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height))) expect(h).toBeGreaterThanOrEqual(44);
    const right = await menu.evaluate((e) => e.getBoundingClientRect().right);
    expect(right).toBeLessThanOrEqual(390);
    await menu.getByRole("button", { name: "Horizontal line" }).click();
    await expect(menu).toHaveCount(0);
    await expect(page.getByText("Tap the chart to place the horizontal line.")).toBeVisible();
    const plot = page.locator("[data-page='charts-v2'] .tv-lightweight-charts").first();
    const box = (await plot.boundingBox())!;
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 4);
    await expect.poll(() => calls.drawingPosts.length).toBe(1);
    expect(calls.drawingPosts[0]).toMatchObject({ timeframe: "5min" });
  });

  for (const theme of THEMES) test(`UI2-4d: the Multi-chart tab - one or two charts, stacked, no sideways scroll, 44 px targets (${theme})`, async ({ page }) => {
    await open(page, theme, { path: "/charts?tab=multi" });
    await cellsReady(page, 2);
    await expect(page.getByRole("group", { name: "Charts on screen" }).getByRole("button")).toHaveText(["1", "2"]);
    const [a, b] = await cells(page).evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).map((r) => ({ x: r.x, y: r.y, w: r.width })));
    expect(b.y).toBeGreaterThan(a.y);                                                           // stacked
    expect(b.x).toBe(a.x);
    await expectPhoneLayout(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.mouse.move(0, 0);
    await expect(page).toHaveScreenshot(`charts-v2-multi-390-${theme}.png`, SHOT);
  });

  test("UI2-4d: an eight-chart layout on a phone shows its first two and says so", async ({ page }) => {
    await open(page, "light", { path: "/charts?tab=multi",
      storage: { [DRAFT]: JSON.stringify(layoutOf(Array.from({ length: 8 }, (_, i) => [i % 2 ? "NIFTY BANK" : "NIFTY 50", "5min", null] as [string, string, null]))) } });
    await cellsReady(page, 2);
    await expect(page.getByText("This screen shows the first 2 of the layout's 8 charts.")).toBeVisible();
    await expect(page.getByRole("button", { name: "2 charts" })).toHaveAttribute("aria-pressed", "true");
  });
});

test.describe("v2 Charts 390x560 phone: off-screen cells (UI2-4d)", () => {
  test.use({ viewport: { width: 390, height: 560 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("the second chart reads its candles only once it is scrolled into view", async ({ page }) => {
    const calls = await open(page, "light", { path: "/charts?tab=multi",
      storage: { [DRAFT]: JSON.stringify(layoutOf([["NIFTY 50", "5min", null], ["NIFTY BANK", "5min", null]])) } });
    await expect(page.locator("[data-multi-chart] .tv-lightweight-charts")).toHaveCount(2);
    await expect.poll(() => calls.candles.filter((c) => c.symbol === "NIFTY 50" && !c.before).length).toBe(1);
    const below = await cells(page).nth(1).evaluate((e) => e.getBoundingClientRect().top - window.innerHeight);
    expect(below).toBeGreaterThan(64);                                                       // off screen, past the margin
    await page.clock.runFor(65_000);
    expect(calls.candles.filter((c) => c.symbol === "NIFTY BANK")).toHaveLength(0);
    expect(calls.ltpSymbols).not.toContain("NIFTY BANK");
    await cells(page).nth(1).scrollIntoViewIfNeeded();
    await expect.poll(() => calls.candles.filter((c) => c.symbol === "NIFTY BANK" && !c.before).length).toBe(1);
    await cellsReady(page, 2);
  });
});

test.describe("flag off", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("today's pages: no /charts page, and the ?chart= window as before", async ({ page }) => {
    const calls = await open(page, "dark", { flag: false, path: "/?chart=NIFTY%2050&tf=15min" });
    await expect(page.locator("[data-shell='v2']")).toHaveCount(0);
    await expect(page.locator("[data-page='charts-v2']")).toHaveCount(0);
    await expect(page.getByText("NIFTY 50").first()).toBeVisible();
    // UI2-4e: no redirect with the flag off - the window stays, with its own words and timeframe.
    await expect(page).toHaveURL(/\/\?chart=NIFTY%2050&tf=15min$/);
    await expect(page.getByText("refreshes every minute · live price every 5 s · scroll left for older history")).toBeVisible();
    await expect.poll(() => calls.candles.some((c) => c.timeframe === "15min")).toBe(true);
    await expect(page.locator("[data-live-chip]")).toHaveCount(0);
    await page.goto("/charts");
    await expect(page.locator("[data-page='charts-v2']")).toHaveCount(0);
  });
});
