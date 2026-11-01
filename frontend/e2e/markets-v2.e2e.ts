import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { DEMO_ANALYSES } from "../src/optionChain/analyses.fixture";
import { DEMO_LIST, DEMO_OVERVIEW } from "../src/markets/markets.fixture";
import { mockApi } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * MO4 slice 2: the Sector & index view (/markets) and the Option chain's "What the chain shows", with `ui_v2` and
 * `markets_view` on, on made-up answers (src/markets/markets.fixture.ts; the chain summary is the backend's own output for
 * the hand-made chain of backend/tests/test_mo4_markets.py).
 * - Baselines `markets-v2-<width>-<theme>.png` (360, 390, 1440; light, dark), `markets-v2-indices-1440-<theme>.png`,
 *   `markets-v2-list-<width>-<theme>.png` (390, 1440) and `chain-dish-v2-<width>-<theme>.png` (the summary block only, 390
 *   and 1440, so the option chain's own baselines stay as they are - they are the "before" pictures).
 * - The heat map on a desktop, the list on a phone; a tile opens the sector's stocks, a stock opens Charts; with the flag
 *   off the page is not in the navigation and its address says "Coming soon"; the phone layout rules.
 * After an intended change: `npx playwright test --project=phone e2e/markets-v2.e2e.ts --update-snapshots`, check the new
 * images, commit.
 */
const CHAIN = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown>;
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON = { ui_v2: true, markets_view: true };
const THEMES = ["light", "dark"] as const;
const DISH = {
  title: "What the chain shows",
  note: "What the chain's numbers show right now, for your own reading.",
  points: [
    { key: "pcr_zone", label: "PCR zone", state: "ok", text: "PCR is 0.84, in the low zone (0.70 to 0.90): call open interest is larger than put open interest." },
    { key: "oi_walls", label: "OI walls", state: "ok", text: "The largest put open interest is at 100 and the largest call open interest is at 130; the spot 118.00 is between them." },
    { key: "max_pain", label: "Max pain", state: "ok", text: "Max pain is 120, 2.00 points (1.69%) above the spot 118.00." },
    { key: "iv_change", label: "IV change", state: "ok", text: "ATM IV is 15.00%, up 2.00 points since 09:20 IST (13.00% then)." },
    { key: "expected_move", label: "Expected move", state: "unknown", text: "I don't know the expected move: the ATM call or put has no price." },
  ],
};

async function prepare(page: Page, theme: "light" | "dark", features: Record<string, boolean>) {
  await page.clock.install({ time: new Date("2026-10-21T18:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["markets", "option-chain"]));
    } catch { /* storage unavailable */ }
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features, brokers: CONNECTED });
  const reads: string[] = [];
  await page.route(/\/api(?:\/v1)?\/markets\/overview$/, (r) => { reads.push("overview"); return r.fulfill({ json: DEMO_OVERVIEW }); });
  await page.route(/\/api(?:\/v1)?\/markets\/lists\/([^/?]+)$/, (r) => {
    const key = decodeURIComponent(/\/lists\/([^/?]+)$/.exec(new URL(r.request().url()).pathname)![1]);
    reads.push(`list:${key}`);
    return r.fulfill({ json: { ...DEMO_LIST, list_key: key } });
  });
  return reads;
}

async function settle(page: Page) {
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

async function openMarkets(page: Page, theme: "light" | "dark", path = "/markets", features: Record<string, boolean> = ON) {
  const reads = await prepare(page, theme, features);
  await page.goto(path);
  await settle(page);
  return reads;
}

const shot = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };

for (const size of [{ width: 360, height: 800 }, { width: 390, height: 844 }]) {
  test.describe(`v2 markets ${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`looks the same (${theme})`, async ({ page }) => {
        await openMarkets(page, theme);
        await expect(page.locator("[data-page='markets-v2']")).toBeVisible();
        await expect(page).toHaveScreenshot(`markets-v2-${size.width}-${theme}.png`, { ...shot, fullPage: true });
      });
    }

    test("a list on the phone, no sideways scroll, 44 px targets", async ({ page }) => {
      await openMarkets(page, "dark");
      await expect(page.locator("[data-list]")).toBeVisible();
      await expect(page.locator("[data-heatmap]")).toBeHidden();
      await expectPhoneLayout(page);
    });
  });
}

test.describe("v2 markets 390: a sector's stocks", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await openMarkets(page, theme, "/markets?view=sectors&period=day&list=sector-it");
      await expect(page.locator("[data-block='list-detail']")).toBeVisible();
      await expect(page).toHaveScreenshot(`markets-v2-list-390-${theme}.png`, { ...shot, fullPage: true });
    });
  }

  test("phone layout of the stocks list", async ({ page }) => {
    await openMarkets(page, "light", "/markets?list=sector-it");
    await expect(page.locator("[data-member='THETA']")).toBeVisible();
    await expectPhoneLayout(page);
  });

  test("M75 NAV-3: a stock opens its chart in a bottom sheet; Back returns to the same list", async ({ page }) => {
    await openMarkets(page, "light", "/markets?list=sector-it");
    await page.locator("[data-member='THETA'] a").click();
    const chart = page.locator("[data-inplace-panel='chart']");
    await expect(chart.locator("[data-embedded-chart='THETA']")).toBeVisible();
    await expect(chart).toHaveAttribute("data-inplace-shape", "bottom");
    expect(new URL(page.url()).pathname).toBe("/markets");
    await expectPhoneLayout(page);
    await page.goBack();
    await expect(chart).toHaveCount(0);
    await expect(page).toHaveURL(/\/markets\?list=sector-it$/);
    await expect(page.locator("[data-member='THETA']")).toBeVisible();
  });
});

test.describe("v2 markets 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await openMarkets(page, theme);
      await expect(page.locator("[data-heatmap]")).toBeVisible();
      await expect(page).toHaveScreenshot(`markets-v2-1440-${theme}.png`, { ...shot, fullPage: true });
    });

    test(`indices, grouped by family (${theme})`, async ({ page }) => {
      await openMarkets(page, theme, "/markets?view=indices&period=week");
      await expect(page.locator("[data-family='SECTORAL']")).toBeVisible();
      await expect(page).toHaveScreenshot(`markets-v2-indices-1440-${theme}.png`, { ...shot, fullPage: true });
    });

    test(`a sector's stocks (${theme})`, async ({ page }) => {
      await openMarkets(page, theme, "/markets?list=sector-it");
      await expect(page.locator("[data-block='list-detail']")).toBeVisible();
      await expect(page).toHaveScreenshot(`markets-v2-list-1440-${theme}.png`, { ...shot, fullPage: true });
    });
  }

  test("opens ready; a tile opens the sector's stocks; a stock opens Charts; back to all", async ({ page }) => {
    const reads = await openMarkets(page, "light");
    await expect(page.locator("[data-shell='v2'] header").first()).toContainText("Sectors & indices");
    await expect(page.locator("[data-block='markets-dish']")).toContainText("NIFTY 50▲+0.45%");   // UX-11b C4: a Signed move
    await expect(page.locator("[data-tile='Realty']")).toContainText("I don't know");
    await page.locator("[data-tile='IT']").click();
    await expect(page).toHaveURL(/\/markets\?view=sectors&period=day&list=sector-it$/);
    await expect(page.locator("[data-member='THETA']")).toBeVisible();
    expect(reads).toEqual(["overview", "list:sector-it"]);
    await page.getByRole("button", { name: "All sectors" }).click();
    await expect(page.locator("[data-heatmap]")).toBeVisible();
    await page.locator("[data-tile='IT']").click();
    // M75 NAV-3: a stock opens its chart in place; the path stays and closing returns to the same stocks list.
    await page.locator("[data-member='THETA'] a").click();
    const chart = page.locator("[data-inplace-panel='chart']");
    await expect(chart.locator("[data-embedded-chart='THETA']")).toBeVisible();
    await expect(chart).toHaveAttribute("data-inplace-shape", "right");
    expect(new URL(page.url()).pathname).toBe("/markets");
    expect(new URL(page.url()).searchParams.get("list")).toBe("sector-it");
    await page.keyboard.press("Escape");
    await expect(chart).toHaveCount(0);
    await expect(page).toHaveURL(/\/markets\?view=sectors&period=day&list=sector-it$/);
    await expect(page.locator("[data-member='THETA']")).toBeVisible();
  });

  test("M75 NAV-3: an index tile without a stocks list opens its chart in place; closing keeps the view", async ({ page }) => {
    await openMarkets(page, "light", "/markets?view=indices&period=week");
    await page.locator("[data-tile='SENSEX']").click();
    const chart = page.locator("[data-inplace-panel='chart']");
    await expect(chart.locator("[data-embedded-chart='SENSEX']")).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/markets");
    await page.keyboard.press("Escape");
    await expect(chart).toHaveCount(0);
    await expect(page).toHaveURL(/\/markets\?view=indices&period=week$/);
    await expect(page.locator("[data-family='BSE']")).toContainText("SENSEX");
  });

  test("the period and the view are in the address", async ({ page }) => {
    await openMarkets(page, "light");
    await page.getByRole("group", { name: "Period" }).getByRole("button", { name: "Month" }).click();
    await expect(page).toHaveURL(/period=month/);
    await expect(page.locator("[data-block='markets-dish']")).toContainText("NIFTY 50▼−0.80%");
    await page.getByRole("group", { name: "Show" }).getByRole("button", { name: "Indices" }).click();
    await expect(page).toHaveURL(/view=indices/);
    await expect(page.locator("[data-family='BSE']")).toContainText("SENSEX");
  });

  test("with `markets_view` off: not in the navigation, the address says Coming soon, nothing is read", async ({ page }) => {
    const reads = await openMarkets(page, "light", "/markets", { ui_v2: true });
    await expect(page.locator("[data-state='coming-soon']")).toBeVisible();
    await expect(page.locator("nav").getByText("Sectors & indices")).toHaveCount(0);
    expect(reads).toEqual([]);
  });

  test("with `markets_view` on: in the Markets section of the sidebar", async ({ page }) => {
    await openMarkets(page, "light");
    await expect(page.locator("nav a[href='/markets']").first()).toBeVisible();
  });
});

