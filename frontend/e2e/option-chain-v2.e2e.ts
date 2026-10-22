import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * OC1: the automatic Option chain page with the `ui_v2` flag on, on a hand-made chain (e2e/fixtures/option_chain_v2.json,
 * round made-up numbers). The clock is 27 Oct 2026, an expiry day, so the page opens on the next expiry (3 Nov).
 * - Baselines `option-chain-v2-<width>-<theme>.png` at 1440 and 390, light and dark (the "after" pictures; the flag-off
 *   page's `option-chain-<width>-<theme>.png` in mobile.e2e.ts are the "before" page without its developer controls).
 * - Automatic symbol and expiry (no click), the ATM row in the middle, the phone layout and a row's details, the
 *   plain-word states, the broker gate, and no sample data anywhere.
 * Browser-dependent state is pinned in the init script (the tab shown, scroll bars that take no room), because CI's
 * Chromium differs from a developer's.
 * After an intended change: `npx playwright test --project=phone e2e/option-chain-v2.e2e.ts --update-snapshots`, check the
 * new images, commit.
 */
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown> & { rows: unknown[] };
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const V2: MockOptions = { features: { ui_v2: true }, brokers: CONNECTED };
const THEMES = ["light", "dark"] as const;

interface Calls { expiries: string[]; live: string[] }

async function open(page: Page, theme: "light" | "dark", opts: {
  options?: MockOptions; marketOpen?: boolean; live?: (body: Record<string, unknown>) => Record<string, unknown>; path?: string;
  /** The chain read fails with this status (the server's own words in the body must never show). */
  failLive?: number;
} = {}): Promise<Calls> {
  await page.clock.install({ time: new Date("2026-10-27T10:30:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["option-chain"]));
    } catch { /* storage unavailable */ }
    // Pinned browser state: the tab is shown (the chain reads only then), whatever the headless build reports.
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, opts.options ?? V2);
  const calls: Calls = { expiries: [], live: [] };
  const sessions = { NSE: { is_open: opts.marketOpen ?? true, reason: "made up", next_open: null } };
  await page.route(/\/api(?:\/v1)?\/system\/status$/, (r) => r.fulfill({ json: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions } }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, async (route) => {
    const url = new URL(route.request().url());
    const m = /\/option-chain\/([^/]+)\/(expiries|live)/.exec(url.pathname)!;
    const symbol = decodeURIComponent(m[1]);
    if (m[2] === "expiries") {
      calls.expiries.push(symbol);
      return route.fulfill({ json: { ...DATA.expiries, underlying: symbol } });
    }
    calls.live.push(`${symbol}@${url.searchParams.get("expiry") ?? ""}`);
    if (opts.failLive) return route.fulfill({ status: opts.failLive, json: { detail: "RuntimeError: socket closed" } });
    const body = { ...DATA.live, underlying: symbol, expiry: url.searchParams.get("expiry") ?? DATA.live.expiry, market_open: opts.marketOpen ?? true };
    return route.fulfill({ json: opts.live ? opts.live(body) : body });
  });
  await page.goto(opts.path ?? "/option-chain");
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    // Pinned: scroll bars take no room (overlay or classic depends on the Chromium build).
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await page.waitForLoadState("networkidle");
  return calls;
}

async function chainReady(page: Page) {
  await expect(page.locator("[data-page='option-chain-v2']")).toBeVisible();
  await expect(page.locator("tr[data-atm]")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0);
}

/** The ATM row's middle is within one row of the middle of the table's visible rows. */
async function expectAtmCentred(page: Page) {
  const m = await page.evaluate(() => {
    const box = document.querySelector("[data-chain-scroll]")!;
    const head = box.querySelector("thead")!.getBoundingClientRect();
    const view = box.getBoundingClientRect();
    const row = box.querySelector("tr[data-atm]")!.getBoundingClientRect();
    return { rowMid: row.top + row.height / 2, viewMid: (head.bottom + view.bottom) / 2, rowHeight: row.height, scrolled: box.scrollTop };
  });
  expect(m.scrolled, "the table scrolled to the ATM row").toBeGreaterThan(0);
  expect(Math.abs(m.rowMid - m.viewMid)).toBeLessThanOrEqual(m.rowHeight);
}

test.describe("Option chain v2 at 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await chainReady(page);
      await expect(page).toHaveScreenshot(`option-chain-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("opens on NIFTY and the next expiry (27 Oct is an expiry day) with no click, the ATM row in the middle", async ({ page }) => {
    const calls = await open(page, "light");
    await chainReady(page);
    expect(calls.expiries).toEqual(["NIFTY"]);
    expect(calls.live[0]).toBe("NIFTY@2026-11-03");
    await expect(page.getByLabel("Symbol", { exact: true })).toHaveValue("NIFTY");
    await expect(page.getByLabel("Expiry", { exact: true })).toHaveValue("2026-11-03");
    await expectAtmCentred(page);
    // Desktop columns: OI, OI change, Volume, IV, LTP on each side, the strike in the centre; Greeks and bid / ask off.
    const heads = await page.locator("[data-chain-scroll] thead th").evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent !== null).map((e) => e.textContent));
    expect(heads).toEqual(["Call OI", "Call OI change", "Call Volume", "Call IV", "Call LTP", "Strike", "Put LTP", "Put IV", "Put Volume", "Put OI change", "Put OI"]);
    await expect(page.locator("[data-max-oi='call']")).toHaveCount(1);
    await expect(page.locator("[data-max-oi='put']")).toHaveCount(1);
    await expect(page.locator("tr[data-strike='25000'] [data-max-oi='call']")).toBeVisible();
    await expect(page.locator("tr[data-strike='24100'] [data-max-oi='put']")).toBeVisible();
    for (const [card, value] of [["spot", "24,480.00"], ["futures", "24,531.50"], ["atm", "24,500"], ["pcr", "0.91"], ["max-pain", "24,500"],
      ["atm-iv", "12.0%"], ["dte", "7 days"]]) {
      await expect(page.locator(`[data-summary='${card}']`)).toContainText(value);
    }
  });

  test("Columns adds the Greeks and bid / ask; a new symbol reads its own expiries and chain", async ({ page }) => {
    const calls = await open(page, "light");
    await chainReady(page);
    await page.getByRole("button", { name: "Columns" }).click();
    await page.getByLabel("Greeks (delta, gamma, theta, vega)").check();
    await page.getByLabel("Bid and ask").check();
    await expect(page.locator("[data-chain-scroll] thead")).toContainText("Delta");
    await expect(page.locator("[data-chain-scroll] thead")).toContainText("Bid");
    await page.keyboard.press("Escape");
    await page.getByLabel("Symbol", { exact: true }).selectOption("BANKNIFTY");
    await expect.poll(() => calls.live[calls.live.length - 1]).toBe("BANKNIFTY@2026-11-03");
    expect(calls.expiries).toEqual(["NIFTY", "BANKNIFTY"]);
    await chainReady(page);
    await page.getByLabel("Expiry", { exact: true }).selectOption("2026-11-10");
    await expect.poll(() => calls.live[calls.live.length - 1]).toBe("BANKNIFTY@2026-11-10");
  });

  test("the chain refreshes on its own while the page is shown", async ({ page }) => {
    const calls = await open(page, "light");
    await chainReady(page);
    const before = calls.live.length;
    await page.clock.runFor(16_000);
    await expect.poll(() => calls.live.length).toBeGreaterThan(before);
  });
});

test.describe("Option chain v2 at 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await chainReady(page);
      await expect(page).toHaveScreenshot(`option-chain-v2-390-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("phone: OI and LTP on each side, the strike in the centre, the ATM row in the middle, a row's details on tap", async ({ page }) => {
    await open(page, "dark");
    await chainReady(page);
    await expectPhoneLayout(page);
    const heads = await page.locator("[data-chain-scroll] thead th").evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent !== null).map((e) => e.textContent));
    expect(heads).toEqual(["Call OI", "Call LTP", "Strike", "Put LTP", "Put OI"]);
    // The strike column sits in the middle of the screen.
    const strike = await page.locator("[data-chain-scroll] thead th", { hasText: "Strike" }).boundingBox();
    expect(Math.abs(strike!.x + strike!.width / 2 - 195)).toBeLessThanOrEqual(24);
    await expectAtmCentred(page);
    // The summary is a strip that swipes sideways.
    const strip = await page.locator("[data-block='chain-summary']").evaluate((e) => ({ sw: e.scrollWidth, cw: e.clientWidth, x: getComputedStyle(e).overflowX }));
    expect(strip.x).toBe("auto");
    expect(strip.sw).toBeGreaterThan(strip.cw);
    await page.locator("tr[data-strike='24500']").tap();
    const sheet = page.locator("[data-row-details]");
    await expect(sheet).toBeVisible();
    await expect(sheet.getByRole("heading")).toContainText("NIFTY 24,500");
    for (const label of ["LTP", "OI change", "IV", "Bid", "Delta", "Vega"]) await expect(sheet.getByRole("rowheader", { name: label, exact: true })).toBeVisible();
    await sheet.getByRole("button", { name: "Close" }).tap();
    await expect(sheet).toHaveCount(0);
  });

  test("phone 360: the layout rules hold with every column on", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await page.addInitScript(() => { try { localStorage.setItem("atp_option_chain_columns", JSON.stringify({ greeks: true, quotes: true, more: true })); } catch { /* none */ } });
    await open(page, "light");
    await chainReady(page);
    await expectPhoneLayout(page);
    // Wider than the screen: the table scrolls sideways inside its box and the strike stays in the centre.
    const box = page.locator("[data-chain-scroll]");
    expect(await box.evaluate((e) => e.scrollWidth > e.clientWidth)).toBe(true);
    await box.evaluate((e) => { e.scrollLeft = e.scrollWidth; });
    const strike = await page.locator("[data-chain-scroll] thead th", { hasText: "Strike" }).boundingBox();
    expect(Math.abs(strike!.x + strike!.width / 2 - 180)).toBeLessThanOrEqual(24);
  });
});

