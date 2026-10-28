import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockCharts } from "./chartsMocks";
import { expectComboValue } from "./combobox";
import { mockApi } from "./mockApi";
import { PRE_MARKET } from "../src/rhythm/fixtures";
import { screenerTool } from "./screenerTools";

/**
 * The day-one trader test (owner order 23 Oct, trader review: "a day-one trader test with screenshots (Today ->
 * watchlists -> a scan -> a Copilot question -> a strategy from an idea -> backtest -> adopt -> its signals) before
 * README row 19 is done"). ONE new PAPER user (role USER, nothing traded yet) walks the whole day in one test, at 390
 * and 1440, light and dark:
 *   1 Today (the dashboard)            2 Charts: the watchlist panel with the system groups
 *   3 Find setups: NIFTY 50, a ready gallery scan in one tap, its matches
 *   4 Copilot: a "Find setups" question, the answer lists the tools it used
 *   5 Ideas: an idea with "Try as a scan" and "Adopt as a paper strategy"; the adopt link opens the Paper bot form
 *   6 The next morning before his broker login: Find setups shows the login gate; Test a strategy runs on ATP's stored
 *     Daily history (the "Prices from ..." line)
 *   7 Adopt the idea's template as a PAPER bot whose every entry waits for his Approve (his trading default)
 *   8 Logged in again: Signals' day-one card - pick, see every setting, adopt on paper, his watch per symbol
 * Flags: every flag of the trader pack (read from backend/app/platform/trader_pack.py, so the two never drift) plus
 * `backtest_stored_history` - an owner decision flag that this test turns on ONLY in its mocked /api/system/features.
 * Data: every reply is made up. Public lists are names only (NIFTY 50 by name, src/screener/universe.ts); bars are a
 * generated walk, never read from a file; signal text uses structure words only. Calls the test does not mock answer
 * 404 / "not available", so an empty panel caused only by the mocks is not a finding.
 * No baseline is stored (as signals-day-one-v2 and copilot-tools-v2): with D1T_SHOTS=<folder> every step is saved
 * there as `<step>-<width>-<theme>.png`, and the developer words seen on each page go to `words-<width>-<theme>.json`.
 * Findings from walking it: docs/design/DAY_ONE_TRADER_TEST.md.
 */

const PACK = (() => {
  const src = readFileSync(new URL("../../backend/app/platform/trader_pack.py", import.meta.url), "utf8");
  const body = /PACK: Tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/.exec(src)?.[1] ?? "";
  return [...body.replace(/#.*$/gm, "").matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
})();
const FEATURES: Record<string, boolean> = { ...Object.fromEntries(PACK.map((f) => [f, true])), backtest_stored_history: true };

const SHOTS = process.env.D1T_SHOTS;
const NOW = "2026-10-23T10:20:00+05:30";
const S = JSON.parse(readFileSync(new URL("./fixtures/setup_v2.json", import.meta.url), "utf8")) as Record<string, unknown>;
const T = JSON.parse(readFileSync(new URL("./fixtures/copilot_tools_v2.json", import.meta.url), "utf8")) as { answers: Record<string, unknown> };
const CHAT = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown> };
const IDEAS = JSON.parse(readFileSync(new URL("./fixtures/ideas_v2.json", import.meta.url), "utf8")) as { ideas: { id: string }[] };
const registry = (JSON.parse(readFileSync(new URL("../src/screener/builderTexts.json", import.meta.url), "utf8")) as { registry: unknown }).registry;
const taxonomy = JSON.parse(readFileSync(new URL("../src/screener/taxonomySample.json", import.meta.url), "utf8")) as unknown;

// NIFTY 50 by name only: the app's own fallback list (src/screener/universe.ts), read as text (importing it pulls the API client).
const FIFTY = [...(/NIFTY50_FALLBACK: readonly string\[\] = \[([\s\S]*?)\];/.exec(
  readFileSync(new URL("../src/screener/universe.ts", import.meta.url), "utf8"))?.[1] ?? "").matchAll(/"([^"]+)"/g)].map((m) => m[1]);
const QUESTION = "Find stocks in a daily uptrend near a demand zone";
const STORED_LINE = "Prices from ATP's stored end-of-day history (NSE's daily files), 23 Oct 2025 to 22 Oct 2026.";
const STORED_NOTE = /Daily backtests use ATP's stored end-of-day history\. Intraday backtests need (your broker|today's login)\./;

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

