import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { screenerTool } from "./screenerTools";

/**
 * FN2-g / FN2-i (docs/design/FN2_GAP_AUDIT.md rows 12 and 15): on Find setups (ui_v2) a run's matches are saved as a new
 * watchlist, and "Past runs" lists the trader's own earlier runs, one reopened with its stored results (and its matches
 * saved from there). Flags: the trader pack (backend/app/platform/trader_pack.py). Every reply is made up; names only
 * for public lists. Setup copied from e2e/d1-find-setups-v2.e2e.ts.
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
const DISCLAIMER = "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations.";
const OLD_MATCHES = [FIFTY[1], FIFTY[2], FIFTY[3]];
const PAST = [
  { run_id: 30, screen_id: 41, screen_name: "Trend up", base_tf: "1d", scanned: 50, matched: 3, first_matches: OLD_MATCHES, created_at: "2026-10-22T09:30:00Z" },
  { run_id: 29, screen_id: null, screen_name: null, base_tf: "1w", scanned: 50, matched: 0, first_matches: [], created_at: "2026-10-21T09:30:00Z" },
];

interface World { runs: number; created: { name: string; items: { symbol: string; exchange: string }[] }[]; opened: number[] }
const body = (route: Route): unknown => { try { return route.request().postDataJSON(); } catch { return route.request().postData(); } };

async function mockWorld(page: Page): Promise<World> {
  const world: World = { runs: 0, created: [], opened: [] };
  await mockApi(page, { features: FEATURES, brokers: TOKEN });
  await mockDashboard(page, { state: "empty" });
  const me = { ...(F.me as Record<string, unknown>), role: "USER", scopes: ["trading:write", "alerts:write"] };
  await page.route(api("/auth/me$"), (r) => r.fulfill({ json: me }));
  await page.route(api("/system/features$"), (r) => r.fulfill({ json: FEATURES }));
  await page.route(api("/broker/token-status$"), (r) => r.fulfill({ json: TOKEN }));
  await page.route(api("/market-data/sources$"), (r) => r.fulfill({ json: SOURCES }));
  await page.route(/\/api(\/v1)?\/watchlists(\?.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      const b = body(route) as World["created"][number];
      if (world.created.some((c) => c.name === b.name)) return route.fulfill({ status: 409, json: { detail: `you already have a watchlist named '${b.name}'` } });
      world.created.push(b);
      return route.fulfill({ status: 201, json: { id: 70 + world.created.length, name: b.name, items: b.items, version: 1, symbols: b.items.map((i) => i.symbol), position: null } });
    }
    return route.fulfill({ json: world.created.map((c, i) => ({ id: 71 + i, name: c.name, symbols: c.items.map((x) => x.symbol), count: c.items.length,
      created_at: null, updated_at: null })) });
  });
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/taxonomy$/.test(url)) return route.fulfill({ json: taxonomy });
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
    if (/\/universes$/.test(url)) return route.fulfill({ json: UNIVERSES });
    if (/\/universes\/index-nifty50$/.test(url)) {
      return route.fulfill({ json: { key: "index-nifty50", name: "NIFTY 50 stocks (50)", symbols: FIFTY, truncated: false, source_count: 50, exchange: "NSE" } });
    }
    if (/\/runs\?limit=\d+$/.test(url)) return route.fulfill({ json: { runs: PAST, disclaimer: DISCLAIMER } });
    const one = /\/runs\/(\d+)$/.exec(url);
    if (one) {
      const id = Number(one[1]);
      world.opened.push(id);
      if (id !== 30) return route.fulfill({ status: 404, json: { detail: "Run not found" } });
      return route.fulfill({ json: { run_id: 30, screen_id: 41, ast_sha256: "0".repeat(64), version: "screenql/1", base_tf: "1d", universe: FIFTY,
        data_source: "broker:upstox", scanned: 50, matched: 3, results: FIFTY.map((s) => ({ symbol: s, matched: OLD_MATCHES.includes(s), reason: null })),
        unread: 0, created_at: PAST[0].created_at, disclaimer: DISCLAIMER } });
    }
    if (/\/run$/.test(url)) {
      const b = body(route) as { source: string; symbols: string[]; preview?: boolean };
      if (b.preview) return route.fulfill({ status: 503, json: { detail: "no preview here" } });
      world.runs += 1;
      const matched = b.symbols.filter((_, i) => i % 9 === 4);
      return route.fulfill({ json: {
        run_id: 31, preview: false, text: b.source, base_tf: "1d", data_source: "broker:upstox", scanned: b.symbols.length, matched,
        results: b.symbols.map((s) => ({ symbol: s, matched: matched.includes(s), reason: null })),
        funnel: { universe: b.symbols.length, with_data: b.symbols.length,
          stages: b.source.split(" AND ").map((text, i, all) => ({ text, survivors: i === all.length - 1 ? matched.length : Math.ceil(b.symbols.length / 2) })) },
        disclaimer: DISCLAIMER,
      } });
    }
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

const SHOTS = process.env.FN2G_SHOTS;

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  test.describe(`FN2-g / FN2-i ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

    test("save a run's matches as a watchlist; reopen a past run and save its matches", async ({ page }) => {
      const shot = async (step: string) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${step}-${size.width}.png`, fullPage: true, animations: "disabled" }); };
      await page.addInitScript(() => { try { localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ } });
      await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
      const world = await mockWorld(page);
      await page.goto("/screener");
      await settle(page);

      // FN2-g: after a run, the matches become a new list (named from the scan to start; a taken name says so)
      await expect(page.getByTestId("save-matches-open")).toHaveCount(0);
      await page.getByTestId("ready-scans").getByTestId("ready-scan-run").first().click();
      await expect.poll(() => world.runs).toBe(1);
      const open = page.getByTestId("save-matches-open");
      await expect(open).toBeVisible();
      if (size.phone) await fingerSized(open);
      await open.click();
      const nameBox = page.getByTestId("save-matches-name");
      await expect(nameBox).toHaveValue(/ matches$/);
      if (size.phone) { await fingerSized(nameBox); await fingerSized(page.getByTestId("save-matches-save")); }
      await nameBox.fill("Breakouts today");
      await shot("01-save-matches-form");
      await page.getByTestId("save-matches-save").click();
      await expect.poll(() => world.created.length).toBe(1);
      const expected = FIFTY.filter((_, i) => i % 9 === 4);
      expect(world.created[0]).toEqual({ name: "Breakouts today", items: expected.map((symbol) => ({ symbol, exchange: "NSE" })) });
      await expect(page.getByText(`Saved "Breakouts today" with ${expected.length} symbols.`)).toBeVisible();
      await expect(page.getByTestId("save-matches-form")).toHaveCount(0);
      await page.getByTestId("save-matches-open").click();
      await page.getByTestId("save-matches-name").fill("Breakouts today");
      await page.getByTestId("save-matches-save").click();
      await expect(page.getByTestId("save-matches-problem")).toHaveText('You already have a list named "Breakouts today". Pick another name.');
      expect(world.created).toHaveLength(1);
      // no sideways scroll on a phone (the page's other controls are checked by their own specs)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

      // FN2-i: "Past runs" lists his own runs in words; one reopens with its stored results
      await screenerTool(page, "past-runs-open");
      const dialog = page.getByRole("dialog", { name: "Past runs" });
      const list = dialog.getByTestId("past-runs");
      await expect(list).toBeVisible();
      await expect(list.getByTestId("past-run-30")).toContainText("Trend up · Daily");
      await expect(list.getByTestId("past-run-30")).toContainText(`3 of 50 matched: ${OLD_MATCHES.join(", ")}`);
      await expect(list.getByTestId("past-run-29")).toContainText("0 of 50 matched");
      await expect(dialog.getByText(/run_id|\b1d\b|\b1w\b/)).toHaveCount(0);
      if (size.phone) await fingerSized(list.getByTestId("past-run-open-30"));
      await shot("02-past-runs");
      await list.getByTestId("past-run-open-30").click();
      const opened = dialog.getByTestId("past-run-opened");
      await expect(opened).toBeVisible();
      expect(world.opened).toEqual([30]);
      for (const s of OLD_MATCHES) await expect(opened.getByRole("cell", { name: s, exact: true })).toBeVisible();
      if (size.phone) {
        await fingerSized(opened.getByTestId("past-run-back"));
        expect(await dialog.evaluate((d) => d.scrollWidth <= d.clientWidth + 1)).toBe(true);
      }
      await shot("03-past-run-opened");
      await opened.getByTestId("save-matches-open").click();
      await opened.getByTestId("save-matches-name").fill("Trend up then");
      await opened.getByTestId("save-matches-save").click();
      await expect.poll(() => world.created.length).toBe(2);
      expect(world.created[1].items.map((i) => i.symbol)).toEqual(OLD_MATCHES);
      await opened.getByTestId("past-run-back").click();
      await expect(dialog.getByTestId("past-runs")).toBeVisible();
    });
  });
}
