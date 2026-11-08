import type { Page, Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "../dashboardMocks";
import { mockApi } from "../mockApi";

/**
 * M92 V2: the made-up world the tutorial videos are recorded in. The real app (the production build) runs against
 * page.route() answers only - no backend, no account, no server address, no token, no email on screen.
 *
 * Every list is a generic one: the members are STOCK01 ... STOCK60, never a real company, and no screen in the videos
 * puts a price next to a name. The list NAMES are the app's own group labels (an index's name is a list label, not a
 * stock). Counts, times and matches are made up and say nothing about any market.
 */

const PACK = (() => {
  const src = readFileSync(new URL("../../../backend/app/platform/trader_pack.py", import.meta.url), "utf8");
  const body = /PACK: Tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/.exec(src)?.[1] ?? "";
  return [...body.replace(/#.*$/gm, "").matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
})();
/** The trader pack plus the screener's own parts the videos show (the guide's section 14 lists the switches). */
export const FEATURES: Record<string, boolean> = {
  ...Object.fromEntries(PACK.map((f) => [f, true])),
  ui_v2: true, screener_v2: true, backtest_stored_history: true,
};

const registry = (JSON.parse(readFileSync(new URL("../../src/screener/builderTexts.json", import.meta.url), "utf8")) as { registry: unknown }).registry;
const taxonomy = JSON.parse(readFileSync(new URL("../../src/screener/taxonomySample.json", import.meta.url), "utf8")) as unknown;

/** STOCK01 ... STOCKnn - generic symbols only. */
export const generic = (n: number, from = 1) => Array.from({ length: n }, (_, i) => `STOCK${String(from + i).padStart(2, "0")}`);
const named = (symbols: string[], exchange = "NSE") => symbols.map((symbol) => ({ symbol, exchange, name: symbol }));
const sysList = (key: string, group: string, label: string, name: string, items: { symbol: string; exchange: string; name: string }[]) =>
  ({ key, group, group_label: label, name, count: items.length, read_only: true, refreshed_at: "2026-10-22T13:00:00Z", items });

const FIFTY = generic(50);
const SYS_LISTS = [
  sysList("index-nifty50", "INDEX_STOCKS", "Index stocks", "NIFTY 50 stocks (50)", named(FIFTY)),
  sysList("index-nifty500", "INDEX_STOCKS", "Index stocks", "NIFTY 500 stocks (500)", named(generic(500))),
  sysList("sector-banking", "SECTORS", "Sectors", "Banking (12)", named(generic(12, 51))),
  sysList("sector-it", "SECTORS", "Sectors", "IT (10)", named(generic(10, 63))),
  sysList("fno-stocks", "FNO", "F&O", "F&O stocks (40)", named(generic(40, 11))),
];
const SYSTEM = {
  groups: ["INDEX_STOCKS", "SECTORS", "FNO"].map((g) => {
    const lists = SYS_LISTS.filter((l) => l.group === g);
    return { group: g, label: lists[0].group_label, lists: lists.map(({ items, ...summary }) => ({ ...summary, count: items.length })) };
  }),
  lists: Object.fromEntries(SYS_LISTS.map((l) => [l.key, l])),
};
/**
 * The batch plan. `store_timeframes` is the shipped M90 shape (PR #953): on a timeframe the store serves, the whole
 * list goes in one request. Before M90 is live the page ignores the field and runs `size` at a time.
 */
const UNIVERSES = {
  default_key: "index-nifty50",
  groups: SYSTEM.groups.map((g) => ({ group: g.group, label: g.label, lists: g.lists.map((l) => ({ key: l.key, name: l.name, count: l.count })) })),
  batch: { size: 40, per_minute: 100, per_second: 4, max_symbols: 500, store_timeframes: ["1d", "1w", "1M"] },
};

/** No broker connected: the M86 end-of-day path (Daily / Weekly / Monthly from the stored daily files). */
const NO_BROKER_SOURCES = { sources: [], usable: false, eod: true, timeframes: ["day", "week", "month"], max_symbols: 500,
  intraday_max_lookback_days: 0, daily_max_lookback_days: 730 };
const TOKEN = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const BROKER_SOURCES = { sources: [{ broker: "upstox", account_label: "primary", token_status: "VALID", usable: true }], usable: true,
  timeframes: ["1m", "5m", "15m", "day"], max_symbols: 500, intraday_max_lookback_days: 30, daily_max_lookback_days: 730 };

export interface VideoWorld { runs: { symbols: string[]; source: string }[]; unmocked: string[] }
export interface WorldOptions { signedOut?: boolean; broker?: boolean; theme?: "light" | "dark"; time?: string }

const body = (route: Route): unknown => { try { return route.request().postDataJSON(); } catch { return route.request().postData(); } };

/** Sets up the made-up world before the first page.goto(). */
export async function videoWorld(page: Page, opts: WorldOptions = {}): Promise<VideoWorld> {
  const world: VideoWorld = { runs: [], unmocked: [] };
  await page.clock.install({ time: new Date(opts.time ?? "2026-10-23T16:05:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["screener"]));
    } catch { /* storage unavailable */ }
  }, opts.theme ?? "light");
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  // Registered first, so it answers last: any call no mock below knows gets a plain 404 (never the preview's proxy to a
  // backend that is not there). The calls are listed, so a scene that needs one can be given an answer.
  await page.route(/\/api\//, (r) => { world.unmocked.push(new URL(r.request().url()).pathname); return r.fulfill({ status: 404, json: { detail: "Not found" } }); });
  // Any API answer of 400 or more is listed too (a mock that answers "off" or "not found" shows an error on screen).
  page.on("response", (r) => { if (r.status() >= 400 && /\/api\//.test(r.url())) world.unmocked.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  await mockApi(page, { features: FEATURES, ...(opts.broker ? { brokers: TOKEN } : {}) });
  await mockDashboard(page, { state: opts.broker ? "connected" : "empty" });
  // No email, no id on screen: the account is a plain USER with a blank address.
  const me = { ...(F.me as Record<string, unknown>), email: "", role: "USER", scopes: ["trading:write", "alerts:write"] };
  await page.route(api("/auth/me$"), (r) => r.fulfill(opts.signedOut ? { status: 401, json: { detail: "Not authenticated" } } : { json: me }));
  if (opts.signedOut) await page.route(api("/auth/refresh$"), (r) => r.fulfill({ status: 401, json: { detail: "Not authenticated" } }));
  await page.route(api("/system/features$"), (r) => r.fulfill({ json: FEATURES }));
  await page.route(api("/broker/token-status$"), (r) => r.fulfill(opts.broker ? { json: TOKEN } : { json: [] }));
  await page.route(api("/market-data/sources$"), (r) => r.fulfill({ json: opts.broker ? BROKER_SOURCES : NO_BROKER_SOURCES }));
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
      if (!b.preview) world.runs.push({ symbols: b.symbols, source: b.source });
      // A short pause per request, so the progress line ("120 / 500 done") is on screen long enough to be recorded.
      if (!b.preview) await new Promise((r) => setTimeout(r, b.symbols.length > 50 ? 900 : 300));
      const stages = b.source.split(" AND ");
      // A scan of three or more stages: the third removes every symbol (scene 16 shows that note and what to do).
      const emptied = stages.length >= 3 ? 2 : -1;
      const matched = emptied >= 0 ? [] : b.symbols.filter((_, i) => i % 9 === 4);
      return route.fulfill({ json: {
        run_id: 31, preview: !!b.preview, text: b.source, base_tf: "1d", data_source: opts.broker ? "broker:upstox" : "lake:eod",
        scanned: b.symbols.length, matched,
        results: b.symbols.map((s) => ({ symbol: s, matched: matched.includes(s), reason: null, evidence_id: matched.includes(s) ? 11 : null })),
        funnel: { universe: b.symbols.length, with_data: b.symbols.length,
          stages: stages.map((text, i) => ({ text,
            survivors: emptied >= 0 && i >= emptied ? 0 : i === stages.length - 1 ? matched.length : Math.ceil(b.symbols.length / (2 + i)) })) },
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
  // My watchlists: none yet (the list picker then shows the system lists and "Type my own symbols").
  await page.route(api("/watchlists$"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/broker/connections$"), (r) => r.fulfill({ json: [] }));
  // "Tell me when it matches": the alert rule is stored (made up), and listed under "Alerts on this scan".
  const rules: Record<string, unknown>[] = [];
  await page.route(/\/api(\/v1)?\/alerts\/rules$/, async (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: rules });
    const b = route.request().postDataJSON() as Record<string, unknown>;
    const rule = { id: 7, name: b.name, kind: "screen", screen_id: b.screen_id, base_tf: "1d", status: "active", symbols: b.symbols, fire_on: b.fire_on };
    rules.push(rule);
    return route.fulfill({ status: 201, json: rule });
  });
  await page.route(/\/api(?:\/v1)?\/signals\/(feed|day-one)/, (route) => route.fulfill({ json: { items: [], offers: [], lists: [], adopted: null,
    tab: "today", counts: { waiting: 0, today: 0 }, next_cursor: null, sources: {} } }));
  return world;
}

/** Fixed fonts, no caret, no busy marks: the frame is the same on every machine. */
export async function settle(page: Page): Promise<void> {
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'Inter', 'Lohit Marathi', 'FreeSans', sans-serif !important; caret-color: transparent !important; }"
    // The Copilot chat bubble is not part of these lessons; on a phone it sits over the bottom bar's Run button.
    + " [data-chat-bubble]{display:none !important}" });
  await page.waitForLoadState("networkidle");
  await page.locator("[aria-busy='true']").first().waitFor({ state: "detached", timeout: 15_000 }).catch(() => undefined);
}
