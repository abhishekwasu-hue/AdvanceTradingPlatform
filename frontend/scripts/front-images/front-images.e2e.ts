import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { REPORT } from "../../src/dailyReport/fixture";
import { DEMO_LIST, DEMO_STATS, DEMO_WEEK } from "../../src/journal/journal.fixture";
import { api, mockDashboard } from "../../e2e/dashboardMocks";
import { mockApi, type MockOptions } from "../../e2e/mockApi";

/**
 * M95 (docs/design/M95_FRONT_DOOR.md, rule M95-Q1): the front door's six benefit images, each a REAL app screen (the
 * production build on a mocked API, as the e2e specs do) at a phone size, 360 x 450 at 2x (720 x 900), light theme.
 * Public images: index data only (NIFTY 50 / BANK NIFTY), as of a date at least three months old, and EVERY price
 * figure and price axis hidden (CSS added to the capture page only; the app is unchanged). No stock names, no amounts.
 * - FRONT_BARS=<json from prepare_bars.py> (required): the real index daily bars, kept outside the repository, and the
 *   Daily / Weekly structure the app's own reader (the daily report's) gives on them. No structure word comes from here.
 * - Writes raw PNGs and each screen's visible words to FRONT_RAW (default /tmp/front-raw); shrink.py makes the
 *   committed files public/front/<slug>.png.
 */
const RAW = process.env.FRONT_RAW ?? "/tmp/front-raw";
mkdirSync(RAW, { recursive: true });
type Bar = { timestamp: string; open: number; high: number; low: number; close: number; volume: number };
if (!process.env.FRONT_BARS) throw new Error("FRONT_BARS=<the file prepare_bars.py wrote> is required");
const STORE = JSON.parse(readFileSync(process.env.FRONT_BARS, "utf8")) as {
  asof: string; symbols: Record<string, Bar[]>; structure: Record<string, { d: string; w: string }> };
/** The as-of day every screen shows: the stored bars' last day. */
const ASOF = STORE.asof;
const AT = `${ASOF}T15:45:00+05:30`;
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

const key = (symbol: string) => (/BANK/.test(symbol) ? "NIFTY BANK" : "NIFTY 50");
const bars = (symbol: string): Bar[] => STORE.symbols[key(symbol)];
const last = (symbol: string) => bars(symbol).at(-1)!;
const prev = (symbol: string) => bars(symbol).at(-2)!;
const changePct = (symbol: string) => (last(symbol).close / prev(symbol).close - 1) * 100;
/** The app reader's direction ("up", "up (weak)", "down", "down (weak)", "range", "none"), from prepare_bars.py. */
const dailyWord = (symbol: string) => STORE.structure[key(symbol)].d;
const weeklyWord = (symbol: string) => STORE.structure[key(symbol)].w;

/** The capture page's own CSS: one font, no caret, no scroll bars, no animation. */
const BASE_CSS = [
  "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; transition: none !important; animation: none !important; }",
  "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  // A crop of the screen: the bottom bar's room goes to the screen itself.
  "[data-bottom-nav-v2], [data-chat-bubble] { display: none !important; }",
].join("\n");

async function prepare(page: Page, features: Record<string, boolean>, extra: Partial<MockOptions> = {}, dismissed: string[] = [], at = AT) {
  await page.clock.install({ time: new Date(at) });
  await page.addInitScript((d) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_install_domain_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(d));
    } catch { /* storage unavailable */ }
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
  }, dismissed);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features, brokers: CONNECTED, ...extra });
}

/** The visible words inside the viewport (hidden figures excluded), for the checks and the report. */
async function visibleText(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      const text = n.textContent?.trim();
      const el = n.parentElement;
      if (!text || !el) continue;
      const st = getComputedStyle(el);
      if (st.visibility === "hidden" || st.display === "none") continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      const r = range.getBoundingClientRect();
      if (r.width === 0 || r.height === 0 || r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) continue;
      let o: Element | null = el;
      let shown = true;
      while (o) { if (Number(getComputedStyle(o).opacity) === 0) { shown = false; break; } o = o.parentElement; }
      if (shown) out.push(text);
    }
    return out;
  });
}

/** Scrolls the page so the element's top sits `gap` px below the screen's top. */
async function toTop(page: Page, selector: string, gap = 8) {
  // Twice: a top bar that folds away on scroll moves the element after the first jump.
  for (let i = 0; i < 2; i++) {
    await page.locator(selector).first().evaluate((el, g) => { document.scrollingElement!.scrollTop += el.getBoundingClientRect().top - g; }, gap);
    await page.waitForTimeout(300);
  }
}

