import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { screenerTool } from "./screenerTools";

/**
 * Owner order (23 Oct) "the scan universe is never empty" (`ui_v2` + `screener_v2`): Find setups opens on NIFTY 50
 * (the server's system list, or the built-in names when it has none), a longer list (NIFTY 500) runs in batches with
 * its progress and growing results, an empty choice runs on NIFTY 50 with a plain note, and a gallery card's "Try as a
 * scan" runs at once on the chosen universe. Symbols here are made up; no prices anywhere.
 * - Baselines `scan-universe-v2-<width>-<theme>.png` (390 / 1440, light / dark): NIFTY 500 scanned in batches.
 * After an intended change: `npx playwright test --project=phone e2e/scan-universe-v2.e2e.ts --update-snapshots`.
 */
const read = (path: string): unknown => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
const registry = (read("../src/screener/builderTexts.json") as { registry: unknown }).registry;
const taxonomy = read("../src/screener/taxonomySample.json");
const names = (n: number, prefix: string) => Array.from({ length: n }, (_, i) => `${prefix}${String(i + 1).padStart(3, "0")}`);
const FIFTY = names(50, "FIFTY");
const FIVE_HUNDRED = names(500, "STOCK");
const UNIVERSES = {
  default_key: "index-nifty50",
  groups: [
    { group: "INDEX_STOCKS", label: "Index stocks", lists: [{ key: "index-nifty50", name: "NIFTY 50 stocks (50)", count: 50 },
      { key: "index-nifty100", name: "NIFTY 100 stocks (100)", count: 100 }, { key: "index-nifty500", name: "NIFTY 500 stocks (500)", count: 500 }] },
    { group: "SECTORS", label: "Sectors", lists: [{ key: "sector-it", name: "IT", count: 0 }] },
    { group: "FNO", label: "F&O", lists: [{ key: "fno-stocks", name: "F&O stocks (180)", count: 180 }] },
  ],
  batch: { size: 40, per_minute: 100, per_second: 4, max_symbols: 500 },
};
const LISTS: Record<string, string[]> = { "index-nifty50": FIFTY, "index-nifty500": FIVE_HUNDRED, "sector-it": [] };
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, screener_v2: true, screener_gallery: true }, brokers: CONNECTED };
const THEMES = ["dark", "light"] as const;

interface RunBody { source: string; symbols: string[]; batch?: boolean; preview?: boolean }

function answer(body: RunBody) {
  const matched = body.symbols.filter((_, i) => i % 7 === 0);
  return {
    run_id: 9, preview: false, text: body.source, base_tf: "1d", data_source: "broker:upstox", scanned: body.symbols.length, matched,
    results: body.symbols.map((s) => ({ symbol: s, matched: matched.includes(s), reason: null })),
    funnel: { universe: body.symbols.length, with_data: body.symbols.length,
      stages: body.source.split(" AND ").map((text, i, all) => ({ text, survivors: i === all.length - 1 ? matched.length : Math.ceil(body.symbols.length / 2) })) },
    disclaimer: "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations.",
    ...(body.batch ? { budget: { broker_calls: 0, refused: 0, per_minute: 100 } } : {}),
  };
}

