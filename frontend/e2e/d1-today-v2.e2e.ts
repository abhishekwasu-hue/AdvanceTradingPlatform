import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";

/**
 * Day-one fix 9 (docs/design/DAY_ONE_TRADER_TEST.md finding 10): Today for a new trader. The empty "Active signals"
 * block points to the one-tap Signals watch ("Watch a list on Signals") instead of the empty scan builder, and the
 * sidebar starts with Today (Broker, with its dot, sits just above Settings). Flags: the trader pack. Every reply is
 * made up. With D1F_SHOTS=<folder> each step is saved as `<step>-<width>-<theme>.png`; no baseline is stored.
 */

const PACK = (() => {
  const src = readFileSync(new URL("../../backend/app/platform/trader_pack.py", import.meta.url), "utf8");
  const body = /PACK: Tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/.exec(src)?.[1] ?? "";
  return [...body.replace(/#.*$/gm, "").matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
})();
const FEATURES: Record<string, boolean> = { ...Object.fromEntries(PACK.map((f) => [f, true])), backtest_stored_history: true };
const SHOTS = process.env.D1F_SHOTS;
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
const SYSTEM = {
  groups: ["INDICES", "INDEX_STOCKS", "SECTORS", "FNO"].map((g) => {
    const lists = SYS_LISTS.filter((l) => l.group === g);
    return { group: g, label: lists[0].group_label, lists: lists.map(({ items, ...summary }) => ({ ...summary, count: items.length })) };
  }),
  lists: Object.fromEntries(SYS_LISTS.map((l) => [l.key, l])),
};
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

const A5_IDEA = "After a level breaks, wait for the market to come back to it from the other side and reject it: old resistance acting as support, or old support as resistance. A retest idea, never a breakout chase.";
const B1_IDEA = "A signal on the first close beyond a range or a consolidation by a set distance; the idea has ended when the close is back inside the range.";
// --- Signals day one (the three offers the server makes, his watch after adopting A5 on NIFTY 50) -----------------
const SETTING = (key: string, label: string, value: string, help: string) => ({ key, label, value, help });
const OFFERS = [
  { id: "A5", name: "Structure flip retest", idea: A5_IDEA, difficulty: "Beginner", family: "A", timeframe: "Daily", rules: [],
    settings: [SETTING("retest_of", "What broke", "A swing level", "The kind of level whose retest is read."),
      SETTING("retest_max_bars", "Retest within (bars)", "10", "The return must come within this many bars after the break.")] },
  { id: "B1", name: "Range breakout", idea: B1_IDEA, difficulty: "Beginner", family: "B", timeframe: "Daily", rules: [], settings: [] },
  { id: "D3", name: "Moving-average pullback in trend", idea: "In a trend, wait for the pullback to reach the fast average and for a reversal there.",
    difficulty: "Intermediate", family: "D", timeframe: "Daily", rules: ["The pullback average must be shorter than the trend average."],
    settings: [SETTING("fast", "Pullback average", "20", "Bars in the fast average the pullback comes back to."),
      SETTING("slow", "Trend average", "50", "Bars in the slow average that defines the trend.")] },
];
const DAY_LISTS = [
  { kind: "system", key: "index-nifty50", name: "NIFTY 50 stocks (50)", group: "Index stocks", count: 50, default: true },
  { kind: "system", key: "index-niftybank", name: "NIFTY BANK stocks (6)", group: "Index stocks", count: 6, default: false },
  { kind: "system", key: "fno-stocks", name: "F&O stocks (8)", group: "F&O", count: 8, default: false },
];
const WATCHING = "No setup in place on the Daily chart; watching for a broken level to be retested from the other side.";
const row = (symbol: string, state: string, text: string, extra: Record<string, unknown> = {}) =>
  ({ symbol, exchange: "NSE", state, direction: null, text, area: null, setup_day: null, bar_day: "2026-10-22", ...extra });
const WATCH = {
  template: { id: "A5", name: "Structure flip retest", idea: A5_IDEA, timeframe: "Daily", settings: OFFERS[0].settings },
  strategy_id: "template:A5:1:1d:both:swing,,,,,10,", list: { kind: "system", key: "index-nifty50", name: "NIFTY 50 stocks (50)" }, truncated: false,
  count: 50, max_watch: 60, mode: "PAPER", entries: "APPROVAL", adopted_at: "2026-10-23T04:55:00Z", evaluated_at: "2026-10-23T04:55:00Z",
  bar_day: "2026-10-22", note: null,
  rows: [
    row(FIFTY[36], "new", "Former resistance on the Daily chart was broken and is being retested from above; rejection formed.",
      { direction: "LONG", setup_day: "2026-10-22" }),
    row(FIFTY[42], "armed", "Former support on the Daily chart was broken; price is back at it from below, waiting for a rejection there.",
      { direction: "SHORT", setup_day: "2026-10-21" }),
    row(FIFTY[23], "recent", "Former resistance on the Daily chart was broken and retested from above; rejection formed three bars ago.",
      { direction: "LONG", setup_day: "2026-10-17" }),
    ...FIFTY.filter((_, i) => ![36, 42, 23, 26].includes(i)).map((s) => row(s, "watching", WATCHING)),
    row(FIFTY[26], "warmup", "Not enough Daily history yet to read this setup."),
  ],
  summary: { new: 1, armed: 1, recent: 1, watching: 46, warmup: 1, no_data: 0, not_read: 0 },
};

const TOKEN = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null, needs_login: false,
  oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const SOURCES = { sources: [{ broker: "upstox", account_label: "primary", token_status: "VALID", usable: true }], usable: true,
  timeframes: ["1m", "5m", "15m", "day"], max_symbols: 50, intraday_max_lookback_days: 30, daily_max_lookback_days: 730 };

interface World { posts: { url: string; body: unknown }[]; adopted: boolean; runs: { symbols: string[] }[] }
const body = (route: Route): unknown => { try { return route.request().postDataJSON(); } catch { return route.request().postData(); } };

async function mockWorld(page: Page): Promise<World> {
  const world: World = { posts: [], adopted: false, runs: [] };
  await mockApi(page, { features: FEATURES, brokers: TOKEN });
  await mockDashboard(page, { state: "empty" });
  const me = { ...(F.me as Record<string, unknown>), role: "USER", scopes: ["trading:write", "alerts:write"] };
  await page.route(api("/auth/me$"), (r) => r.fulfill({ json: me }));
  await page.route(api("/system/features$"), (r) => r.fulfill({ json: FEATURES }));
  await page.route(api("/broker/token-status$"), (r) => r.fulfill({ json: TOKEN }));
  await page.route(api("/market-data/sources$"), (r) => r.fulfill({ json: SOURCES }));
  // Find setups (the screener)
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
      world.runs.push({ symbols: b.symbols });
      const matched = b.symbols.filter((_, i) => i % 9 === 4);
      return route.fulfill({ json: {
        run_id: 31, preview: false, text: b.source, base_tf: "1d", data_source: "lake:daily", scanned: b.symbols.length, matched,
        results: b.symbols.map((s) => ({ symbol: s, matched: matched.includes(s), reason: null })),
        funnel: { universe: b.symbols.length, with_data: b.symbols.length,
          stages: b.source.split(" AND ").map((text, i, all) => ({ text, survivors: i === all.length - 1 ? matched.length : Math.ceil(b.symbols.length / 2) })) },
        disclaimer: "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations.",
      } });
    }
    if (/\/screens$/.test(url)) {
      const b = body(route) as { name: string; source: string; base_tf: string };
      return route.fulfill({ status: 201, json: { id: 41, name: b.name, text: b.source, base_tf: b.base_tf } });
    }
    if (/\/screens\/41\/versions$/.test(url)) return route.fulfill({ json: { screen_id: 41, imported: true, max: 50, versions: [] } });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });

  // Signals: an empty inbox on day one, and the day-one card
  await page.route(/\/api(?:\/v1)?\/signals\/(feed|day-one)/, (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api(?:\/v1)?/, "");
    if (path === "/signals/feed") {
      return route.fulfill({ json: { tab: url.searchParams.get("tab") ?? "today", source: "all", symbol: null, items: [], next_cursor: null,
        counts: { waiting: 0, today: 0 }, as_of: "2026-10-23T04:50:00+00:00", since: null, sources: {} } });
    }
    const card = () => ({ offers: OFFERS, lists: DAY_LISTS, default_list: { kind: "system", key: "index-nifty50" }, can_adopt: true, max_watch: 60,
      adopted: world.adopted ? WATCH : null, outdated: false });
    if (path === "/signals/day-one" && request.method() === "GET") return route.fulfill({ json: card() });
    if (path === "/signals/day-one/adopt" && request.method() === "POST") {
      world.posts.push({ url: path, body: body(route) });
      world.adopted = true;
      return route.fulfill({ json: card() });
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
  for (const theme of ["light", "dark"] as const) {
    test.describe(`Day-one fix 9: Today ${size.width} ${theme}`, () => {
      test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

      test("the ready watch from Today; Today first in the sidebar", async ({ page }) => {
        const shot = async (step: string) => {
          if (SHOTS) await page.screenshot({ path: `${SHOTS}/${step}-${size.width}-${theme}.png`, fullPage: true, animations: "disabled" });
        };
        await page.addInitScript((t) => {
          try {
            localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
            localStorage.setItem("atp_install_dismissed", "1");
          } catch { /* storage unavailable */ }
        }, theme);
        await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
        await page.route(api("/signals/feed(\\?.*)?$"), (r) => r.fulfill({ json: { tab: "today", source: "all", symbol: null, items: [], next_cursor: null,
          counts: { waiting: 0, today: 0 }, as_of: "2026-10-23T04:50:00+00:00", since: null, sources: {} } }));
        await mockWorld(page);
        await page.goto("/");
        await settle(page);
        const block = page.locator("[data-block='signals']");
        await expect(block).toContainText("Signals show here as they come today.");      // TD-1: one line + the one button
        await expect(block.getByRole("link", { name: "Watch a list on Signals" })).toHaveAttribute("href", "/signals");
        if (!size.phone) {
          const nav = page.getByRole("navigation", { name: "Pages" }).getByRole("link");
          await expect(nav.first()).toHaveText("Today");
          const names = await nav.allInnerTexts();
          expect(names.indexOf("Broker")).toBe(names.indexOf("Settings") - 1);
        }
        await shot("01-today");
        await block.getByRole("link", { name: "Watch a list on Signals" }).click();
        await expect(page).toHaveURL(/\/signals$/);
        await expect(page.locator("[data-day-one='setup']")).toBeVisible();
      });
    });
  }
}