test.describe("Option chain v2 states", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("OI history still building", async ({ page }) => {
    await open(page, "light", { live: (b) => ({ ...b, oi_change: { status: "building", source: "first_reading", since: "2026-10-27T04:58:00+00:00" } }) });
    await chainReady(page);
    await expect(page.getByText("OI change will show after the first few minutes")).toBeVisible();
  });

  test("market closed: the last session's chain", async ({ page }) => {
    await open(page, "light", { marketOpen: false });
    await chainReady(page);
    await expect(page.getByText("Market closed — showing the last session's chain")).toBeVisible();
  });

  test("no broker: only the 'Connect your broker' card, never a chain or sample data", async ({ page }) => {
    const calls = await open(page, "light", { options: { features: { ui_v2: true }, brokers: [] } });
    await expect(page.locator("[data-broker-gate='no-broker']")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Connect your broker" })).toBeVisible();
    await expect(page.locator("[data-page='option-chain-v2']")).toHaveCount(0);
    expect(calls.live).toEqual([]);
    await expect(page.getByText(/sample/i)).toHaveCount(0);
  });

  test("a chain that cannot be read says so in plain words; no developer text anywhere", async ({ page }) => {
    await open(page, "light", { failLive: 502 });
    await expect(page.getByText("We could not load the option chain. We will try again in a moment.")).toBeVisible();
    const words = await page.locator("main").innerText();
    expect(words).not.toMatch(/sample|lookback|cached|Underlying LTP|tilt|RuntimeError|Insufficient data|Stale/i);
  });
});

