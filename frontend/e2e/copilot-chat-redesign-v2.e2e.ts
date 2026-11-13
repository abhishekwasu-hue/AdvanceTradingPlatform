import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { copilotStrip } from "./copilotMocks";
import { T, mockTrading } from "./tradingMocks";

/**
 * Owner 13 Nov: "chat window is not attractive and not looking real", and "Ask AI opened the Copilot's old clutter - a
 * specific chat should open". On made-up replies (no backend): the general chat sends a message (Enter), the input sits
 * at the foot, the answer lands in the conversation; Ask AI on a position opens a NEW chat about that position only (its
 * header, facts and own questions, the market folded, no old messages). Desktop 1440 and phone 390.
 * `CHAT_SHOTS=<dir>` writes the evidence screenshots there (dark and light); `CHAT_STAGE=before` runs the same journey
 * on the build before the redesign, for the before / after comparison (only the steps both builds share).
 */
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown>; answers: Record<string, Record<string, unknown>> };
const SHOTS = process.env.CHAT_SHOTS;
const BEFORE = process.env.CHAT_STAGE === "before";

/** Made-up: the owner's screenshot - a short NIFTY call, two lots, no stop of its own. */
const POSITION = {
  id: 642, mode: "PAPER", symbol: "NIFTY 25150 CE 17 NOV 26", strategy_id: "manual", direction: "SHORT", entry_time: "2026-11-11T05:00:00Z",
  entry_price: 174.6, quantity: 130, stop_loss: null, target1: null, target2: null, exit_time: null, exit_price: null, exit_reason: null, pnl: null,
  charges: 0, lot_size: 65, exchange: "NFO", instrument_kind: "OPTION", option_position: "WRITE", expiry: "2026-11-17", underlying_symbol: "NIFTY",
  underlying_direction: "SHORT", underlying_stop_loss: null, underlying_target1: null,
};
const AREA = { low: 24989.35, high: 25141.3, state: "fresh", where: "below", distance_pct: 0.03, range_text: "24,989.35–25,141.30", from: "strike", change: null };
const MY_DAY = { as_of: "2026-11-13T06:40:00Z", today: "2026-11-13", previous_session: "2026-11-12", note: "", thresholds: { watch_room_left_pct: 1, near_room_left_pct: 0.5 },
  positions: [{ id: 642, symbol: POSITION.symbol, mode: "PAPER", side: "SHORT", kind: "OPTION", underlying: "NIFTY", deployment_id: null, chip: null, chip_label: null,
    room_left_pct: null, chip_text: null, expiry: "2026-11-17", expiry_sessions: 4, expiry_text: "4 sessions", area: AREA, area_text: "" }],
  since_last_session: { gaps: [], events: [], areas: [], empty: null } };

/** Made-up: the market numbers of the owner's screenshot - BANK NIFTY's change not known. */
function strip() {
  const base = copilotStrip() as { indices: Record<string, unknown>[] } & Record<string, unknown>;
  const at = "2026-11-13T06:40:00Z";
  const rows: Record<string, Record<string, unknown>> = {
    "NIFTY 50": { ltp: 25118.6, prev_close: 25299.35, change: -180.75, change_pct: -0.71 },
    "NIFTY BANK": { ltp: 60948.65, prev_close: null, change: null, change_pct: null },
    SENSEX: { ltp: 81462.28, prev_close: 81911.01, change: -448.73, change_pct: -0.55 },
    "INDIA VIX": { ltp: 16.14, prev_close: 15.8, change: 0.34, change_pct: 2.15 },
  };
  return { ...base, fetched_at: at, indices: base.indices.map((i) => ({ ...i, ...rows[i.key as string], as_of: at, stale: false, source: "live", missing: false })) };
}

const HOME = { ...F.home, market_line: { line: "NIFTY 50 last closed at 25,299.35 (12 Nov 2026); by its swing highs and lows the daily trend is down (weak), the weekly sideways; India VIX 16.14 (+2.15%).", reason: null, as_of: null, fii_net_crore: null } };

/** The Check my trade answer: before - the last close called "the price"; after - the live price with its time. */
function tradeCheck() {
  const a = F.answers["Check my trade idea"];
  const price = BEFORE ? [] : [{ title: "Price", lines: ["Price: 25,118.60 at 12:10"] }];
  const area = BEFORE ? "The price 25,299.35 is inside the demand area 25,213.65 - 25,370.7 (tested)."
    : "The price 25,118.60 at 12:10 is not inside any of ATP's stored areas.";
  return { ...a, sections: [...price, { title: "Your idea as I read it", lines: ["NIFTY: nothing else stated yet."] },
    { title: "Trend side", lines: ["Daily: the structure reads down (weak).", "Weekly: the structure reads range."] }, { title: "Area", lines: [area] }] };
}