async function open(page: Page, theme: "dark" | "light", { universes = true, copilot = false, slow = 0 }: { universes?: boolean; copilot?: boolean; slow?: number } = {}) {
  const runs: RunBody[] = [];
  await page.clock.setFixedTime(new Date("2026-10-23T10:05:00Z"));          // "Ran at 15:35 IST" in every baseline
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["screener"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, copilot ? { ...ON, features: { ...ON.features, screener_copilot: true } } : ON);
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/taxonomy$/.test(url)) return route.fulfill({ json: taxonomy });
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
    if (/\/universes$/.test(url)) {
      return universes ? route.fulfill({ json: UNIVERSES })
        : route.fulfill({ status: 404, json: { detail: "Not found" } });
    }
    const one = /\/universes\/([a-z0-9-]+)$/.exec(url);
    if (one) {
      const symbols = LISTS[one[1]];
      return symbols ? route.fulfill({ json: { key: one[1], name: one[1], symbols, truncated: false, source_count: symbols.length, exchange: "NSE" } })
        : route.fulfill({ status: 404, json: { detail: "This list is not available yet." } });
    }
    if (/\/screens$/.test(url)) {
      const b = route.request().postDataJSON() as { name: string; source: string; base_tf: string };
      return route.fulfill({ status: 201, json: { id: 41, name: b.name, text: b.source, base_tf: b.base_tf } });
    }
    if (/\/screens\/41\/versions$/.test(url)) return route.fulfill({ json: { screen_id: 41, imported: true, max: 50, versions: [] } });
    if (/\/run$/.test(url)) {
      const body = route.request().postDataJSON() as RunBody;
      if (body.preview) return route.fulfill({ status: 503, json: { detail: "no preview here" } });
      runs.push(body);
      if (slow) await new Promise((r) => { setTimeout(r, slow); });
      return route.fulfill({ json: answer(body) });
    }
    return route.fulfill({ status: 503, headers: { "X-Feature-Disabled": "screener_legends" }, json: { detail: "disabled" } });
  });
  await page.goto("/screener");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await expect(page.getByText("Start with a template or add your first condition.")).toBeVisible();
  return runs;
}

async function addStarter(page: Page) {
  await screenerTool(page, "browse-screens");
  const dialog = page.getByRole("dialog", { name: "Screen library" });
  await dialog.getByRole("button", { name: /^Open .* in the builder$/ }).first().click();
  await expect(dialog).toBeHidden();
}

const runButton = (page: Page) => page.getByTestId(page.viewportSize()!.width < 640 ? "run-scan-mobile" : "run-scan");

