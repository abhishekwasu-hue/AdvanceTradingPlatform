import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * FN2-b (docs/design/FN2_GAP_AUDIT.md row 6): "n bars ago" on the screener builder's indicator block. A trader adds an
 * indicator block, sets its left side to 3 bars ago under the block's options, sees it in the block's sentence and in
 * the block's code, and Run sends `RSI(14)[3] > 60`; a value beyond the server's limit is marked and not applied, and
 * clearing it goes back to the current bar. Setup copied from d1-find-setups-v2.e2e.ts; every reply is made up.
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
// --- the system lists (names only) --------------------------------------------------------------------------------
const named = (symbols: string[], exchange = "NSE") => symbols.map((symbol) => ({ symbol, exchange, name: symbol }));
const sysList = (key: string, group: string, label: string, name: string, items: { symbol: string; exchange: string; name: string }[]) =>
  ({ key, group, group_label: label, name, count: items.length, read_only: true, refreshed_at: "2026-10-22T13:00:00Z", items });
const SYS_LISTS = [
  sysList("indices", "INDICES", "Indices", "Indices", [...named(["NIFTY 50", "NIFTY BANK", "FINNIFTY", "NIFTY IT"]), ...named(["SENSEX"], "BSE")]),
  sysList("index-nifty50", "INDEX_STOCKS", "Index stocks", "NIFTY 50 stocks (50)", named(FIFTY)),
  sysList("index-niftybank", "INDEX_STOCKS", "Index stocks", "NIFTY BANK stocks (6)",
    named(["AXISBANK", "HDFCBANK", "ICICIBANK", "INDUSINDBK", "KOTAKBANK", "SBIN"])),
  sysList("sector-it", "SECTORS", "Sectors", "IT (5)", named(["HCLTECH", "INFY", "TCS", "TECHM", "WIPRO"])),
  sysList("sector-banking", "SECTORS", "Sectors", "Banking (6)", named(["AXISBANK", "HDFCBANK", "ICICIBANK", "INDUSINDBK", "KOTAKBANK", "SBIN"])),
  sysList("fno-stocks", "FNO", "F&O", "F&O stocks (8)", named(["AXISBANK", "HDFCBANK", "INFY", "ITC", "RELIANCE", "SBIN", "TCS", "TITAN"])),
];
const UNIVERSES = {
  default_key: "index-nifty50",
  groups: [
    { group: "INDEX_STOCKS", label: "Index stocks", lists: [{ key: "index-nifty50", name: "NIFTY 50 stocks (50)", count: 50 },
      { key: "index-niftybank", name: "NIFTY BANK stocks (6)", count: 6 }] },
    { group: "SECTORS", label: "Sectors", lists: [{ key: "sector-it", name: "IT (5)", count: 5 }, { key: "sector-banking", name: "Banking (6)", count: 6 }] },
    { group: "FNO", label: "F&O", lists: [{ key: "fno-stocks", name: "F&O stocks (8)", count: 8 }] },
  ],
  batch: { size: 40, per_minute: 100, per_second: 4, max_symbols: 500 },
};

const TOKEN = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null, needs_login: false,
  oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const SOURCES = { sources: [{ broker: "upstox", account_label: "primary", token_status: "VALID", usable: true }], usable: true,
  timeframes: ["1m", "5m", "15m", "day"], max_symbols: 50, intraday_max_lookback_days: 30, daily_max_lookback_days: 730 };

const body = (route: Route): unknown => { try { return route.request().postDataJSON(); } catch { return route.request().postData(); } };

