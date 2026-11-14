import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { DEMO_ENTRY, DEMO_LIST, DEMO_STATS } from "../../src/journal/journal.fixture";
import { api, mockDashboard } from "../../e2e/dashboardMocks";
import { mockApi, type MockOptions } from "../../e2e/mockApi";

/**
 * M95 (docs/design/M95_FRONT_DOOR.md) + the owner's review (14 Nov, "the images and the explanation under them don't
 * match"): the front door's six benefit images, each a REAL app screen (the production build on a mocked API, as the
 * e2e specs do), shot at the card's own 16:10 shape - a 360 x 225 phone-width window at 2x (720 x 450, FrontDoor.tsx
 * IMAGE_W x IMAGE_H) placed on the part that shows what the card's title and text say:
 *   copilot       a question and the Copilot's plain-words answer (chat bubbles);
 *   option-chain  the chain with Calls / Puts OI per strike, the "(OI ... in lakhs)" unit line, figures and bars;
 *   find-setups   a ready scan run, with its list of matches;
 *   trading-bot   "Your bots" (Paper) with one running bot and its rules (what it trades, how long, how it enters);
 *   journal       one practice trade opened, with the trader's own notes and tags;
 *   daily-report  the Telegram message the app sends (telegram_message.py: the backend's own text), in a chat bubble.
 * Public surface rule (CLAUDE.md §3: no names, no prices, no call words; no performance claims): MADE-UP data only,
 * placeholder names ("Index A", "Stock A".."Stock H"), every price hidden (strikes shown as ATM / ATM+n, no premium,
 * no fill, no amount, no P&L), checked on the visible words of each shot below. Light theme, English.
 * Raw PNGs and each shot's visible words go to FRONT_RAW (default /tmp/front-raw); shrink.py writes public/front/<slug>.png.
 */
const RAW = process.env.FRONT_RAW ?? "/tmp/front-raw";
mkdirSync(RAW, { recursive: true });
/** The shot: the card's 16:10 shape at a phone's width (CSS px; the file is 720 x 450, the config's scale 720 / 432). */
const SHOT = { width: 432, height: 270 };
/** A made-up day well in the past (no market day is claimed: every figure here is made up). */
const DAY = "2026-07-31";
const AT = `${DAY}T15:45:00+05:30`;
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const READ = (path: string): unknown => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));

/** The capture page's own CSS: one font, no caret, no scroll bars, no animation, no floating bars over the screen. */
const BASE_CSS = [
  "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; transition: none !important; animation: none !important; }",
  "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  "[data-bottom-nav-v2], [data-chat-bubble] { display: none !important; }",
].join("\n");
const DEBUG = process.env.FRONT_DEBUG === "1";

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

/** The visible words inside the shot (hidden ones excluded), for the checks and the report. */
async function visibleText(page: Page, size = SHOT): Promise<string[]> {
  return page.evaluate((shot) => {
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
      if (r.width === 0 || r.height === 0 || r.bottom <= 0 || r.top >= shot.height || r.right <= 0 || r.left >= shot.width) continue;
      let o: Element | null = el;
      let shown = true;
      while (o) { if (Number(getComputedStyle(o).opacity) === 0 || o.classList.contains("sr-only")) { shown = false; break; } o = o.parentElement; }
      if (shown) out.push(text);
    }
    return out;
  }, size);
}

/** Scrolls the page (or the element's own scroll box) so the element's top sits `gap` px below the screen's top. */
async function toTop(page: Page, selector: string, gap = 8) {
  // Twice: a top bar that folds away on scroll moves the element after the first jump.
  for (let i = 0; i < 2; i++) {
    await page.locator(selector).first().evaluate((el, g) => {
      let box: Element | null = el.parentElement;
      while (box && box !== document.body && !(box.scrollHeight > box.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(box).overflowY))) box = box.parentElement;
      const scroller = !box || box === document.body ? document.scrollingElement! : box;
      scroller.scrollTop += el.getBoundingClientRect().top - g;
    }, gap);
    await page.waitForTimeout(300);
  }
}