// ---------------------------------------------------------------- OC5: the Charts view
// The made-up answer of GET /api/option-chain/charts (e2e/fixtures/option_chain_charts.json: the backend's own output for
// the chain above plus 8 made-up stored readings; e2e/fixtures/make_option_chain_charts.py writes it). New baselines
// `option-chain-charts-v2-<width>-<theme>.png`; the chain's own baselines above are unchanged.
const CHARTS = JSON.parse(readFileSync(new URL("./fixtures/option_chain_charts.json", import.meta.url), "utf8")) as Record<string, unknown> & {
  charts: { key: string; title: string; caption: string; x_kind: string }[];
};

async function openCharts(page: Page, theme: "light" | "dark", body: (b: typeof CHARTS) => Record<string, unknown> = (b) => b) {
  // Each chain answer carries its own read time, as the server's does (the charts read again when it changes).
  let reads = 0;
  const calls = await open(page, theme, { live: (b) => ({ ...b, fetched_at: new Date(Date.parse(String(b.fetched_at)) + 1000 * reads++).toISOString() }) });
  const asked: URLSearchParams[] = [];
  await page.route(/\/api(?:\/v1)?\/option-chain\/charts\?/, (route) => {
    asked.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: body(CHARTS) });
  });
  await chainReady(page);
  await page.getByRole("button", { name: "Charts", exact: true }).click();
  await expect(page.locator("[data-oc-charts]")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0);
  return { calls, asked };
}

