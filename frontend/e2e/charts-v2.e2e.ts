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
 * Every reply is made-up data (e2e/chartsMocks.ts): no real account, price or id.
 * After an intended change: `npx playwright test --project=phone e2e/charts-v2.e2e.ts --update-snapshots`, check the
 * new images, commit.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

interface Setup extends ChartsMocks {
  flag?: boolean; brokers?: unknown[]; path?: string; intro?: boolean;
  /** More localStorage for the first load (e.g. this user's recent symbols). */
  storage?: Record<string, string>;
  /** More routes, registered after the mocks so they answer first. */
  before?: (page: Page) => Promise<void>;
  /** Wait for the network to settle after loading (off when a test holds a reply). */
  settle?: boolean;
}

async function open(page: Page, theme: "dark" | "light", setup: Setup = {}): Promise<ChartsCalls> {
  const { flag = true, brokers = CONNECTED, path = "/charts", intro = false, storage = {}, before, settle = true, ...mocks } = setup;
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
  await mockApi(page, { features: flag ? { ui_v2: true, chart_workspace: true } : undefined, brokers });
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
    await open(page, "light", { storage: { "atp_recent_symbols:2": JSON.stringify([{ symbol: "TCS", exchange: "NSE" }]) } });
    await chartReady(page);
    const field = page.getByRole("combobox", { name: "Symbol" });
    await field.click();
    await expect(page.getByRole("listbox", { name: "Recent symbols" }).getByRole("option")).toHaveText([/TCS/]);
    await field.fill("reliance");
    await expect(page.getByRole("listbox")).toHaveCount(0);                    // the recent rows are not shown as matches
    await field.press("Enter");                                                // before the search answers
    await expect(page).toHaveURL(/\/charts\/RELIANCE$/);
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

  test("the Multi-chart tab opens the workspace in a new tab", async ({ page }) => {
    await open(page, "light");
    await page.getByRole("tab", { name: "Multi-chart" }).click();
    await expect(page).toHaveURL(/\?tab=multi$/);
    await expect(page.getByRole("link", { name: /Open the multi-chart workspace/ })).toHaveAttribute("href", /workspace=1&chart=NIFTY\+50/);
    await expect(page.locator("canvas")).toHaveCount(0);
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
});

test.describe("flag off", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("today's pages: no /charts page, and the ?chart= window as before", async ({ page }) => {
    await open(page, "dark", { flag: false, path: "/?chart=NIFTY%2050&tf=5min" });
    await expect(page.locator("[data-shell='v2']")).toHaveCount(0);
    await expect(page.locator("[data-page='charts-v2']")).toHaveCount(0);
    await expect(page.getByText("NIFTY 50").first()).toBeVisible();
    await page.goto("/charts");
    await expect(page.locator("[data-page='charts-v2']")).toHaveCount(0);
  });
});