/** Made-up: the answer about the position the focused chat asked about (its own numbers only). */
const ABOUT_POSITION = { ...F.answers["How are my open positions?"], title: "Your position", headline: null, blocks: [], chips: [], starters: [],
  summary: ["NIFTY 25150 CE 17 NOV 26, short 2 lots (130): at the last price 123.75 against your average 174.60 the open P&L is +6,610."],
  sections: [{ title: "P&L", lines: ["Average: 174.60", "Last price: 123.75", "Open P&L: +6,610"] },
    { title: "Time to expiry", lines: ["Expiry: 4 days", "An option's time value shrinks as expiry comes nearer; the last days shrink it fastest."] }] };

/** An earlier conversation of today, kept by the server (the owner's "Asked earlier · 13 Nov, 12:09"). */
const KEPT = { keep: 20, session: { id: 9, title: "Check my trade idea" }, turns: [{ id: 1, question: "Check my trade idea", answer: tradeCheck(), at: "2026-11-13T06:39:00Z" }] };

async function open(page: Page, theme: "dark" | "light", path: string) {
  await page.clock.install({ time: new Date("2026-11-13T12:10:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["copilot-chat", "positions"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ...(T.features as Record<string, boolean>), ai_copilot: true }, brokers: T.token_status as unknown[] });
  await mockTrading(page);
  const asked: { message: string; context: unknown; new_chat?: boolean }[] = [];
  await page.route(/\/api(?:\/v1)?\/positions$/, (r) => (r.request().method() === "GET" ? r.fulfill({ json: [POSITION] }) : r.fallback()));
  await page.route(/\/api(?:\/v1)?\/portfolio\/exposure(\?.*)?$/, (r) => r.fulfill({ json: { ...(T.exposure as object), prices: { [POSITION.symbol]: 123.75 }, priced_symbols: [POSITION.symbol], price_source: "ltp" } }));
  await page.route(/\/api(?:\/v1)?\/today\/my-day$/, (r) => r.fulfill({ json: MY_DAY }));
  await page.route(/\/api(?:\/v1)?\/market\/strip(\?.*)?$/, (r) => r.fulfill({ json: strip() }));
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history|\/sessions)?(\?.*)?$/, (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/history")) return route.fulfill({ json: KEPT });
    if (url.pathname.endsWith("/sessions")) return route.fulfill({ json: { sessions: [{ id: 9, title: "Check my trade idea", turns: 1, updated_at: "2026-11-13T06:39:00Z" }] } });
    if (request.method() === "GET") return route.fulfill({ json: HOME });
    const body = request.postDataJSON() as { message: string; context: unknown; new_chat?: boolean };
    asked.push(body);
    const answer = body.message === "Check my trade idea" ? tradeCheck() : ABOUT_POSITION;
    return route.fulfill({ json: { ...answer, conversation: { id: body.new_chat ? 10 : 9, title: body.message } } });
  });
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; transition: none !important; animation: none !important; }" });
  return asked;
}

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${BEFORE ? "before" : "after"}-${name}.png` });
}

const SIZES = [{ name: "1440", viewport: { width: 1440, height: 900 }, phone: false }, { name: "390", viewport: { width: 390, height: 844 }, phone: true }] as const;

for (const size of SIZES) {
  test.describe(`owner 13 Nov chat redesign ${size.name}`, () => {
    test.use({ viewport: size.viewport, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as const) {
      test(`the general chat sends a message with Enter; the input stays at the foot (${theme})`, async ({ page }) => {
        const asked = await open(page, theme, "/copilot");
        const box = page.locator("#copilot-question");
        await expect(box).toBeVisible({ timeout: 15_000 });
        await box.fill("Check my trade idea");
        const before = await page.locator("[data-turn] [data-answer]").count();     // the kept turn may be on screen already
        await box.press("Enter");
        await expect(page.locator("[data-turn] [data-answer]")).toHaveCount(before + 1);
        expect(asked.at(-1)?.message).toBe("Check my trade idea");
        if (!BEFORE) {
          const area = await page.locator("[data-conversation]").boundingBox();
          const input = await box.boundingBox();
          expect(input!.y).toBeGreaterThan(area!.y);                                 // the box below the conversation
          await expect(page.locator("[data-market-pills] [data-index='NIFTY BANK']")).not.toContainText("–");
          await expect(page.locator("[data-section='Price']").last()).toContainText("25,118.60 at 12:10");
          await expect(page.locator("[data-msg='copilot'] [data-copilot-avatar]").last()).toBeVisible();
          const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
          expect(sideways).toBeLessThanOrEqual(1);
        }
        await page.waitForTimeout(1500);
        await shot(page, `general-${size.name}-${theme}`);
      });

      test(`Ask AI on a position opens a new chat about that position only (${theme})`, async ({ page }) => {
        const asked = await open(page, theme, "/positions");
        const ask = page.locator("[data-ask-ai]").first();
        await expect(ask).toBeVisible({ timeout: 15_000 });
        await ask.click();
        const panel = page.locator("[data-inplace-panel='copilot']");
        await expect(panel.locator("#copilot-question")).toBeVisible({ timeout: 15_000 });
        await page.waitForTimeout(600);
        if (!BEFORE) {
          await expect(panel.locator("[data-focus-title]")).toHaveText("NIFTY 25150 CE · 17 Nov · Short 2 lots");
          await expect(panel.locator("[data-focus-facts]")).toContainText("LTP123.75");
          await expect(panel.locator("[data-fact='pnl']")).toContainText("+₹6,610");                // (174.60 − 123.75) × 130, short
          await expect(panel.locator("[data-fact='stop']")).toContainText("not set");
          await expect(panel.locator("[data-turn]")).toHaveCount(0);                // no old messages
          await expect(panel.locator("[data-quick-chips]")).toHaveCount(0);
          await expect(panel.locator("[data-market-pills]")).toHaveCount(0);          // folded; one tap shows it
          const q = panel.locator("[data-suggestion]");
          await expect(q).toHaveCount(4);
          await expect(q.first()).toHaveText("What happens to this position if NIFTY reaches the area 24,989–25,141?");
        }
        await shot(page, `focus-${size.name}-${theme}`);
        if (!BEFORE) {
          await panel.locator("[data-suggestion]").nth(2).click();
          await expect(panel.locator("[data-turn] [data-answer]")).toHaveCount(1);
          const sent = asked.at(-1)!;
          expect(sent.message).toBe("Explain this position's P&L and its time decay to expiry (4 days).");
          expect(sent.new_chat).toBe(true);                                             // a new conversation
          expect(sent.context).toMatchObject({ object: { kind: "trade", id: 642 }, page: "positions" });
          await page.waitForTimeout(1500);
          await shot(page, `focus-answer-${size.name}-${theme}`);
        }
      });
    }
  });
}

/** Made-up: a chart reading as a card, with the widest numbers the table carries (the coordinator's 13 Nov note: the
 * "Protected level" column was cut at 390 px). */
const CARD_ANSWER = {
  ...F.answers["Read the NIFTY chart"], structure_card: {
    symbol: "NIFTY", price: 25118.6, timeframe: "Daily",
    timeframes: [
      { tf: "1w", label: "Weekly", ready: true, trend: "sideways", weak: true, protected: 25370.7, last_break: { label: "Break against the trend", price: 25213.65 } },
      { tf: "1d", label: "Daily", ready: true, trend: "down", weak: true, protected: 25299.35, last_break: { label: "Swing break", price: 25141.3 } },
      { tf: "1h", label: "1-hour", ready: true, trend: "down", weak: false, protected: 25230.45, last_break: null },
      { tf: "15m", label: "15-minute", ready: true, trend: "up", weak: true, protected: 25101.15, last_break: null },
    ],
    areas_above: [{ side: "supply", from: 25213.65, to: 25370.7, where: "above", distance: 95.05 }],
    areas_below: [{ side: "demand", from: 24989.35, to: 25041.3, where: "below", distance: 77.3 }],
    swing_levels: [{ side: "high", price: 25370.7 }, { side: "low", price: 24989.35 }],
    watch: "Whether the daily protected level holds or breaks on a closed bar, and how the price behaves when it reaches the nearest area.",
  },
};

for (const width of [360, 390]) {
  test.describe(`owner 13 Nov structure card ${width}`, () => {
    test.use({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const where of ["page", "panel"] as const) {
      test(`no number is cut off and nothing scrolls sideways (${where})`, async ({ page }) => {
        await open(page, "light", where === "page" ? "/copilot" : "/positions");
        await page.route(/\/api(?:\/v1)?\/ai\/chat$/, (route) => (route.request().method() === "POST" ? route.fulfill({ json: CARD_ANSWER }) : route.fallback()));
        const scope = where === "page" ? page.locator("main") : page.locator("[data-inplace-panel='copilot']");
        if (where === "panel") await page.locator("[data-ask-ai]").first().click();
        const box = scope.locator("#copilot-question");
        await expect(box).toBeVisible({ timeout: 15_000 });
        await box.fill("Read the NIFTY chart");
        await box.press("Enter");
        const table = scope.locator("[data-structure-card='NIFTY'] [data-card-block='table']").last();
        await expect(table).toBeVisible();
        await expect(table.locator("[data-tf='1w']")).toContainText("25,370.70");
        await table.scrollIntoViewIfNeeded();
        await page.waitForTimeout(300);
        await shot(page, `structure-${where}-${width}`);
        if (BEFORE) return;                                                            // the before build: evidence only
        const fit = await table.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth,
          cells: [...el.querySelectorAll("td, th")].every((c) => c.scrollWidth <= (c as HTMLElement).clientWidth + 1) }));
        expect(fit.scroll).toBeLessThanOrEqual(fit.client + 1);                       // the whole table fits its card
        expect(fit.cells).toBe(true);                                                  // and no cell clips its text
        const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(sideways).toBeLessThanOrEqual(1);
      });
    }
  });
}
