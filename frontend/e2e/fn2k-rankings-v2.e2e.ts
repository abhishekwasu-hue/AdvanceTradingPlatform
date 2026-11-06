import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { screenerTool } from "./screenerTools";

/**
 * FN2-k (docs/design/FN2_GAP_AUDIT.md row 15): "Rankings" on Find setups (ui_v2, flag `screener_rankings`): pick a ranking
 * system, rank the list 0-100, read the table, open one symbol's factors. Every reply is made up. Setup copied from
 * e2e/fn2g-past-runs-v2.e2e.ts.
 */

const PACK = (() => {
  const src = readFileSync(new URL("../../backend/app/platform/trader_pack.py", import.meta.url), "utf8");
  const body = /PACK: Tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/.exec(src)?.[1] ?? "";
  return [...body.replace(/#.*$/gm, "").matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
})();
const FEATURES: Record<string, boolean> = { ...Object.fromEntries(PACK.map((f) => [f, true])), backtest_stored_history: true };
const registry = (JSON.parse(readFileSync(new URL("../src/screener/builderTexts.json", import.meta.url), "utf8")) as { registry: unknown }).registry;
const taxonomy = JSON.parse(readFileSync(new URL("../src/screener/taxonomySample.json", import.meta.url), "utf8")) as unknown;
const FIFTY = [...(/NIFTY50_FALLBACK: readonly string\[\] = \[([\s\S]*?)\];/.exec(
  readFileSync(new URL("../src/screener/universe.ts", import.meta.url), "utf8"))?.[1] ?? "").matchAll(/"([^"]+)"/g)].map((m) => m[1]);
const UNIVERSES = {
  default_key: "index-nifty50",
  groups: [{ group: "INDEX_STOCKS", label: "Index stocks", lists: [{ key: "index-nifty50", name: "NIFTY 50 stocks (50)", count: 50 }] }],
  batch: { size: 40, per_minute: 100, per_second: 4, max_symbols: 500 },
};
const TOKEN = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null, needs_login: false,
  oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const SOURCES = { sources: [{ broker: "upstox", account_label: "primary", token_status: "VALID", usable: true }], usable: true,
  timeframes: ["1m", "5m", "15m", "day"], max_symbols: 50, intraday_max_lookback_days: 30, daily_max_lookback_days: 730 };
const DISCLAIMER = "A rank orders symbols by the factors shown, on the data shown; information only.";
const SYSTEMS = {
  platform: [
    { id: null, name: "platform:Momentum", description: "Price momentum over several windows.", platform: true, archived: false },
    { id: null, name: "platform:Low volatility", description: "Calm stocks.", platform: true, archived: false },
  ],
  systems: [{ id: 5, name: "My blend", description: "", platform: false, archived: false }],
  limits: { max_depth: 3, max_factors: 12 }, disclaimer: DISCLAIMER,
};
const comp = (name: string, value: number, score: number) => ({ path: `/${name}`, name, depth: 1, weight: 50, score, factor: "x", value, higher_is_better: true, neutral: false });
const RANKED = [
  { symbol: FIFTY[0], rank: 100, composite: 91.4, sector: null, as_of: "2026-10-22T00:00:00", components: [comp("12m return", 0.42, 98), comp("6m return", 0.2, 90)] },
  { symbol: FIFTY[1], rank: 50, composite: 55.2, sector: null, as_of: "2026-10-22T00:00:00", components: [comp("12m return", 0.1, 50), comp("6m return", 0.05, 52)] },
  { symbol: FIFTY[2], rank: 0, composite: 12.9, sector: null, as_of: "2026-10-22T00:00:00", components: [comp("12m return", -0.2, 5), comp("6m return", -0.1, 8)] },
];

const body = (route: Route): unknown => { try { return route.request().postDataJSON(); } catch { return route.request().postData(); } };

async function mockWorld(page: Page, features = FEATURES): Promise<{ runs: unknown[] }> {
  const world = { runs: [] as unknown[] };
  await mockApi(page, { features, brokers: TOKEN });
  await mockDashboard(page, { state: "empty" });
  const me = { ...(F.me as Record<string, unknown>), role: "USER", scopes: ["trading:write", "alerts:write"] };
  await page.route(api("/auth/me$"), (r) => r.fulfill({ json: me }));
  await page.route(api("/system/features$"), (r) => r.fulfill({ json: features }));
  await page.route(api("/broker/token-status$"), (r) => r.fulfill({ json: TOKEN }));
  await page.route(api("/market-data/sources$"), (r) => r.fulfill({ json: SOURCES }));
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/taxonomy$/.test(url)) return route.fulfill({ json: taxonomy });
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
    if (/\/universes$/.test(url)) return route.fulfill({ json: UNIVERSES });
    if (/\/universes\/index-nifty50$/.test(url)) {
      return route.fulfill({ json: { key: "index-nifty50", name: "NIFTY 50 stocks (50)", symbols: FIFTY, truncated: false, source_count: 50, exchange: "NSE" } });
    }
    if (/\/rankings\/run$/.test(url)) {
      world.runs.push(body(route));
      return route.fulfill({ json: { system: { name: "platform:Momentum", description: "d", tree: {} }, ranked: RANKED,
        not_ranked: { [FIFTY[3]]: "no factor value (too little history?)" }, scanned: 50, data_source: "broker:upstox", disclaimer: DISCLAIMER,
        method: "Each factor is a percentile across the symbols ranked; the rank is the composite's percentile, 0-100." } });
    }
    if (/\/rankings$/.test(url)) return route.fulfill({ json: SYSTEMS });
    if (/\/runs\?limit=\d+$/.test(url)) return route.fulfill({ json: { runs: [], disclaimer: DISCLAIMER } });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.route(/\/api(?:\/v1)?\/signals\/(feed|day-one)/, (route) => route.fulfill({ status: 404, json: { detail: "Not found" } }));
  return world;
}

async function settle(page: Page) {
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

async function fingerSized(target: Locator) {
  const box = await target.boundingBox();
  expect(box && box.height >= 43.5, `${await target.textContent()} is ${box?.height} px high`).toBeTruthy();
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  test.describe(`FN2-k ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

    test("rank the list by a system, read the table, open a symbol's factors", async ({ page }) => {
      await page.addInitScript(() => { try { localStorage.setItem("atp_install_dismissed", "1"); } catch { /* intentional: storage unavailable in the test browser */ } });
      await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
      const world = await mockWorld(page);
      await page.goto("/screener");
      await settle(page);

      await screenerTool(page, "rankings-open");
      const dialog = page.getByRole("dialog", { name: "Rankings" });
      await expect(dialog.getByTestId("rankings")).toBeVisible();
      await expect(dialog.getByTestId("ranking-system-momentum")).toHaveAttribute("aria-pressed", "true");
      await expect(dialog.getByTestId("ranking-system-my-blend")).toBeVisible();
      await expect(dialog.getByText(/platform:|system_id/)).toHaveCount(0);
      if (size.phone) { await fingerSized(dialog.getByTestId("ranking-system-low-volatility")); await fingerSized(dialog.getByTestId("rankings-run")); }
      await dialog.getByTestId("ranking-system-low-volatility").click();
      await dialog.getByTestId("ranking-system-momentum").click();
      await dialog.getByTestId("rankings-run").click();

      const result = dialog.getByTestId("rankings-result");
      await expect(result).toBeVisible();
      expect(world.runs).toEqual([{ system: "platform:Momentum", symbols: FIFTY, exchange: "NSE" }]);
      await expect(result).toContainText("3 of 50 ranked");
      const rows = result.locator("tbody tr[data-testid^='ranking-row-']");
      await expect(rows).toHaveCount(3);
      await expect(rows.nth(0)).toContainText(FIFTY[0]);
      await expect(rows.nth(0).locator("td").nth(1)).toHaveText("100");
      await expect(rows.nth(0).locator("td").nth(1)).toHaveClass(/text-up/);
      await expect(rows.nth(2).locator("td").nth(1)).toHaveClass(/text-down/);
      await expect(result.getByTestId("rankings-skipped")).toContainText(`${FIFTY[3]} (no factor value`);
      await expect(dialog.getByText(/\b(buy|sell|target|stop-loss)\b/i)).toHaveCount(0);

      const why = result.getByTestId(`ranking-why-${FIFTY[0]}`);
      if (size.phone) await fingerSized(why);
      await why.click();
      await expect(result.getByTestId(`ranking-factors-${FIFTY[0]}`)).toContainText("12m return");
      await expect(result.getByTestId(`ranking-factors-${FIFTY[0]}`)).toContainText("0.42");
      await result.locator("details[data-details='screener.rankings.about'] summary").click();
      await expect(result.getByText(/percentile across the symbols ranked/)).toBeVisible();

      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      if (size.phone) {
        expect(await dialog.evaluate((d) => d.scrollWidth <= d.clientWidth + 1)).toBe(true);
        await expectPhoneLayout(page);
      }
    });

    test("with the flag off there is no Rankings tool", async ({ page }) => {
      await page.addInitScript(() => { try { localStorage.setItem("atp_install_dismissed", "1"); } catch { /* intentional: storage unavailable in the test browser */ } });
      await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
      await mockWorld(page, { ...FEATURES, screener_rankings: false });
      await page.goto("/screener");
      await settle(page);
      const more = page.getByTestId("screener-more");
      if (await more.isVisible()) await more.click();
      await expect(page.getByTestId("past-runs-open")).toBeVisible();
      await expect(page.getByTestId("rankings-open")).toHaveCount(0);
    });
  });
}
