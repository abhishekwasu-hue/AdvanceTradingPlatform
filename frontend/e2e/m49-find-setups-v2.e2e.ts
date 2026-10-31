import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M49 U6 (owner-approved mockup docs/ui/U6_SCREENER.html, `ui_v2` + `screener_v2`): Find setups with ONE "Start a scan"
 * chooser (Ready scan / Template / Own words), Custom scan folded in as "Conditions" (`/screener/custom` opens the same
 * page with Conditions open), "Matched N of M" said once - in the results header, after a run - and no "Autopilot".
 * Symbols are made up; no prices anywhere.
 * - Baselines `m49-find-setups-v2-<width>-<theme>.png` (390 / 1440, light / dark): a ready scan run on NIFTY 50.
 * After an intended change: `npx playwright test --project=phone e2e/m49-find-setups-v2.e2e.ts --update-snapshots`.
 */
const read = (path: string): unknown => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
const registry = (read("../src/screener/builderTexts.json") as { registry: unknown }).registry;
const taxonomy = read("../src/screener/taxonomySample.json");
const FIFTY = Array.from({ length: 50 }, (_, i) => `STOCK${String(i + 1).padStart(2, "0")}`);
const UNIVERSES = {
  default_key: "index-nifty50",
  groups: [{ group: "INDEX_STOCKS", label: "Index stocks", lists: [{ key: "index-nifty50", name: "NIFTY 50 stocks (50)", count: 50 }] }],
  batch: { size: 40, per_minute: 100, per_second: 4, max_symbols: 500 },
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, screener_v2: true, screener_gallery: true, screener_copilot: true }, brokers: CONNECTED };
const THEMES = ["dark", "light"] as const;

interface RunBody { source: string; symbols: string[]; preview?: boolean }

function answer(body: RunBody) {
  const matched = body.symbols.filter((_, i) => i % 12 === 0);
  return {
    run_id: 9, preview: false, text: body.source, base_tf: "1d", data_source: "broker:upstox", scanned: body.symbols.length, matched,
    results: body.symbols.map((s) => ({ symbol: s, matched: matched.includes(s), reason: null })),
    funnel: { universe: body.symbols.length, with_data: body.symbols.length,
      stages: body.source.split(" AND ").map((text, i, all) => ({ text, survivors: i === all.length - 1 ? matched.length : Math.ceil(body.symbols.length / 2) })) },
    disclaimer: "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations.",
  };
}

async function open(page: Page, theme: "dark" | "light", at = "/screener") {
  const runs: RunBody[] = [];
  await page.clock.setFixedTime(new Date("2026-10-23T10:05:00Z"));
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["screener", "scanner"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, ON);
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/taxonomy$/.test(url)) return route.fulfill({ json: taxonomy });
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
    if (/\/universes$/.test(url)) return route.fulfill({ json: UNIVERSES });
    if (/\/universes\/index-nifty50$/.test(url)) {
      return route.fulfill({ json: { key: "index-nifty50", name: "NIFTY 50", symbols: FIFTY, truncated: false, source_count: 50, exchange: "NSE" } });
    }
    if (/\/run$/.test(url)) {
      const body = route.request().postDataJSON() as RunBody;
      if (body.preview) return route.fulfill({ status: 503, json: { detail: "no preview here" } });
      runs.push(body);
      return route.fulfill({ json: answer(body) });
    }
    return route.fulfill({ status: 503, headers: { "X-Feature-Disabled": "screener_legends" }, json: { detail: "disabled" } });
  });
  await page.goto(at);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await expect(page.getByTestId("start-scan")).toBeVisible();
  return runs;
}

test.describe("M49 U6 Find setups, 1440", () => {
  test.use({ viewport: { width: 1440, height: 1000 } });

  test("one Start a scan chooser; Conditions is one line until a scan fills it", async ({ page }) => {
    await open(page, "light");
    await expect(page.getByTestId("start-scan")).toHaveCount(1);
    const chooser = page.getByRole("tablist", { name: "Start a scan" });
    await expect(chooser.getByRole("tab")).toHaveCount(3);
    for (const name of ["Ready scan", "Template", "Own words"]) await expect(chooser.getByRole("tab", { name, exact: true })).toBeVisible();
    await expect(page.getByTestId("ready-scan-run")).toHaveCount(3);
    await expect(page.getByTestId("conditions-toggle")).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByText("Start with a ready scan")).toHaveCount(0);
    await expect(page.getByTestId("run-why")).toHaveText("Pick a scan first");
    await chooser.getByRole("tab", { name: "Template" }).click();
    await page.getByRole("button", { name: "Momentum" }).click();
    await expect(page.getByTestId("conditions-toggle")).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("heading", { name: "Conditions (2)" })).toBeVisible();
    await expect(page.getByLabel("Scan name")).toHaveValue("Momentum");
  });

  test("/screener/custom opens the same page with Conditions open; the custom scanner is inside it", async ({ page }) => {
    await open(page, "light", "/screener/custom");
    await expect(page).toHaveURL(/\/screener\/custom$/);
    await expect(page.getByTestId("conditions-toggle")).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByTestId("conditions-empty")).toContainText("Pick a scan above, or add your first condition.");
    await page.getByTestId("custom-scanner-toggle").click();
    await expect(page.getByTestId("custom-scanner")).toContainText("Indicator filters");
  });

  test("the old /scanner address lands on the merged page too", async ({ page }) => {
    await open(page, "light", "/scanner");
    await expect(page).toHaveURL(/\/screener\/custom$/);
    await expect(page.getByTestId("conditions-toggle")).toHaveAttribute("aria-expanded", "true");
  });
});

for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 1000 }]) {
  test.describe(`M49 U6 Find setups after a run, ${size.width}`, () => {
    test.use(size.width < 640 ? { viewport: size, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size });
    for (const theme of THEMES) {
      test(`a ready scan run: Matched once, no Autopilot, ${theme}`, async ({ page }) => {
        const runs = await open(page, theme);
        await page.getByTestId("ready-scan-run").first().click();
        await expect.poll(() => runs.length).toBe(1);
        await expect(page.getByTestId("results-matched")).toHaveText("Matched 5 of 50");
        await expect(page.getByText(/Matched\s*\d/)).toHaveCount(1);              // the count once (rows say "Matched" each)
        await expect(page.getByTestId("screener-bottom-bar")).not.toContainText("Matched");
        await expect(page.locator("body")).not.toContainText(/autopilot/i);
        await expect(page.getByTestId("scan-alert-open")).toBeVisible();
        if (size.width < 640) await expectPhoneLayout(page);
        await expect.poll(async () => page.evaluate(() => { window.scrollTo(0, 0); return window.scrollY; })).toBe(0);
        await page.waitForTimeout(300);
        await expect(page).toHaveScreenshot(`m49-find-setups-v2-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }
  });
}