async function capture(page: Page, slug: string, hideCss: string, place?: () => Promise<void>) {
  await page.addStyleTag({ content: BASE_CSS + "\n" + hideCss });
  if (place) await place();
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(400);
  const words = await visibleText(page);
  writeFileSync(join(RAW, `${slug}.txt`), words.join("\n") + "\n");
  // M95-Q1 checks on the words on screen (the chart canvases are checked by eye): no amount, no price-like figure
  // (a thousands separator or two decimals that is not a %), no other name.
  const text = words.join(" ");
  expect(text, "no rupee amount").not.toMatch(/₹|\bRs\.?\s?\d/);
  expect(text, "no price-like figure").not.toMatch(/\d,\d{3}|\d\.\d{2}\b(?!%)/);
  expect(text, "only the two indices").not.toMatch(/STOCK\d|DEMO[A-D]\b|TEST[A-D]{3}|RELIANCE|SENSEX|VIX|FINNIFTY/i);
  await page.screenshot({ path: join(RAW, `${slug}.png`), animations: "disabled" });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "no sideways scroll").toBeLessThanOrEqual(0);
}

/** The Copilot strip and candles: the two indices only, from the stored bars (the e2e mock adds SENSEX / VIX / FII). */
async function mockIndexNumbers(page: Page, keep = 60) {
  const row = (key: string, label: string) => ({ key, label, symbol: key, exchange: "NSE", ltp: last(key).close, prev_close: prev(key).close,
    change: last(key).close - prev(key).close, change_pct: changePct(key), as_of: `${ASOF}T10:00:00Z`, stale: false, source: "close", missing: false });
  await page.route(/\/api(?:\/v1)?\/market\/strip(\?.*)?$/, (r) => r.fulfill({ json: {
    indices: [row("NIFTY 50", "NIFTY 50"), row("NIFTY BANK", "BANK NIFTY")], source: "close", broker: { broker: "upstox", account_label: "primary" },
    market: { exchange: "NSE", phase: "closed", is_open: false, opens_at: "09:15", closes_at: "15:30", next_open: null, holiday: null, reason: null },
    refresh_seconds: 15, fetched_at: `${ASOF}T10:00:00Z` } }));
  await page.route(/\/api(?:\/v1)?\/market-data\/candles$/, (r) => {
    const body = r.request().postDataJSON() as { symbols: string[]; timeframe: string; lookback_days: number; before?: string | null };
    const symbol = body.symbols[0];
    const list = body.before ? [] : bars(symbol).slice(-keep);
    return r.fulfill({ json: { source: { broker: "upstox", account_label: "primary" }, exchange: "NSE", timeframe: body.timeframe, base_interval: "1d",
      lookback_days: body.lookback_days, before: body.before ?? null, fetched_at: `${ASOF}T10:00:00Z`, warnings: [], note: "",
      symbols: { [symbol]: { bars: list, count: list.length, first: list[0]?.timestamp ?? null, last: list.at(-1)?.timestamp ?? null, error: null, volume_source: null } } } });
  });
}

/** Hides a lightweight-charts price axis (the right / left scale cells) and any price label on a chart. */
const HIDE_CHART_PRICES = [
  ".tv-lightweight-charts table tr td:first-child:not(:only-child), .tv-lightweight-charts table tr td:last-child:not(:only-child) { visibility: hidden !important; }",
  // The first-use hint and the floating view buttons cover the bars in a still picture.
  "[data-chart-hint], [data-chart-view-controls] { display: none !important; }",
].join("\n");