/** Names, call words and prices a public picture must not carry (CLAUDE.md §3). `figures`: OI in lakhs may show. */
function checkWords(text: string, figures = false) {
  expect(text, "no rupee amount").not.toMatch(/₹|\bRs\.?\s?\d/);
  expect(text, "no price-like figure (a thousands separator or a 4+ digit number)").not.toMatch(/\d,\d{2,3}|(?<!(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) )\b\d{4,}(?:\.\d+)?\b/);
  if (!figures) expect(text, "no two-decimal figure").not.toMatch(/\d\.\d{2}\b(?!%)/);
  expect(text, "no real name").not.toMatch(/NIFTY|SENSEX|BANKEX|VIX|RELIANCE|STOCK\d|DEMO[A-D]\b|TEST[A-D]{3}/i);
  expect(text, "no call words").not.toMatch(/\b(buy|sell|target|stop[- ]?loss|stop hit)\b/i);
}

async function capture(page: Page, slug: string, hideCss: string, place?: () => Promise<void>, figures = false, shot = SHOT) {
  await page.addStyleTag({ content: BASE_CSS + "\n" + hideCss });
  if (place) await place();
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(400);
  const words = await visibleText(page, shot);
  if (DEBUG) writeFileSync(join(RAW, `${slug}.all.txt`), (await page.evaluate(() => {
    const out: string[] = [];
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      const t = n.textContent?.trim(); const el = n.parentElement;
      if (!t || !el) continue;
      const r = el.getBoundingClientRect();
      if (r.height) out.push(`${Math.round(r.top)} ${el.tagName}${[...el.attributes].filter((a) => a.name.startsWith("data-")).map((a) => `[${a.name}]`).join("")}: ${t.slice(0, 80)}`);
    }
    return out;
  })).join("\n"));
  writeFileSync(join(RAW, `${slug}.txt`), words.join("\n") + "\n");
  checkWords(words.join(" "), figures);
  await page.screenshot({ path: join(RAW, `${slug}.png`), animations: "disabled", clip: { x: 0, y: 0, ...shot } });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "no sideways scroll").toBeLessThanOrEqual(0);
}

const CHAT = READ("../../e2e/fixtures/copilot_chat_v2.json") as { home: Record<string, unknown>; answers: Record<string, Record<string, unknown>> };

test("copilot", async ({ page }) => {
  await prepare(page, { ui_v2: true, ai_copilot: true });
  // A question about an idea, in plain words, and the Copilot's plain-words answer (the fixture's shape, no chart, no
  // figure, no name). The home card's market line is left out (it names an index and its close).
  const fixture = CHAT.answers["What does the option chain show?"];
  const question = "What does a higher low on a chart mean?";
  const answer = { ...fixture, job: "explain", title: "Higher low",
    summary: ["The price fell back, then turned up again before reaching its last low.", "It is one sign that the up-move is still in place."],
    answer: "The price fell back, then turned up again before reaching its last low. It is one sign that the up-move is still in place.",
    sections: [{ title: "What to watch", lines: ["Whether the next rise goes past the last high.", "A close below the higher low cancels it."] }],
    blocks: [], worked: [], unknowns: [], starters: [], concepts: [], notes: [] };
  const home = { ...CHAT.home, market_line: { line: null, reason: null, as_of: null, fii_net_crore: null, fii_as_of: null } };
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, async (route) => {
    const request = route.request();
    if (request.url().endsWith("/history")) return route.fulfill({ json: { turns: [], keep: 20 } });
    if (request.method() === "GET") return route.fulfill({ json: home });
    return route.fulfill({ json: answer });
  });
  await page.route(/\/api(?:\/v1)?\/market\/strip(\?.*)?$/, (r) => r.fulfill({ status: 404, json: { detail: "Not found" } }));
  await page.goto("/copilot");
  await page.waitForLoadState("networkidle");
  const box = page.getByRole("textbox", { name: "Your question" });
  await box.fill(question);
  await box.press("Enter");
  await expect(page.locator("[data-msg='copilot'] [data-answer-card]")).toBeVisible({ timeout: 15_000 });
  await page.waitForLoadState("networkidle");
  // The question box (it floats over the conversation in a still picture), the folded "Advanced" list of jobs (its
  // lines name indices) and the sent / answered times.
  await capture(page, "copilot", "[data-composer='bottom'], [data-composer-dock], [data-tour='copilot-more'], [data-sent-at], [data-turn-at], [data-msg-head] > span:last-child:not(.font-semibold) { display: none !important; }",
    () => toTop(page, "[data-turn]", 12));
});