test.describe("The scan universe, 1440", () => {
  test.use({ viewport: { width: 1440, height: 1000 } });

  test("opens on NIFTY 50 from the server's list and runs on it", async ({ page }) => {
    const runs = await open(page, "light");
    await expect(page.getByTestId("universe-select")).toHaveValue("sys:index-nifty50");
    await expect(page.getByTestId("universe-count")).toHaveText("50 stocks · NIFTY 50");
    await addStarter(page);
    await runButton(page).click();
    await expect.poll(() => runs.length).toBe(1);
    expect(runs[0].symbols).toEqual(FIFTY);
    expect(runs[0].batch).toBeUndefined();                     // one run of 50: exactly as before
  });

  test("without the server's lists, the built-in NIFTY 50 names stand in", async ({ page }) => {
    const runs = await open(page, "light", { universes: false });
    await expect(page.getByTestId("universe-select")).toHaveValue("sys:index-nifty50");
    await expect(page.getByTestId("universe-count")).toHaveText("50 stocks · NIFTY 50");
    await addStarter(page);
    await runButton(page).click();
    await expect.poll(() => runs.length).toBe(1);
    expect(runs[0].symbols).toHaveLength(50);
    expect(runs[0].symbols).toContain("RELIANCE");
  });

  test("an empty choice runs on NIFTY 50 and says so", async ({ page }) => {
    const runs = await open(page, "light");
    await page.getByTestId("universe-select").selectOption("sys:sector-it");
    await expect(page.getByTestId("universe-note")).toHaveText("This list has no stocks yet, so the scan runs on NIFTY 50.");
    await expect(page.getByTestId("universe-count")).toHaveText("50 stocks · NIFTY 50");
    await page.getByTestId("universe-select").selectOption("custom");
    await page.locator("#screener-symbols").fill("");
    await expect(page.getByTestId("universe-note")).toHaveText("Type at least one symbol. Until then the scan runs on NIFTY 50.");
    await page.locator("#screener-symbols").fill("ALPHA, BETA");
    await expect(page.getByTestId("universe-note")).toHaveCount(0);
    await addStarter(page);
    await runButton(page).click();
    await expect.poll(() => runs.length).toBe(1);
    expect(runs[0].symbols).toEqual(["ALPHA", "BETA"]);
  });

  test("Try as a scan runs at once on the chosen universe", async ({ page }) => {
    const runs = await open(page, "light");
    await screenerTool(page, "browse-screens");
    const dialog = page.getByRole("dialog", { name: "Screen library" });
    await dialog.getByTestId("screen-try").first().click();
    await expect(dialog).toBeHidden();
    await expect.poll(() => runs.length).toBe(1);
    expect(runs[0].symbols).toEqual(FIFTY);
    await expect(page.getByRole("list", { name: "Stages - every one must pass" }).locator(":scope > li").first()).toBeVisible();   // still editable
  });

  test("NIFTY 500 runs in batches the broker's budget allows, with progress and growing results", async ({ page }) => {
    const runs = await open(page, "light", { slow: 250 });
    await page.getByTestId("universe-select").selectOption("sys:index-nifty500");
    await expect(page.getByTestId("universe-count")).toHaveText("500 stocks · NIFTY 500");
    await expect(page.getByTestId("universe-batches")).toContainText("Run scans all 500, 40 at a time");
    await addStarter(page);
    await runButton(page).click();
    const progress = page.getByTestId("scan-progress");
    await expect(progress).toContainText(/Scanning (40|80|120) of 500 stocks/);                 // progress, and the results so far
    await expect(page.getByRole("cell", { name: "STOCK001" })).toBeVisible();
    await expect.poll(() => runs.length, { timeout: 15_000 }).toBe(13);
    expect(runs.every((r) => r.batch === true && r.symbols.length <= 40)).toBe(true);
    expect(runs.flatMap((r) => r.symbols)).toEqual(FIVE_HUNDRED);
    await expect(page.getByTestId("scan-progress")).toHaveCount(0);
  });

  test("an alert saved from the scan keeps the chosen universe", async ({ page }) => {
    await open(page, "light", { copilot: true });
    const rules: { symbols: string[] }[] = [];
    await page.route(/\/api(\/v1)?\/alerts\/rules$/, async (route) => {
      if (route.request().method() !== "POST") return route.fulfill({ json: [] });
      const b = route.request().postDataJSON() as { name: string; screen_id: number; symbols: string[]; fire_on: string };
      rules.push(b);
      return route.fulfill({ status: 201, json: { id: 7, name: b.name, kind: "screen", screen_id: b.screen_id, base_tf: "1d", status: "active",
        symbols: b.symbols, fire_on: b.fire_on } });
    });
    await page.getByTestId("universe-select").selectOption("sys:index-nifty500");
    await addStarter(page);
    await screenerTool(page, "scan-alert-open");
    const dialog = page.getByRole("dialog", { name: "Tell me when it matches" });
    await expect(dialog).toContainText("on NIFTY 500 (50 stocks)");
    await expect(dialog.getByTestId("scan-alert-first")).toHaveText("An alert watches up to 50 stocks, so this one watches the first 50 of the 500 in NIFTY 500.");
    await dialog.getByTestId("scan-alert-create").click();
    await expect(dialog.getByTestId("scan-alert-done")).toBeVisible();
    expect(rules[0].symbols).toEqual(FIVE_HUNDRED.slice(0, 50));
  });
});

for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 1000 }]) {
  test.describe(`The scan universe, ${size.width}`, () => {
    test.use(size.width < 640 ? { viewport: size, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size });
    for (const theme of THEMES) {
      test(`NIFTY 500 scanned in batches, ${theme}`, async ({ page }) => {
        const runs = await open(page, theme);
        await page.getByTestId("universe-select").selectOption("sys:index-nifty500");
        await addStarter(page);
        await runButton(page).click();
        await expect.poll(() => runs.length).toBe(13);
        await expect(page.getByTestId("scan-progress")).toHaveCount(0);
        if (size.width < 640) await expectPhoneLayout(page);
        // the phone keeps the scroll where the results grew to: the picture is of the page's top
        await expect.poll(async () => page.evaluate(() => { window.scrollTo(0, 0); return window.scrollY; })).toBe(0);
        await page.waitForTimeout(300);
        await expect.poll(async () => page.evaluate(() => { window.scrollTo(0, 0); return window.scrollY; })).toBe(0);
        await expect(page).toHaveScreenshot(`scan-universe-v2-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }
  });
}