const CHAT = JSON.parse(readFileSync(new URL("../../e2e/fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as {
  home: Record<string, unknown>; answers: Record<string, Record<string, unknown>>;
};

test("structure words (for the report)", () => {
  console.log(`as of ${ASOF}: ` + ["NIFTY 50", "NIFTY BANK"].map((s) => `${s} daily ${dailyWord(s)} weekly ${weeklyWord(s)} change ${changePct(s).toFixed(2)}%`).join("; "));
});

test("copilot", async ({ page }) => {
  await prepare(page, { ui_v2: true, ai_copilot: true });
  await mockIndexNumbers(page);
  const fixture = CHAT.answers["Read the NIFTY chart"];
  const stamp = `${ASOF}T10:00:00+00:00`;
  // The Copilot's own words for the reader's direction (its trend chip knows up / down / sideways).
  const word = (w: string) => (w.startsWith("up") ? "up" : w.startsWith("down") ? "down" : "sideways");
  const daily = word(dailyWord("NIFTY 50"));
  const wk = word(weeklyWord("NIFTY 50"));
  const read = `On the daily chart the structure reads ${daily}; the weekly reads ${wk}.`;
  // The chart reader's answer in structure words only (the fixture's shape; its sentences carry no figure here).
  const answer = { ...fixture,
    summary: [read],
    answer: read,
    sections: [{ title: "What to watch", lines: ["Whether the protected level holds or breaks on a closed bar.", "How the price behaves when it reaches the nearest area."] }],
    blocks: [], symbols: ["NIFTY 50"],
    worked: (fixture.worked as Record<string, unknown>[]).map((w) => ({ ...w, as_of: stamp, numbers: [] })),
    headline: { ...(fixture.headline as Record<string, unknown>), symbol: "NIFTY 50", price: last("NIFTY 50").close, as_of: stamp, trend: daily, area: null,
      chart: { symbol: "NIFTY 50", exchange: "NSE", timeframe: "day" } } };
  const home = { ...CHAT.home, market_line: { line: `NIFTY 50: by its swing highs and lows the daily trend is ${daily}, the weekly ${wk}.`, reason: null, as_of: stamp,
    fii_net_crore: null, fii_as_of: null } };
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, async (route) => {
    const request = route.request();
    if (request.url().endsWith("/history")) return route.fulfill({ json: { turns: [], keep: 20 } });
    if (request.method() === "GET") return route.fulfill({ json: home });
    return route.fulfill({ json: answer });
  });
  await page.goto("/copilot");
  await page.waitForLoadState("networkidle");
  const box = page.getByRole("textbox", { name: "Your question" });
  await box.fill("Read the NIFTY 50 chart");
  await box.press("Enter");
  await expect(page.locator("[data-answer='chart']")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("[data-mini-chart] canvas").first()).toBeVisible({ timeout: 15_000 });
  await page.waitForLoadState("networkidle");
  await capture(page, "copilot", [HIDE_CHART_PRICES,
    "[data-headline-price], [data-headline-live], [data-headline] > div:has([data-headline-area]) { display: none !important; }",
    // The question box and what follows the mini chart in the card (the action row, the folded details): the chart
    // answer then ends with its chart in the phone crop.
    "[data-composer='bottom'], [data-answer='chart'] [data-mini-chart] ~ * { display: none !important; }"].join("\n"), async () => {
    await toTop(page, "[data-answer='chart']");
  });
});

const CHAIN = JSON.parse(readFileSync(new URL("../../e2e/fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown> & { rows: { strike: number }[]; summary: Record<string, unknown> };
};

test("option-chain", async ({ page }) => {
  await prepare(page, { ui_v2: true }, {}, ["option-chain"]);
  // The e2e chain (made-up OI, round numbers), its expiries moved to the as-of week and its strikes around the stored
  // close; every strike and premium is hidden below, so only OI (lakhs), IV % and the counts show.
  const day = new Date(`${ASOF}T00:00:00Z`);
  const tuesday = (weeks: number) => { const d = new Date(day); d.setUTCDate(d.getUTCDate() + ((2 - d.getUTCDay() + 7) % 7 || 7) + 7 * weeks); return d.toISOString().slice(0, 10); };
  const expiries = [0, 1, 2, 3].map((w, i) => ({ date: tuesday(w), kind: i === 3 ? "monthly" : "weekly" }));
  const shift = Math.round(last("NIFTY 50").close / 50) * 50 - (CHAIN.live.summary.atm_strike as number);
  const s = CHAIN.live.summary;
  const live = { ...CHAIN.live, expiry: expiries[0].date, fetched_at: `${ASOF}T10:00:00+00:00`, market_open: false,
    rows: CHAIN.live.rows.map((r) => ({ ...r, strike: r.strike + shift })),
    summary: { ...s, spot: last("NIFTY 50").close, atm_strike: (s.atm_strike as number) + shift, max_pain: (s.max_pain as number) + shift,
      max_call_oi_strike: (s.max_call_oi_strike as number) + shift, max_put_oi_strike: (s.max_put_oi_strike as number) + shift, days_to_expiry: 4,
      futures: { ...(s.futures as object), expiry: expiries[3].date, symbol: "NIFTY FUT", ltp: last("NIFTY 50").close + 50 } } };
  const sessions = { NSE: { is_open: false, reason: "closed", next_open: null } };
  await page.route(/\/api(?:\/v1)?\/system\/status$/, (r) => r.fulfill({ json: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions } }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/expiries")) return route.fulfill({ json: { ...CHAIN.expiries, underlying: "NIFTY", today: ASOF, default: expiries[0].date, expiries } });
    return route.fulfill({ json: live });
  });
  await page.route(/\/api(?:\/v1)?\/option-chain\/analyses/, (r) => r.fulfill({ status: 404, json: { detail: "Not found" } }));
  await page.route(/\/api(?:\/v1)?\/me\/prefs$/, (r) => r.fulfill({ json: {
    prefs: { chart: {}, setup: {}, oc_analyses: { items: {} }, appearance: { text_size: "normal" } }, versions: { chart: 0, setup: 0, oc_analyses: 0, appearance: 0 } } }));
  await page.goto("/option-chain");
  await expect(page.locator("tr[data-atm]")).toBeVisible({ timeout: 15_000 });
  await page.waitForLoadState("networkidle");
  // No real per-strike OI is stored for the as-of day, so every fixture value is hidden: the whole summary (prices, and
  // the made-up PCR / IV), the strike and premium columns, every OI figure, its bar and the highest-OI mark. Shown: the
  // chain's header, its Calls / Puts OI column titles and the ATM row.
  await capture(page, "option-chain", [
    "[data-block='chain-summary'] { display: none !important; }",
    "[data-chain-scroll] td[data-col$='-oi'] > span { visibility: hidden !important; }",
    "[data-chain-scroll] td[data-max-oi] { box-shadow: none !important; }",
    "[data-chain-scroll] [data-col$='-ltp'], [data-chain-scroll] tr > th[scope='row'], [data-chain-scroll] th[data-front-hide] { display: none !important; }",
  ].join("\n"), async () => {
    // The header cells of the hidden columns (they carry no data attribute of their own): marked here, hidden above.
    await page.locator("[data-chain-scroll] thead th").evaluateAll((els) => els.forEach((e) => {
      if (/LTP|Strike/.test(e.textContent ?? "")) e.setAttribute("data-front-hide", "");
    }));
    await toTop(page, "[data-chain-notes]");
    // The chain's own scroll box ends inside the crop (its rows scroll there, as on a phone), not cut by the crop's edge.
    await page.locator("[data-chain-scroll]").evaluate((el) => {
      const box = el as HTMLElement;
      box.style.maxHeight = `${window.innerHeight - box.getBoundingClientRect().top - 8}px`;
    });
  });
});

const READ = (path: string): unknown => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));