async function openChain(page: Page, theme: "light" | "dark", features: Record<string, boolean>) {
  await prepare(page, theme, features);
  const analyses: string[] = [];
  const sessions = { NSE: { is_open: true, reason: "made up", next_open: null } };
  await page.route(/\/api(?:\/v1)?\/system\/status$/, (r) => r.fulfill({ json: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions } }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, async (route) => {
    const url = new URL(route.request().url());
    const m = /\/option-chain\/([^/]+)\/(expiries|live)/.exec(url.pathname)!;
    const symbol = decodeURIComponent(m[1]);
    if (m[2] === "expiries") return route.fulfill({ json: { ...CHAIN.expiries, underlying: symbol } });
    return route.fulfill({ json: { ...CHAIN.live, underlying: symbol, expiry: url.searchParams.get("expiry") ?? CHAIN.live.expiry, market_open: true } });
  });
  await page.route(/\/api(?:\/v1)?\/option-chain\/analyses/, (r) => {
    analyses.push(r.request().url());
    const symbol = new URL(r.request().url()).searchParams.get("symbol") ?? "NIFTY";
    return r.fulfill({ json: { ...DEMO_ANALYSES, symbol, ...(features.markets_view ? { dish: DISH } : {}) } });
  });
  await page.route(/\/api(?:\/v1)?\/me\/prefs$/, (r) => r.fulfill({ json: {
    prefs: { chart: {}, setup: {}, oc_analyses: { items: {} }, appearance: { text_size: "normal" } },
    versions: { chart: 0, setup: 0, oc_analyses: 0, appearance: 0 } } }));
  await page.goto("/option-chain");
  await settle(page);
  return analyses;
}

test.describe("v2 option chain: no prose summary (UI-28 U1.2)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("with `markets_view` on or off: the numbers strip only, no 'What the chain shows' block; one read per chain", async ({ page }) => {
    const reads = await openChain(page, "light", ON);
    await expect(page.locator("[data-block='chain-summary']")).toBeVisible();
    await expect(page.locator("[data-block='chain-dish']")).toHaveCount(0);
    expect(new Set(reads).size).toBe(reads.length);
  });

  test("with `markets_view` off there is no summary either", async ({ page }) => {
    await openChain(page, "light", { ui_v2: true });
    await expect(page.locator("[data-block='chain-summary']")).toBeVisible();
    await expect(page.locator("[data-block='chain-dish']")).toHaveCount(0);
  });
});

/** UX-11 PR 5 (owner, 27 Oct): numbers first - every Markets card passes the text budget; the visible-word count is printed. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`v2 Markets text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await openMarkets(page, "light");
      console.log(`[words] Markets ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