const CHAIN = READ("../../e2e/fixtures/option_chain_v2.json") as {
  expiries: Record<string, unknown>; live: Record<string, unknown> & { rows: { strike: number }[]; summary: Record<string, unknown> };
};

test("option-chain", async ({ page }) => {
  await prepare(page, { ui_v2: true }, {}, ["option-chain"]);
  // The e2e chain (made-up OI, round numbers). Shown: the Calls / Puts OI per strike with its bar and the unit line;
  // hidden: the summary (spot, max pain - prices), every premium column and the underlying's name; each strike is
  // relabelled ATM / ATM+n / ATM-n (its place from the money, not its price).
  const tuesday = (weeks: number) => { const d = new Date(`${DAY}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + ((2 - d.getUTCDay() + 7) % 7 || 7) + 7 * weeks); return d.toISOString().slice(0, 10); };
  const expiries = [0, 1, 2, 3].map((w, i) => ({ date: tuesday(w), kind: i === 3 ? "monthly" : "weekly" }));
  const live = { ...CHAIN.live, expiry: expiries[0].date, fetched_at: `${DAY}T10:00:00+00:00`, market_open: false };
  const sessions = { NSE: { is_open: false, reason: "closed", next_open: null } };
  await page.route(/\/api(?:\/v1)?\/system\/status$/, (r) => r.fulfill({ json: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions } }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/expiries")) return route.fulfill({ json: { ...CHAIN.expiries, today: DAY, default: expiries[0].date, expiries } });
    return route.fulfill({ json: live });
  });
  await page.route(/\/api(?:\/v1)?\/option-chain\/analyses/, (r) => r.fulfill({ status: 404, json: { detail: "Not found" } }));
  await page.route(/\/api(?:\/v1)?\/me\/prefs$/, (r) => r.fulfill({ json: {
    prefs: { chart: {}, setup: {}, oc_analyses: { items: {} }, appearance: { text_size: "normal" } }, versions: { chart: 0, setup: 0, oc_analyses: 0, appearance: 0 } } }));
  await page.goto("/option-chain");
  await expect(page.locator("tr[data-atm]")).toBeVisible({ timeout: 15_000 });
  await page.waitForLoadState("networkidle");
  await capture(page, "option-chain", [
    "[data-block='chain-summary'] { display: none !important; }",
    "[data-chain-scroll] [data-col$='-ltp'], [data-chain-scroll] [data-front-hide] { display: none !important; }",
    // The column titles' own screen-reader words ("Call" / "Put") shown, so the picture says which side is which.
    "[data-chain-scroll] thead .sr-only { position: static !important; width: auto !important; height: auto !important; margin: 0 !important; clip: auto !important; overflow: visible !important; white-space: nowrap !important; }",
  ].join("\n"), async () => {
    await page.evaluate(() => {
      const scroll = document.querySelector("[data-chain-scroll]")!;
      // The header cells of the hidden premium columns (no data attribute of their own).
      scroll.querySelectorAll("thead th").forEach((e) => { if (/LTP/.test(e.textContent ?? "")) e.setAttribute("data-front-hide", ""); });
      // Each strike as its place from the money: ATM, ATM+1 (above), ATM-1 (below).
      const rows = [...scroll.querySelectorAll("tbody tr")];
      const atm = rows.findIndex((r) => r.hasAttribute("data-atm"));
      rows.forEach((r, i) => {
        const cell = r.querySelector("[data-strike], th[scope='row']");
        if (!cell) return;
        const d = i - atm;
        // The ATM row already carries its own "ATM" mark: its strike figure just goes.
        const label = d === 0 ? (/ATM/.test(cell.textContent ?? "") ? "" : "ATM") : d > 0 ? `ATM+${d}` : `ATM−${-d}`;
        const walk = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
        let first = true;
        for (let n = walk.nextNode(); n; n = walk.nextNode()) {
          if (!/\d/.test(n.textContent ?? "")) continue;
          n.textContent = first ? label : "";
          first = false;
        }
      });
    });
    await toTop(page, "[data-chain-scroll]", 30);
    await page.locator("[data-chain-scroll]").evaluate((el) => {
      const box = el as HTMLElement;
      const atm = box.querySelector("tr[data-atm]") as HTMLElement | null;
      if (atm && box.scrollHeight > box.clientHeight) box.scrollTop = atm.offsetTop - box.clientHeight / 2;
    });
  }, true);
});

test("find-setups", async ({ page }) => {
  await prepare(page, { ui_v2: true, screener_v2: true, screener_gallery: true, screener_copilot: true }, {}, ["screener", "scanner"]);
  const registry = (READ("../../src/screener/builderTexts.json") as { registry: unknown }).registry;
  const taxonomy = READ("../../src/screener/taxonomySample.json");
  // A made-up list with placeholder names; a ready scan is run on it and its matches listed.
  const members = Array.from({ length: 40 }, (_, i) => `Member ${i + 1}`);
  /** The answer's names: placeholders only, the matches "Stock A", "Stock B", ... in order. */
  const answerNames = (count: number) => { let k = 0; return Array.from({ length: count }, (_, i) => (i % 6 === 1 ? `Stock ${String.fromCharCode(65 + k++)}` : `Other ${i + 1}`)); };
  const universes = { default_key: "index-a", groups: [{ group: "INDEX_STOCKS", label: "Index stocks", lists: [{ key: "index-a", name: "Index A stocks (40)", count: 40 }] }],
    batch: { size: 40, per_minute: 100, per_second: 4, max_symbols: 500 } };
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/taxonomy$/.test(url)) return route.fulfill({ json: taxonomy });
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
    if (/\/universes$/.test(url)) return route.fulfill({ json: universes });
    if (/\/universes\/index-a$/.test(url)) return route.fulfill({ json: { key: "index-a", name: "Index A", symbols: members, truncated: false, source_count: 40, exchange: "NSE" } });
    if (/\/run$/.test(url)) {
      const b = route.request().postDataJSON() as { source: string; symbols: string[]; preview?: boolean };
      if (b.preview) return route.fulfill({ status: 503, json: { detail: "no preview here" } });
      // The ready scan runs on its own list; the answer names its symbols by placeholder only.
      const names = answerNames(b.symbols.length);
      const matched = names.filter((n) => n.startsWith("Stock "));
      return route.fulfill({ json: {
        run_id: 31, preview: false, text: b.source, base_tf: "1d", data_source: "broker:upstox", scanned: names.length, matched,
        results: names.map((s) => ({ symbol: s, matched: matched.includes(s), reason: null })),
        funnel: { universe: b.symbols.length, with_data: b.symbols.length,
          stages: b.source.split(" AND ").map((text, i, all) => ({ text, survivors: i === all.length - 1 ? matched.length : Math.ceil(b.symbols.length / 2) })) },
        disclaimer: "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations." } });
    }
    return route.fulfill({ status: 503, headers: { "X-Feature-Disabled": "screener_legends" }, json: { detail: "disabled" } });
  });
  await page.goto("/screener");
  await expect(page.getByTestId("start-scan")).toBeVisible({ timeout: 15_000 });
  await page.waitForLoadState("networkidle");
  await page.getByTestId("ready-scans").getByTestId("ready-scan-run").first().click();
  await expect(page.getByRole("cell", { name: "Stock A", exact: true })).toBeVisible({ timeout: 15_000 });
  await page.waitForLoadState("networkidle");
  // From "Matched n of N" down the list of matches. Left out for room: the run's clock time, the "did not match" line,
  // the results' own header (its tools) and each row's empty stage mark.
  await capture(page, "find-setups", ["[data-testid='screener-bottom-bar'], [data-testid='results-head'] > span, [data-testid='results-hidden'] { display: none !important; }",
    "section[aria-label='Results'] > header, section[aria-label='Results'] td span.text-t12:has(> .sr-only) { display: none !important; }"].join("\n"),
    () => toTop(page, "[data-testid='results-head']", 8));
});

test("trading-bot", async ({ page }) => {
  const D = READ("../../e2e/fixtures/dashboard_v2.json") as Record<string, unknown>;
  const S = READ("../../e2e/fixtures/setup_v2.json") as Record<string, unknown>;
  await prepare(page, D.features as Record<string, boolean>, { brokers: D.token_status as unknown[] }, [], `${DAY}T11:20:00+05:30`);
  await mockDashboard(page, { me: true });
  const stamp = (hm: string) => `${DAY}T${hm}:00+05:30`;
  // One running PAPER bot on a placeholder index: its rules as the page shows them (the strategy, what it trades, how
  // long it holds, how it enters), no position, no signal time.
  const bot = { exchange: "NSE", broker_name: null, last_signal_at: null, last_error: null, consecutive_failures: 0, created_by: 7,
    created_at: stamp("09:00"), updated_at: stamp("09:00"), strike_offset: 0, premium_stop_pct: null, contract_rules: "", mode: "PAPER",
    instrument_kind: "OPTION", option_position: "BUY", expiry_rule: "NEAREST", strike_rule: "ATM", max_lots: 1, holding: "SWING", pause_reason: null,
    id: 52, strategy_id: "vwap_supertrend_5m", symbol: "Index A", timeframe: "15min", status: "ACTIVE", last_evaluated_at: stamp("11:19"), open_positions: 0,
    entry_mode_in_effect: "APPROVAL", entry_mode_follows_settings: true };
  await page.route(api("/strategies$"), (route) => route.fulfill({ json: S.strategies }));
  await page.route(api("/custom-strategies$"), (route) => route.fulfill({ json: [] }));
  await page.route(api("/broker/credentials$"), (route) => route.fulfill({ json: [{ broker_name: "upstox", account_label: "primary" }] }));
  await page.route(api("/broker/accounts$"), (route) => route.fulfill({ json: [] }));
  await page.route(api("/deployments(\\?.*)?$"), (route) => route.fulfill({ json: [bot] }));
  await page.route(api("/worker/status$"), (route) => route.fulfill({ json: { running: true, market_open: true, market_status: "Open" } }));
  await page.goto("/deployments");
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  await capture(page, "trading-bot", "[data-bot-signal] { display: none !important; }", () => toTop(page, "[data-block='bots']", 6));
});

test("journal", async ({ page }) => {
  await prepare(page, { ui_v2: true, discipline_engine: true, phase0_paper_only: true }, { brokers: [] }, ["journal"]);
  // One practice (PAPER) trade on a placeholder index, opened: the trader's own notes on it.
  // Hidden: the plan and the fills (prices), the amount and R (no performance), the exit reason, the charts.
  const entry = { ...DEMO_ENTRY, symbol: "Index A", direction: "LONG", setup: "Buyer area",
    entry_time: `${DAY}T05:05:00Z`, exit_time: `${DAY}T08:40:00Z`,
    plan_view: { ...DEMO_ENTRY.plan_view, setup: "Buyer area", timeframe: "15min", area: ["Pullback into the buyer area", "Weakening momentum"] },
    notes: "Waited for the candle to close inside the area. Entered a little late; next time I set the alert earlier.",
    tags: [], user_tags: ["late entry"],
    checklist: [{ rule: "Trend agrees", followed: true, retired: false }, { rule: "Waited for the close", followed: true, retired: false }] };
  const list = { ...DEMO_LIST, setups: ["Buyer area", "Seller area"], tags: ["late entry"],
    entries: DEMO_LIST.entries.map((e, i) => ({ ...e, symbol: i % 2 ? "Index B" : "Index A", source: "ATP", source_label: null, outside_atp: false,
      kind: "trade", mode: "PAPER", user_tags: i === 2 ? ["late entry"] : [] })) };
  await page.route(/\/api(?:\/v1)?\/journal\/entries(\?.*)?$/, (r) => r.fulfill({ json: list }));
  await page.route(/\/api(?:\/v1)?\/journal\/(trades|manual)\/\d+(\?.*)?$/, (r) => r.fulfill({ json: entry }));
  await page.route(/\/api(?:\/v1)?\/journal\/stats(\?.*)?$/, (r) => r.fulfill({ json: DEMO_STATS }));
  await page.route(/\/api(?:\/v1)?\/journal\/settings$/, (r) => r.fulfill({ json: { settings: { custom_tags: ["late entry"], checklist_rules: [], attachment_max_mb: 5 }, defaults: {}, ranges: {}, components: {}, tags: [] } }));
  await page.goto("/analytics/journal");
  await expect(page.locator("[data-block='journal-list']")).toBeVisible({ timeout: 15_000 });
  await page.locator("[data-journal-row]").first().click();
  await expect(page.locator("[data-notes]")).toBeVisible({ timeout: 15_000 });
  await page.waitForLoadState("networkidle");
  await capture(page, "journal", [
    "[data-sheet-section='plan'], [data-sheet-section='execution'], [data-sheet-section='charts'], [data-sheet-section='images'] { display: none !important; }",
    // The sheet over the whole shot (the list behind it carries amounts) and its first line (time, amount, R) left out.
    "#root { visibility: hidden !important; } [role='dialog'] { top: 0 !important; max-height: 100vh !important; border-radius: 0 !important; }",
    "[data-entry-editor] > div:first-child, [data-save-notes] { display: none !important; }",
    // The notes right under the trade's name, the box as tall as his words (the parts after it are cut by the shot).
    "[data-sheet-section='notes'] { border-top: 0 !important; padding-top: 0 !important; } [data-notes] { height: 6.4em !important; }",
    "[data-entry-editor] > * { margin-top: 0 !important; } [data-entry-editor] > section ~ section { margin-top: 10px !important; padding-top: 10px !important; }",
  ].join("\n"));
});

/** The Telegram chat at a wider window (Telegram's own column), 480 x 300 at 1.5x: the same 720 x 450 file. */
const TELEGRAM = { width: 480, height: 300 };
test.describe("telegram", () => {
  test.use({ viewport: TELEGRAM, deviceScaleFactor: 1.5, isMobile: false, hasTouch: false });
test("daily-report", async ({ page }) => {
  // The message the app sends (the backend's own text and buttons, telegram_message.py) in a Telegram-style chat.
  const script = fileURLToPath(new URL("./telegram_message.py", import.meta.url));
  const msg = JSON.parse(execFileSync("python3", [script], { encoding: "utf8" })) as { html: string; buttons: string[] };
  await page.clock.install({ time: new Date(AT) });
  const body = msg.html.split("\n").map((line) => line || "&nbsp;").map((line) => `<div>${line}</div>`).join("");
  await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>
    body { margin: 0; background: #c8d9a8; font: 12.5px/1.3 'DejaVu Sans', sans-serif; color: #000; }
    .bar { display: flex; align-items: center; gap: 10px; height: 38px; padding: 0 12px; background: #fff; border-bottom: 1px solid #ddd; }
    .ava { width: 30px; height: 30px; border-radius: 50%; background: #3390ec; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 12px; }
    .name { font-weight: 700; font-size: 14px; } .sub { color: #707579; font-size: 11px; }
    .chat { padding: 6px 10px; }
    .bubble { background: #fff; border-radius: 12px 12px 12px 4px; padding: 6px 10px; max-width: 86%; }
    .btns { display: flex; gap: 4px; margin-top: 4px; max-width: 86%; }
    .btn { flex: 1; text-align: center; background: rgba(0,0,0,.22); color: #fff; border-radius: 8px; padding: 6px 4px; font-size: 12px; font-weight: 600; }
  </style></head><body>
    <div class="bar"><div class="ava">AA</div><div><div class="name">AiAlgoDesk</div><div class="sub">bot</div></div></div>
    <div class="chat">
      <div class="bubble" data-message>${body}</div>
      <div class="btns">${msg.buttons.map((b) => `<div class="btn">${b}</div>`).join("")}</div>
    </div></body></html>`);
  await capture(page, "daily-report", "", undefined, false, TELEGRAM);
});
});