test("find-setups", async ({ page }) => {
  await prepare(page, { ui_v2: true, screener_v2: true, screener_gallery: true, screener_copilot: true }, {}, ["screener", "scanner"]);
  const registry = (READ("../../src/screener/builderTexts.json") as { registry: unknown }).registry;
  const taxonomy = READ("../../src/screener/taxonomySample.json");
  // The list is named by its index only; its members are placeholders that no picture shows (no scan is run).
  const members = Array.from({ length: 50 }, (_, i) => `STOCK${String(i + 1).padStart(2, "0")}`);
  const universes = { default_key: "index-nifty50", groups: [{ group: "INDEX_STOCKS", label: "Index stocks", lists: [{ key: "index-nifty50", name: "NIFTY 50 stocks (50)", count: 50 }] }],
    batch: { size: 40, per_minute: 100, per_second: 4, max_symbols: 500 } };
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/taxonomy$/.test(url)) return route.fulfill({ json: taxonomy });
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
    if (/\/universes$/.test(url)) return route.fulfill({ json: universes });
    if (/\/universes\/index-nifty50$/.test(url)) return route.fulfill({ json: { key: "index-nifty50", name: "NIFTY 50", symbols: members, truncated: false, source_count: 50, exchange: "NSE" } });
    return route.fulfill({ status: 503, headers: { "X-Feature-Disabled": "screener_legends" }, json: { detail: "disabled" } });
  });
  await page.goto("/screener");
  await expect(page.getByTestId("start-scan")).toBeVisible({ timeout: 15_000 });
  await page.waitForLoadState("networkidle");
  // Hidden: each ready scan's formula line (its name and Run stay) and the phone's run bar (it floats where the hidden bottom bar was; nothing is picked yet).
  await capture(page, "find-setups", "[data-testid='ready-scans'] li .line-clamp-1, [data-testid='screener-bottom-bar'] { display: none !important; }", () => toTop(page, "[data-testid='screener-hero']"));
});