// --- the broker: logged in today, or not yet (the next morning) ----------------------------------------------------
const TOKEN = (valid: boolean) => [{ broker_name: "upstox", account_label: "primary", token_status: valid ? "VALID" : "EXPIRED", token_expires_at: null,
  last_verified_at: null, needs_login: !valid, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const SOURCES = (valid: boolean) => ({ sources: [{ broker: "upstox", account_label: "primary", token_status: valid ? "VALID" : "EXPIRED", usable: valid }],
  usable: valid, timeframes: ["1m", "5m", "15m", "day"], max_symbols: 50, intraday_max_lookback_days: 30, daily_max_lookback_days: 730 });

// --- the strategy template the idea adopts (A5, as GET /strategy-templates lists it) ------------------------------
const TF = [{ value: "5m", label: "5-minute" }, { value: "15m", label: "15-minute" }, { value: "30m", label: "30-minute" },
  { value: "1h", label: "1-hour" }, { value: "1d", label: "Daily" }];
const A5_IDEA = "After a level breaks, wait for the market to come back to it from the other side and reject it: old resistance acting as support, or old support as resistance. A retest idea, never a breakout chase.";
const A5_PARAMS = [
  { key: "retest_of", label: "What broke", help: "The kind of level whose retest is read.", type: "enum", default: "swing",
    choices: [{ value: "swing", label: "A swing level" }, { value: "range", label: "A range edge" }, { value: "trendline", label: "A trendline" }, { value: "channel", label: "A channel edge" }] },
  { key: "range_mode", label: "The range", help: "For a range edge: the bars just before the break, or the consolidation found before it.", type: "enum", default: "auto",
    choices: [{ value: "fixed", label: "A set number of bars before the break" }, { value: "auto", label: "The consolidation before the break" }] },
  { key: "vol_mult", label: "Volume expansion", help: "For a range edge: the breaking bar's volume must be at least this many times the average (0 means no volume condition).",
    type: "float", default: 0, min: 0, max: 20, step: 0.1 },
  { key: "retest_max_bars", label: "Retest within (bars)", help: "The return must come within this many bars after the break; a later return is a new base, not a retest.",
    type: "int", default: 10, min: 1, max: 100, step: 1 },
];
const TEMPLATES = [
  { id: "A5", version: 1, name: "Structure flip retest", family: "A", family_name: "Price action", idea: A5_IDEA, difficulty: "Beginner", style: "Both",
    timeframes: TF, default_timeframe: "5m", sides_default: "both", params: A5_PARAMS, rules: [], available: true },
  { id: "B1", version: 1, name: "Range breakout", family: "B", family_name: "Breakout",
    idea: "A signal on the first close beyond a range or a consolidation by a set distance; the idea has ended when the close is back inside the range.",
    difficulty: "Beginner", style: "Both", timeframes: TF, default_timeframe: "5m", sides_default: "both", params: [], rules: [], available: true },
];
const LABEL: Record<string, string> = { "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1d": "day" };

// --- Signals day one (the three offers the server makes, his watch after adopting A5 on NIFTY 50) -----------------
const SETTING = (key: string, label: string, value: string, help: string) => ({ key, label, value, help });
const OFFERS = [
  { id: "A5", name: "Structure flip retest", idea: A5_IDEA, difficulty: "Beginner", family: "A", timeframe: "Daily", rules: [],
    settings: [SETTING("retest_of", "What broke", "A swing level", "The kind of level whose retest is read."),
      SETTING("retest_max_bars", "Retest within (bars)", "10", "The return must come within this many bars after the break.")] },
  { id: "B1", name: "Range breakout", idea: TEMPLATES[1].idea, difficulty: "Beginner", family: "B", timeframe: "Daily", rules: [], settings: [] },
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

/** Made-up Daily bars: a deterministic walk (never prices from a file). */
function walk(n: number): { timestamp: string; open: number; high: number; low: number; close: number; volume: number }[] {
  let p = 100;
  const start = Date.UTC(2025, 9, 23, 3, 45);
  return Array.from({ length: n }, (_, i) => {
    const o = p;
    p = Math.max(20, p * (1 + Math.sin(i / 7) * 0.012 + ((i * 37) % 11 - 5) / 900));
    const c = Math.round(p * 100) / 100;
    return { timestamp: new Date(start + Math.floor(i * 7 / 5) * 86_400_000).toISOString(), open: Math.round(o * 100) / 100,
      high: Math.round(Math.max(o, c) * 101) / 100, low: Math.round(Math.min(o, c) * 99) / 100, close: c, volume: 100_000 + (i % 9) * 5_000 };
  });
}

interface World {
  broker: boolean;
  posts: { url: string; body: unknown }[];
  adopted: boolean;
  runs: { symbols: string[] }[];
}

const body = (route: Route): unknown => { try { return route.request().postDataJSON(); } catch { return route.request().postData(); } };

async function mockWorld(page: Page): Promise<World> {
  const world: World = { broker: true, posts: [], adopted: false, runs: [] };
  await mockApi(page, { features: FEATURES, brokers: TOKEN(true) });
  await mockDashboard(page, { state: "empty" });
  await mockCharts(page, { watchlists: [], systemLists: SYSTEM });
  // A new account: no trades yet, nothing used of the day's risk, and the morning brief with his own sections still empty
  // (the server's own empty sentences, app/rhythm/build.py).
  await page.route(api("/analytics/summary(\\?|$)"), (r) => r.fulfill({ json: { total_trades: 0, closed_trades: 0, open_trades: 0, win_rate: 0, net_pnl: 0,
    gross_profit: 0, gross_loss: 0, profit_factor: null, avg_win: 0, avg_loss: 0, by_strategy: [], by_symbol: [] } }));
  await page.route(api("/paper/risk$"), (r) => r.fulfill({ json: { mode: "PAPER", today: { realised: 0, open_marks: 0, pnl: 0, worst: 0, limit: 10000,
    used: 0, entries: 0, max_entries: 10 }, lockout: null } }));
  const EMPTY_OWN: Record<string, string> = { setups: "No setups from your strategies or scans since the last session.", positions: "No open paper positions." };
  const brief = { ...PRE_MARKET, subtitle: "Fri 23 Oct · the market opens at 09:15", as_of: "2026-10-23T03:15:00Z",
    sections: PRE_MARKET.sections.map((sec) => (EMPTY_OWN[sec.id] ? { ...sec, lines: [], empty: EMPTY_OWN[sec.id] } : sec)) };
  await page.route(api("/rhythm/today"), (r) => r.fulfill({ json: { phase: "pre_market", kinds: ["pre_market", "mid_session", "closing", "weekly"], brief } }));
  const me = { ...(F.me as Record<string, unknown>), role: "USER", scopes: ["trading:write", "alerts:write"] };
  await page.route(api("/auth/me$"), (r) => r.fulfill({ json: me }));
  await page.route(api("/system/features$"), (r) => r.fulfill({ json: FEATURES }));
  await page.route(api("/broker/token-status$"), (r) => r.fulfill({ json: TOKEN(world.broker) }));
  await page.route(api("/market-data/sources$"), (r) => r.fulfill({ json: SOURCES(world.broker) }));
  await page.route(api("/broker/credentials$"), (r) => r.fulfill({ json: [{ broker_name: "upstox", account_label: "primary" }] }));
  await page.route(api("/broker/accounts$"), (r) => r.fulfill({ json: [] }));
  // His own trading defaults: every entry waits for his Approve (the approval-only path). Charts read the same route.
  const tradingDefaults = { instrument_kind: null, option_position: null, expiry_rule: null, strike_rule: null, strike_offset: null, premium_stop_pct: null,
    max_lots: null, option_strategy: null, spread_width: null, target_credit_pct: null, stop_credit_pct: null, entry_mode: "APPROVAL", exit_rules: null, ticket: null };
  await page.route(api("/me/prefs(/[a-z_]+)?(\\?.*)?$"), (r) => {
    if (r.request().method() !== "GET") return r.fulfill({ json: { namespace: "chart", value: body(r), version: 2 } });
    return r.fulfill({ json: { prefs: { chart: {}, trading_defaults: tradingDefaults }, versions: { chart: 1, trading_defaults: 1 } } });
  });

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
        run_id: 31, preview: false, text: b.source, base_tf: "1d", data_source: "broker:upstox", scanned: b.symbols.length, matched,
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

  // Copilot: his own AI key is connected; the answer is the backend's rule answer on its synthetic eval fixture
  await page.route(api("/ai/byok/connection$"), (r) => r.fulfill({ json: { connected: true, provider: "gemini", label: "Google Gemini", key_end: "x1y2",
    ok: true, error: null, tested_at: "2026-10-22T12:00:00Z", consent: null, may_train: false, paid_plan: false, free_tier_warning: null,
    limits: { month_inr: 0, monthly_cap_inr: 880, today_inr: 0, daily_cap_inr: 88, max_cap_inr: 44000, line: "AI this month: ₹0 of your ₹880 limit", reached: null },
    copy_tenant_key: null } }));
  await page.route(api("/ai/chat(/home)?$"), (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ json: { ...CHAT.home, ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null, code: null, own_key: true } } });
    }
    const message = (body(route) as { message: string }).message;
    return route.fulfill({ json: T.answers[message] ?? T.answers[QUESTION] });
  });

  // Ideas
  await page.route(api("/ideas(/[A-Za-z0-9]+)?$"), (route) => {
    const m = /\/ideas\/([A-Za-z0-9]+)$/.exec(route.request().url());
    if (!m) return route.fulfill({ json: IDEAS });
    const idea = IDEAS.ideas.find((i) => i.id === m[1]);
    return idea ? route.fulfill({ json: idea }) : route.fulfill({ status: 404, json: { detail: "no such idea" } });
  });

  // Strategies, the Test page and the Paper bot
  await page.route(api("/strategies$"), (r) => r.fulfill({ json: S.strategies }));
  await page.route(api("/custom-strategies$"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/strategy-templates$"), (r) => r.fulfill({ json: { templates: TEMPLATES } }));
  await page.route(api("/strategy-templates/\\w+/adopt$"), (route) => {
    const b = body(route) as { timeframe?: string; params?: Record<string, unknown> };
    const id = /strategy-templates\/(\w+)\/adopt/.exec(route.request().url())![1];
    const tf = b.timeframe ?? "5m";
    const sid = `template:${id}:1:${tf}:both:swing,,fixed,,1.5,,10,`;
    world.posts.push({ url: `/strategy-templates/${id}/adopt`, body: b });
    return route.fulfill({ json: { template_id: id, name: TEMPLATES.find((t) => t.id === id)!.name, strategy_id: sid,
      deployment: { strategy_id: sid, timeframe: LABEL[tf], holding: tf === "1d" ? "SWING" : "INTRADAY", mode: "PAPER" },
      settings: { timeframe: tf, sides: "both", params: b.params ?? {} },
      notes: ["Signals only: what to trade, how much and when to get out stay your own choices.", "Paper money only. Nothing starts until you confirm it."] } });
  });
  const bots: Record<string, unknown>[] = [];
  await page.route(api("/deployments(\\?.*)?$"), (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: bots });
    const b = body(route) as Record<string, unknown>;
    world.posts.push({ url: "/deployments", body: b });
    const bot = { id: 70, ...b, broker_name: "upstox", status: "ACTIVE", pause_reason: null, last_evaluated_at: null, last_signal_at: null, last_error: null,
      consecutive_failures: 0, open_positions: 0, created_by: 7, created_at: "2026-10-23T04:50:00Z", updated_at: "2026-10-23T04:50:00Z",
      option_position: null, expiry_rule: null, strike_rule: null, strike_offset: 0, premium_stop_pct: null, max_lots: null, contract_rules: "" };
    bots.push(bot);
    return route.fulfill({ json: bot });
  });
  await page.route(api("/worker/status$"), (r) => r.fulfill({ json: { running: true, market_open: true, market_status: "Open" } }));
  await page.route(api("/backtests$"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/backtest/bars$"), (route) => {
    const b = body(route) as { symbol: string; timeframe: string };
    world.posts.push({ url: "/backtest/bars", body: b });
    const candles = walk(250);
    return route.fulfill({ json: { symbol: b.symbol, timeframe: b.timeframe, source: "lake:daily", kind: "stored",
      words: "ATP's stored end-of-day history (NSE's daily files)", from_day: "23 Oct 2025", to_day: "22 Oct 2026", bars: candles.length,
      line: STORED_LINE, candles } });
  });
  await page.route(api("/backtest$"), (route) => {
    const b = body(route) as { strategy_id: string; symbol: string; data_source?: string };
    world.posts.push({ url: "/backtest", body: { strategy_id: b.strategy_id, symbol: b.symbol, data_source: b.data_source } });
    // Made-up figures on the made-up walk (the run's own trades and equity, as the engine returns them).
    const bars = walk(250);
    const trade = (i: number, pnl: number) => ({ symbol: b.symbol, strategy_id: b.strategy_id, direction: "LONG", entry_time: bars[i].timestamp,
      entry_price: bars[i].close, quantity: 10, stop_loss: bars[i].low, target1: bars[i].high, target2: null, exit_time: bars[i + 6].timestamp,
      exit_price: bars[i + 6].close, exit_reason: pnl >= 0 ? "TARGET1" : "STOP", pnl, charges: 12 });
    const trades = [trade(40, 180), trade(95, -120), trade(150, 240), trade(210, -90)];
    const equity_curve = trades.reduce<number[]>((acc, t) => [...acc, acc[acc.length - 1] + (t.pnl ?? 0)], [0]);
    return route.fulfill({ json: { ...(S.backtest as object), strategy_id: b.strategy_id, symbol: b.symbol, total_trades: 4, winning_trades: 2,
      losing_trades: 2, win_rate: 50, net_pnl: 210, gross_profit: 420, gross_loss: -210, profit_factor: 2, max_drawdown: 120, avg_win: 210, avg_loss: -105,
      expectancy: 52.5, trades, equity_curve, analytics: null, run_id: null } });
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

// Words a day-one trader should not meet outside "Advanced" (recorded per step, never a failure: the findings doc ranks them).
const DEV_WORDS = [/\b1d\b/, /\b1m\b/, /\b15m\b/, /\bBOS\b/, /\bCHoCH\b/, /\bPCR\b/, /\bOI\b/, /\bscreenql\b/i, /\w+\(\)/, /==/, /\btemplate:/,
  /\bstrategy_id\b/, /\bR:R\b/, /\bATR\b/, /\bEMA\b/, /\bVWAP\b/, /\bSupertrend\b/i, /\bMonte Carlo\b/, /\bwalk-forward\b/i, /\bJSON\b/i,
  /\bevent-driven\b/i, /\bNot found\b/, /\bundefined\b/, /\bnull\b/, /\bNaN\b/, /#\d+\b/];

async function settle(page: Page) {
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`Day-one trader ${size.width} ${theme}`, () => {
      test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

      test("Today -> watchlists -> a scan -> a Copilot question -> an idea -> backtest -> adopt on paper -> its signals", async ({ page }) => {
        test.setTimeout(180_000);
        const words: Record<string, string[]> = {};
        const shot = async (step: string) => {
          const text = await page.locator("main").innerText().catch(() => "");
          words[step] = DEV_WORDS.flatMap((re) => [...text.matchAll(new RegExp(re.source, re.flags.includes("g") ? re.flags : `${re.flags}g`))].map((m) => m[0]))
            .filter((w, i, all) => all.indexOf(w) === i);
          if (SHOTS) await page.screenshot({ path: `${SHOTS}/${step}-${size.width}-${theme}.png`, fullPage: true, animations: "disabled" });
        };
        await page.clock.install({ time: new Date(NOW) });
        await page.addInitScript((t) => {
          try {
            if (sessionStorage.getItem("e2e_seeded")) return;
            sessionStorage.setItem("e2e_seeded", "1");
            localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
            localStorage.setItem("atp_install_dismissed", "1");
          } catch { /* storage unavailable */ }
        }, theme);
        await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
        const world = await mockWorld(page);
        const go = async (path: string) => { await page.goto(path); await settle(page); };

        // 1 Today
        await go("/");
        await expect(page.locator("[data-page='dashboard-v2']")).toBeVisible();
        await shot("01-today");

        // 2 Charts: the watchlist panel with the system groups (the phone opens it as a sheet)
        await go("/charts");
        await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible();
        const panel = size.phone ? page.getByRole("dialog", { name: "Watchlist" }) : page.locator("main");
        if (size.phone) {
          await page.evaluate(() => window.scrollTo(0, 0));
          await page.getByRole("button", { name: "Watchlist", exact: true }).click();
          await expect(panel).toBeVisible();
        }
        await expect(panel.getByRole("button", { name: /^Choose a list\. Shown: / })).toContainText("NIFTY 50 stocks (50)");
        await expect(panel.getByText(/^Read-only list/)).toBeVisible();
        await shot("02a-charts-watchlist");
        await panel.getByRole("button", { name: /^Choose a list\. Shown: / }).click();
        await expect(panel.locator("[data-list-browser] [data-group] h3 button")).toHaveText([/Indices/, /Index stocks/, /Sectors/, /F&O/, /My lists/]);
        // The list on screen (NIFTY 50 stocks, day-one fix 8) sits in Index stocks, so that group is already open.
        await expect(panel.getByRole("button", { name: /^Index stocks/ })).toHaveAttribute("aria-expanded", "true");
        await expect(panel.getByRole("button", { name: /^NIFTY 50 stocks/ })).toBeVisible();
        await page.mouse.move(0, 0);
        await shot("02b-charts-list-groups");

        // 3 Find setups: NIFTY 50 on open, a ready gallery scan in one tap, its matches
        await go("/screener");
        await expectComboValue(page.getByTestId("universe-select"), "sys:index-nifty50");
        await expect(page.getByTestId("universe-count")).toHaveText("50 stocks · NIFTY 50");
        await shot("03a-find-setups");
        await screenerTool(page, "browse-screens");
        const library = page.getByRole("dialog", { name: "Screen library" });
        await expect(library.getByTestId("screen-try").first()).toBeVisible();
        await shot("03b-screen-gallery");
        await library.getByTestId("screen-try").first().click();
        await expect(library).toBeHidden();
        await expect.poll(() => world.runs.length).toBe(1);
        expect(world.runs[0].symbols).toEqual(FIFTY);
        await expect(page.getByRole("cell", { name: FIFTY[4], exact: true })).toBeVisible();
        await shot("03c-scan-matches");

        // 4 Copilot: a "Find setups" question; the answer says which tools it used
        await go("/copilot");
        await expect(page.locator("#copilot-question")).toBeVisible();
        await shot("04a-copilot");
        await page.locator("#copilot-question").fill(QUESTION);
        await page.getByRole("button", { name: "Ask", exact: true }).click();
        const answer = page.locator("[data-answer]").last();
        await expect(answer).toContainText("2 of 4 symbols in My swing list pass this screen");
        await answer.locator("[data-worked] summary").click();
        await expect(answer.locator("[data-tools-used]")).toHaveText("Tools used: Screen builder, Your watchlist, Screener");
        await answer.scrollIntoViewIfNeeded();
        await shot("04b-copilot-answer");

        // 5 Ideas: an idea with its scans and the adopt link; the adopt link opens the Paper bot form with the idea's settings
        await go("/ideas");
        const idea = page.locator("[data-idea='T5']");
        await idea.locator("summary").click();
        await expect(idea.locator("[data-scan='above']")).toBeVisible();
        await expect(idea.locator("[data-adopt='A5']")).toHaveText("Adopt as a paper strategy");
        await idea.scrollIntoViewIfNeeded();
        await shot("05a-idea");
        await idea.locator("[data-adopt='A5']").click();
        await expect(page).toHaveURL(/\/deployments\?template=A5/);
        await settle(page);
        const form = page.locator("[data-block='start']");
        await expect(form.getByLabel("Pick a strategy")).toHaveValue("template-pick:A5");
        await expect(form.locator("[data-template-notes]")).toContainText("Starts with the settings from the idea.");
        await form.scrollIntoViewIfNeeded();
        await shot("05b-idea-adopt-form");
        expect(world.posts.filter((p) => p.url === "/deployments")).toHaveLength(0);           // nothing starts from a link

        // 6 The next morning, before his broker login: Find setups keeps its Daily content under a one-tap login line (day-one
        // fix 2); Test a strategy runs on stored history
        world.broker = false;
        await go("/screener");
        await expect(page.locator("[data-stored-daily]")).toBeVisible();
        await shot("06a-find-setups-before-login");
        await go("/strategies/test");
        await expect(page.locator("[data-plain-data-source='stored']")).toContainText(STORED_NOTE);
        await shot("06b-test-a-strategy");
        await page.getByRole("combobox").first().selectOption({ label: "Swing EMA Pullback (daily)" });
        await page.getByRole("button", { name: "Run Backtest" }).click();
        await expect(page.getByTestId("prices-from")).toHaveText(STORED_LINE);
        expect(world.posts.find((p) => p.url === "/backtest")?.body).toEqual({ strategy_id: "swing_ema_pullback_d", symbol: "NIFTY", data_source: "lake:daily" });
        await page.getByTestId("prices-from").scrollIntoViewIfNeeded();
        await shot("06c-backtest-stored-history");

        // 7 Adopt the idea's template as a PAPER bot; every entry waits for his Approve (his own trading default)
        await go("/deployments?template=A5&timeframe=15m&set.retest_of=swing&set.retest_max_bars=10&set.vol_mult=1.5&set.range_mode=fixed");
        await expect(form.getByLabel("Pick a strategy")).toHaveValue("template-pick:A5");
        await form.getByLabel("Chart timeframe").selectOption("1d");
        await form.getByLabel("Pick a symbol").selectOption("RELIANCE");
        await expect(form.locator("[data-defaults-line]")).toContainText("each entry waits for your Approve");
        await form.scrollIntoViewIfNeeded();
        await shot("07a-adopt-on-paper");
        await form.getByRole("button", { name: "Start on paper" }).click();
        await expect(form).toContainText("Started on paper: RELIANCE.");
        const started = world.posts.find((p) => p.url === "/deployments")?.body as Record<string, unknown>;
        expect(started).toMatchObject({ symbol: "RELIANCE", mode: "PAPER", entry_mode: "APPROVAL", timeframe: "day", holding: "SWING" });
        expect(String(started.strategy_id)).toMatch(/^template:A5:/);
        await shot("07b-bot-started");

        // 8 Logged in again: Signals' day-one card, then his watch with each symbol's structure state
        world.broker = true;
        await go("/signals");
        // Day-one fix 7: with his paper bot on, Signals shows it first; watching a whole list is one tap away.
        await page.locator("[data-day-one-also-list]").click();
        const card = page.locator("[data-day-one='setup']");
        await expect(card.getByRole("heading", { name: "Pick a ready strategy and a list to watch" })).toBeVisible();
        await expect(card.getByLabel("List to watch")).toHaveValue("system:index-nifty50");
        await shot("08a-signals-day-one");
        await card.getByRole("radio", { name: /Structure flip retest/ }).check();
        await card.getByRole("button", { name: "See its settings" }).click();
        const confirm = page.locator("[data-day-one='confirm']");
        await expect(confirm.getByText("What broke", { exact: true })).toBeVisible();
        await expect(confirm.getByText("Paper money only. Nothing is placed: each setup waits for you.")).toBeVisible();
        await shot("08b-signals-settings");
        await confirm.getByRole("button", { name: "Adopt on paper and read my list" }).click();
        const watch = page.locator("[data-day-one='watch']");
        await expect(watch.getByRole("heading", { name: "Your watch: Structure flip retest" })).toBeVisible();
        expect(world.posts.find((p) => p.url === "/signals/day-one/adopt")?.body).toEqual({ template_id: "A5", list: { kind: "system", key: "index-nifty50" } });
        await expect(watch.getByText("1 new setup, 1 waiting for its trigger, 1 formed recently", { exact: false })).toBeVisible();
        await expect(watch.locator("[data-day-one-row='new']")).toContainText("retested from above; rejection formed");
        await shot("08c-signals-watch");
        await watch.getByRole("button", { name: /Show the other/ }).click();
        await expect(watch.locator("[data-day-one-row='watching']")).toHaveCount(46);
        await expect(watch.locator("[data-day-one-row='watching']").first()).toContainText(WATCHING);

        // Signal text and the public lists stay in structure words: no trade instructions anywhere on his watch.
        expect((await watch.innerText()).replace(/\s+/g, " ")).not.toMatch(/\b(buy|sell|target|stop-loss|stoploss)\b/i);
        if (SHOTS) writeFileSync(`${SHOTS}/words-${size.width}-${theme}.json`, `${JSON.stringify(words, null, 2)}\n`);
      });
    });
  }
}