test.describe("Option chain v2 Charts at 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`Charts looks the same (${theme})`, async ({ page }) => {
      await openCharts(page, theme);
      await expect(page.locator("[data-chart-card]")).toHaveCount(10);
      await expect(page).toHaveScreenshot(`option-chain-charts-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("Charts reads the shared expiry, ATM ± 10 and 5-minute readings; the range and the view are the trader's; it refreshes with the chain", async ({ page }) => {
    const { asked, calls } = await openCharts(page, "light");
    await expect.poll(() => asked.length).toBeGreaterThan(0);
    expect(Object.fromEntries(asked[0])).toEqual({ symbol: "NIFTY", strikes: "10", interval: "5", expiry: "2026-11-03" });
    await expect(page.locator("[data-block='chain']")).toHaveCount(0);
    for (const c of CHARTS.charts) {
      const card = page.locator(`[data-chart-card='${c.key}']`);
      await expect(card.getByRole("heading", { name: c.title })).toBeVisible();
      await expect(card).toContainText(c.caption);
    }
    await expect(page.locator("[data-charts-facts]")).toContainText("7 days to expiry.");
    await page.getByLabel("Strikes around ATM").selectOption("15");
    await expect.poll(() => asked[asked.length - 1].get("strikes")).toBe("15");
    await page.getByRole("button", { name: "10 min" }).click();
    await expect.poll(() => asked[asked.length - 1].get("interval")).toBe("10");
    const before = asked.length;
    const chainReads = calls.live.length;
    await page.clock.runFor(16_000);
    await expect.poll(() => calls.live.length).toBeGreaterThan(chainReads);
    await expect.poll(() => asked.length).toBeGreaterThan(before);
    // Back to the chain: the table again, the ATM row in the middle.
    await page.getByRole("button", { name: "Chain", exact: true }).click();
    await chainReady(page);
    const words = await page.locator("main").innerText();
    expect(words).not.toMatch(/sample|lookback|RuntimeError|\bbuy\b|\bsell\b/i);
  });

  test("early in the session a chart says it builds through the session, never a number", async ({ page }) => {
    await openCharts(page, "light", (b) => ({ ...b, session: { readings: 0, since: null, note: null },
      charts: b.charts.map((c) => c.x_kind === "time" ? { ...c, state: "building", series: [],
        message: "Builds through the session: a reading of the chain is stored every 5 minutes while the market is open, so this chart fills in as the day goes on." } : c) }));
    await expect(page.locator("[data-chart-building]").first()).toContainText("Builds through the session");
    await expect(page.locator("[data-chart-card='oi_by_strike'] svg")).toBeVisible();
  });
});

test.describe("Option chain v2 Charts at 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`Charts looks the same (${theme})`, async ({ page }) => {
      await openCharts(page, theme);
      // The first chart in view (the controls and the facts sit above it).
      await page.locator("[data-charts-pager]").evaluate((e) => window.scrollTo(0, e.getBoundingClientRect().top + window.scrollY - 120));
      await expect(page).toHaveScreenshot(`option-chain-charts-v2-390-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("phone: one chart at a time, swiped sideways or moved with the arrows; the layout rules hold", async ({ page }) => {
    await openCharts(page, "dark");
    const deck = page.locator("[data-charts-deck]");
    const pager = page.locator("[data-charts-pager]");
    await expect(pager).toContainText("OI by strike · 1 of 10");
    // Exactly one card is in view.
    const inView = () => page.locator("[data-chart-card]").evaluateAll((cards) => cards.filter((c) => {
      const r = c.getBoundingClientRect();
      return r.left >= -1 && r.right <= window.innerWidth + 1;
    }).length);
    expect(await inView()).toBe(1);
    await page.getByRole("button", { name: "Next chart" }).tap();
    await expect(pager).toContainText("Change in OI by strike · 2 of 10");
    // A swipe: the deck scrolls sideways and snaps to the next card.
    await deck.evaluate((e) => { const cards = e.children as HTMLCollectionOf<HTMLElement>; e.scrollTo({ left: cards[2].offsetLeft - cards[0].offsetLeft }); });
    await expect(pager).toContainText("3 of 10");
    // Every card is exactly the deck's width, so a snapped deck shows one chart at a time.
    const widths = await deck.evaluate((e) => [...e.children].map((c) => Math.round(c.getBoundingClientRect().width) - e.clientWidth));
    expect(widths.every((d) => d === 0)).toBe(true);
    await expectPhoneLayout(page);
  });

  test("phone: the chain view's toggle is a 44 px target in the gap above the chain", async ({ page }) => {
    await open(page, "light");
    await chainReady(page);
    const toggle = await page.locator("[data-view-toggle]").boundingBox();
    const table = await page.locator("[data-chain-scroll]").boundingBox();
    expect(toggle!.height).toBeGreaterThanOrEqual(44);
    expect(toggle!.y + toggle!.height).toBeLessThanOrEqual(table!.y + 1);
    await expectPhoneLayout(page);
  });
});