test("trading-bot", async ({ page }) => {
  const D = READ("../../e2e/fixtures/dashboard_v2.json") as Record<string, unknown>;
  const S = READ("../../e2e/fixtures/setup_v2.json") as Record<string, unknown>;
  // The as-of day during the session (the bots run then).
  await prepare(page, D.features as Record<string, boolean>, { brokers: D.token_status as unknown[] }, [], `${ASOF}T11:20:00+05:30`);
  await mockDashboard(page, { me: true });
  const stamp = (hm: string) => `${ASOF}T${hm}:00+05:30`;
  const base = { exchange: "NSE", broker_name: null, last_signal_at: null, last_error: null, consecutive_failures: 0, created_by: 7,
    created_at: stamp("09:00"), updated_at: stamp("09:00"), strike_offset: 0, premium_stop_pct: null, contract_rules: "", mode: "PAPER",
    instrument_kind: "OPTION", option_position: "BUY", expiry_rule: "NEAREST", strike_rule: "ATM", max_lots: 1, holding: "INTRADAY", pause_reason: null };
  // Two running paper bots on the two indices: states and words only (no position count; the signal time is hidden).
  const bots = [
    { ...base, id: 52, strategy_id: "orb_15m_5m", symbol: "NIFTY 50", timeframe: "1min", status: "ACTIVE", last_evaluated_at: stamp("11:19"), open_positions: 0 },
    { ...base, id: 51, strategy_id: "vwap_supertrend_5m", symbol: "NIFTY BANK", timeframe: "1min", status: "ACTIVE", last_evaluated_at: stamp("11:19"), open_positions: 0 },
  ];
  await page.route(api("/strategies$"), (route) => route.fulfill({ json: S.strategies }));
  await page.route(api("/custom-strategies$"), (route) => route.fulfill({ json: [] }));
  await page.route(api("/broker/credentials$"), (route) => route.fulfill({ json: [{ broker_name: "upstox", account_label: "primary" }] }));
  await page.route(api("/broker/accounts$"), (route) => route.fulfill({ json: [] }));
  await page.route(api("/deployments(\\?.*)?$"), (route) => route.fulfill({ json: bots }));
  await page.route(api("/worker/status$"), (route) => route.fulfill({ json: { running: true, market_open: true, market_status: "Open" } }));
  await page.goto("/deployments");
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  await capture(page, "trading-bot", "[data-bot-signal] { display: none !important; }", () => toTop(page, "[data-block='bots']"));
});