async function mockWorld(page: Page): Promise<{ runs: string[] }> {
  const world = { runs: [] as string[] };
  await mockApi(page, { features: FEATURES, brokers: TOKEN });
  await mockDashboard(page, { state: "empty" });
  const me = { ...(F.me as Record<string, unknown>), role: "USER", scopes: ["trading:write", "alerts:write"] };
  await page.route(api("/auth/me$"), (r) => r.fulfill({ json: me }));
  await page.route(api("/system/features$"), (r) => r.fulfill({ json: FEATURES }));
  await page.route(api("/broker/token-status$"), (r) => r.fulfill({ json: TOKEN }));
  await page.route(api("/market-data/sources$"), (r) => r.fulfill({ json: SOURCES }));
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/taxonomy$/.test(url)) return route.fulfill({ json: taxonomy });
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
    if (/\/universes$/.test(url)) return route.fulfill({ json: UNIVERSES });
    const one = /\/universes\/([a-z0-9-]+)$/.exec(url);
    if (one) {
      const list = SYS_LISTS.find((l) => l.key === one[1]);
      return list ? route.fulfill({ json: { key: list.key, name: list.name, symbols: list.items.map((i) => i.symbol), truncated: false,
        source_count: list.count, exchange: "NSE" } }) : route.fulfill({ status: 404, json: { detail: "This list is not available yet." } });
    }
    if (/\/run$/.test(url)) {
      const b = body(route) as { source: string; symbols: string[]; preview?: boolean };
      if (b.preview) return route.fulfill({ status: 503, json: { detail: "no preview here" } });
      world.runs.push(b.source);
      const matched = b.symbols.filter((_, i) => i % 9 === 4);
      return route.fulfill({ json: {
        run_id: 32, preview: false, text: b.source, base_tf: "1d", data_source: "broker:upstox", scanned: b.symbols.length, matched,
        results: b.symbols.map((s) => ({ symbol: s, matched: matched.includes(s), reason: null })),
        funnel: { universe: b.symbols.length, with_data: b.symbols.length, stages: [{ text: b.source, survivors: matched.length }] },
        disclaimer: "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations.",
      } });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  return world;
}

async function settle(page: Page) {
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  test.describe(`FN2-b: bars ago on the indicator block ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

    test("set under the block's options, shown in its sentence and code, sent as x[n]; out of range is refused", async ({ page }) => {
      await page.addInitScript(() => {
        try { localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ }
      });
      await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
      const world = await mockWorld(page);
      await page.goto("/screener/custom");
      await settle(page);

      await page.getByTestId("conditions-empty").getByRole("button", { name: "Add a condition" }).click();
      const row = page.getByTestId("stage-row").first();
      await expect(row).toHaveAttribute("data-kind", "indicator");
      await expect(row.getByTestId("bars-ago-left-inline")).toHaveCount(0);           // the current bar: the sentence as before

      await row.getByRole("button", { name: "Advanced options for stage 1" }).click();
      const field = row.getByTestId("bars-ago-left").getByRole("textbox", { name: "Left side, bars ago" });
      await expect(field).toHaveValue("");
      await expect(field).toHaveAttribute("placeholder", "now");
      const box = await field.boundingBox();
      expect(box && box.height).toBeGreaterThanOrEqual(size.phone ? 43.5 : 27);

      await field.fill("501");
      await field.press("Enter");
      await expect(field).toHaveAttribute("aria-invalid", "true");
      await expect(row.getByTestId("bars-ago-left-inline")).toHaveCount(0);

      await field.fill("3");
      await field.press("Enter");
      await expect(field).not.toHaveAttribute("aria-invalid", "true");
      await expect(row.getByTestId("bars-ago-left-inline").getByRole("textbox")).toHaveValue("3");
      await row.getByText("Advanced: the code").click();
      await expect(row.getByTestId("stage-code").locator("code")).toHaveText("RSI(14)[3] > 60");
      await expect(row).toContainText("Reads: RSI(14) 3 bars ago above 60");
      if (size.phone) await expectPhoneLayout(page);

      await page.getByTestId(size.phone ? "run-scan-mobile" : "run-scan").click();
      await expect.poll(() => world.runs.length).toBe(1);
      expect(world.runs[0]).toBe("RSI(14)[3] > 60");

      await field.fill("");
      await field.press("Enter");
      await expect(row.getByTestId("stage-code").locator("code")).toHaveText("RSI(14) > 60");
      await expect(row.getByTestId("bars-ago-left-inline")).toHaveCount(0);
    });
  });
}