test("journal", async ({ page }) => {
  await prepare(page, { ui_v2: true, discipline_engine: true, phase0_paper_only: true }, { brokers: [] }, ["journal"]);
  // The e2e journal answers moved to the two indices and the as-of week. The picture is the Week tab's discipline score
  // (scores and counts, no amount); the list behind the Trades tab is mocked only because the page reads it first.
  const day = (d: number, hm: string) => { const t = new Date(`${ASOF}T${hm}:00+05:30`); t.setUTCDate(t.getUTCDate() - d); return t.toISOString(); };
  const list = { ...DEMO_LIST, setups: ["Buyer area", "Seller area"], tags: ["late entry"],
    entries: DEMO_LIST.entries.map((e, i) => ({ ...e, symbol: i % 2 ? "NIFTY BANK" : "NIFTY 50", source: "ATP", source_label: null, outside_atp: false,
      kind: "trade", mode: "PAPER", entry_time: day(i, "10:05"), exit_time: e.exit_time ? day(i, "13:40") : null, tags: [], user_tags: i === 2 ? ["late entry"] : [] })) };
  await page.route(/\/api(?:\/v1)?\/journal\/entries(\?.*)?$/, (r) => r.fulfill({ json: list }));
  await page.route(/\/api(?:\/v1)?\/journal\/stats(\?.*)?$/, (r) => r.fulfill({ json: DEMO_STATS }));
  const asof = new Date(`${ASOF}T00:00:00Z`);
  const back = (asof.getUTCDay() + 6) % 7;                                  // days since Monday
  const thursday = new Date(asof); thursday.setUTCDate(asof.getUTCDate() - back + 3);
  const isoWeek = Math.ceil(((thursday.getTime() - Date.UTC(thursday.getUTCFullYear(), 0, 1)) / 86_400_000 + 1) / 7);
  const week = { ...DEMO_WEEK, week: `${thursday.getUTCFullYear()}-W${String(isoWeek).padStart(2, "0")}`, start: day(back, "00:00"), end: day(back - 7, "00:00"),
    entries: DEMO_WEEK.entries.map((e, i) => ({ ...e, symbol: i % 2 ? "NIFTY BANK" : "NIFTY 50", entry_time: day(i + 1, "10:05"), exit_time: day(i + 1, "13:40") })),
    manual_entries: [] };
  await page.route(/\/api(?:\/v1)?\/journal\/week(\?.*)?$/, (r) => r.fulfill({ json: week }));
  await page.route(/\/api(?:\/v1)?\/journal\/settings$/, (r) => r.fulfill({ json: { settings: { custom_tags: ["late entry"], checklist_rules: [], attachment_max_mb: 5 }, defaults: {}, ranges: {}, components: {}, tags: [] } }));
  await page.goto("/analytics/journal");
  await expect(page.locator("[data-block='journal-list']")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("tab", { name: "Week" }).click();
  await page.waitForLoadState("networkidle");
  // On the Week tab: the discipline score card only (the cards after it would be cut by the crop), with every made-up
  // value hidden - the score, the decision count, each part's points and its bar (the title and part names stay).
  await capture(page, "journal", ["[data-front-score] ~ * { visibility: hidden !important; }",
    "div:has(> [data-score]) { display: none !important; } [data-component] .font-tabular, [data-component] > div[aria-hidden] { visibility: hidden !important; }"].join("\n"), async () => {
    await page.getByText("Discipline score").first().evaluate((el) => {
      const card = el.closest("section, [data-block], div.rounded-panel") ?? el;
      card.setAttribute("data-front-score", "");
      document.scrollingElement!.scrollTop = card.getBoundingClientRect().top + window.scrollY - 8;
    });
  });
});

test("daily-report", async ({ page }) => {
  await prepare(page, { ui_v2: true, ai_copilot: true, shared_daily_report: true });
  await mockIndexNumbers(page);
  const words: Record<string, string> = { up: "up", "up (weak)": "up, weakening", down: "down", "down (weak)": "down, weakening",
    range: "in a range", none: "not formed yet" };
  const indexRow = (symbol: string) => ({ ...REPORT.index[0], symbol, name: symbol, close: last(symbol).close, change_pct: changePct(symbol), read: null,
    structure_d: { direction: dailyWord(symbol), words: "", swing_high: null, swing_low: null },
    structure_w: { direction: weeklyWord(symbol), words: "", swing_high: null, swing_low: null } });
  const when = new Date(`${ASOF}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const n = "NIFTY 50";
  // The report's index row (NIFTY 50, as the real report) from the stored bars; the stock parts are left out of the picture.
  const report = { ...REPORT, trade_date: ASOF, generated_at: `${ASOF}T13:00:00Z`, info_line: `Last close ${when} · AI-generated · information only, not advice`,
    index: [indexRow(n)],
    sections: { ...REPORT.sections,
      index_read: dailyWord(n) === weeklyWord(n)
        ? `${n} closed ${changePct(n) >= 0 ? "higher" : "lower"}; daily and weekly structure ${words[dailyWord(n)]}.`
        : `${n} closed ${changePct(n) >= 0 ? "higher" : "lower"}; daily structure ${words[dailyWord(n)]}, weekly ${words[weeklyWord(n)]}.` } };
  await page.route(/\/api(?:\/v1)?\/daily-report\/latest$/, (r) => r.fulfill({ json: { report } }));
  await page.goto("/copilot/read");
  const card = page.locator("[data-daily-report]");
  await expect(card).toContainText("Daily market report", { timeout: 15_000 });
  await card.getByRole("button", { name: "Open the full report" }).click();
  const sheet = page.getByRole("dialog", { name: "Daily market report" });
  await expect(sheet).toBeVisible();
  await page.waitForLoadState("networkidle");
  // Hidden: the index close (the first figure of its row of four), the page behind the sheet (its card repeats the
  // close) and the parts after the index read (stock breadth, sectors, names) and each part's repeat of the
  // information line (the sheet's top line keeps it).
  await capture(page, "daily-report", ["[data-daily-report-body] [data-report-part] > .grid > div:first-child { display: none !important; }",
    "#root { visibility: hidden !important; }",
    "[data-daily-report-body] > [data-report-part]:not(:first-child), [data-report-part] [data-report-info] { display: none !important; }"].join("\n"));
});
