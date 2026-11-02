import { expect, test, type Page } from "@playwright/test";
import { READING_WITH_AREAS, madeUpBars, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M75 W-3 (owner order M75 CW-2 and CW-8; flag `copilot_workspace`): the workspace's annotations in a browser, every
 * reply made up here (no backend). One reading, one source: areas as filled bands with side / timeframe / state / tests,
 * swings, BOS / CHoCH, an RSI divergence and the structure level on the chart; the short table beside it (below it on a
 * phone) with the source line; an engine reading that lacks a part says so. 390 / 1440, light and dark, no sideways
 * scroll. `W3_OUT=<folder>` also writes screenshots (the comparison page), never baselines.
 */
const OUT = process.env.W3_OUT;
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

/** Made-up annotations on the made-up 5-minute bars: swings at the bars' local extremes (a mock, not a reader). */
function annotations(source: "atp" | "engine") {
  const bars = madeUpBars("5min").map((b) => ({ ...b, time: Date.parse(b.timestamp) / 1000 }));
  const swings: { label: string; kind: "H" | "L"; time: number; price: number; price_text: string; timeframe: string }[] = [];
  let lastH: number | null = null, lastL: number | null = null;
  for (let i = 8; i < bars.length - 8; i += 1) {
    const win = bars.slice(i - 8, i + 9);
    if (bars[i].high === Math.max(...win.map((b) => b.high))) {
      swings.push({ label: lastH == null || bars[i].high > lastH ? "HH" : "LH", kind: "H", time: bars[i].time, price: bars[i].high, price_text: bars[i].high.toFixed(2), timeframe: "5m" });
      lastH = bars[i].high;
    } else if (bars[i].low === Math.min(...win.map((b) => b.low))) {
      swings.push({ label: lastL == null || bars[i].low > lastL ? "HL" : "LL", kind: "L", time: bars[i].time, price: bars[i].low, price_text: bars[i].low.toFixed(2), timeframe: "5m" });
      lastL = bars[i].low;
    }
  }
  const shown = swings.slice(-8);
  const highs = shown.filter((s) => s.kind === "H"), lows = shown.filter((s) => s.kind === "L");
  const last = bars[bars.length - 1];
  const area = (role: "supply" | "demand", low: number, high: number, state: string, tests: number) => ({
    role, side: role === "demand" ? "buyer" : "seller", low, high, low_text: low.toFixed(2), high_text: high.toFixed(2), timeframe: "15m", state,
    state_words: state, tests, words: `${role === "demand" ? "Buyer" : "Seller"} area ${low.toFixed(2)} - ${high.toFixed(2)} (15-minute): ${state}.` });
  const areas = [area("supply", 24700, 24780, "fresh", 0), area("demand", 24000, 24060, "tested", 2), area("demand", 23450, 23520, "fresh", 0)];
  const hi = highs[highs.length - 1];
  const engine = source === "engine";
  return {
    source, timeframe: "5m", as_of: "2026-10-19T05:25:00+00:00", last_close: last.close,
    source_words: engine ? "Drawn from your signal engine's reading of its signal timeframe, as of its last closed bar. Signal timeframe: 15-minute."
      : "Drawn from ATP's chart readers on this chart's closed bars.",
    areas, swings: shown,
    events: engine || !hi ? [] : [{ type: "BOS", name: "BOS", direction: "up", time: bars[bars.length - 6].time, swing_time: hi.time, price: hi.price,
      price_text: hi.price.toFixed(2), timeframe: "5m", words: "break of structure up on the 5-minute chart" }],
    trend: [{ timeframe: "1w", direction: "up", words: "weekly: up" }, { timeframe: "1d", direction: "down (weak)", words: "daily: down, weakening" },
            { timeframe: "1h", direction: "range", words: "1-hour: a range" }, { timeframe: "5m", direction: "up", words: "5-minute: up" }],
    next_areas: { above: areas[0], below: areas[1], inside: null },
    divergence: engine || lows.length < 2 ? null : { side: "bullish", role: "demand", timeframe: "5m",
      first: { time: lows[lows.length - 2].time, price: lows[lows.length - 2].price }, second: { time: lows[lows.length - 1].time, price: lows[lows.length - 1].price },
      words: "RSI divergence on the 5-minute chart: a lower swing low with a higher RSI." },
    level: engine || !lows.length ? null : { price: lows[lows.length - 1].price, price_text: lows[lows.length - 1].price.toFixed(2), side: "below",
      words: `The up structure changes beyond ${lows[lows.length - 1].price.toFixed(2)}.` },
    summary: ["Trend - weekly: up; daily: down, weakening; 1-hour: a range; 5-minute: up."],
    not_given: engine ? ["events", "divergence", "level"] : [],
  };
}

/** M75 W-4: a made-up AI card (the server's answer shape; ready, or one plain line when a cap is hit). */
const CARD = { status: "ready", message: null, notes: [], as_of: "2026-10-19T05:25:00+00:00", cached: false,
  disclaimer: "Educational only. It is not investment advice or a recommendation, and ATP places no order from it.",
  card: { story: ["Price pulled back inside the up leg and holds above the buyer area."],
          trend: { side: "up", text: "Up on the 5-minute chart; the daily chart is weakening." },
          current_state: { phase: "pullback", text: "A pullback inside the running leg." },
          rsi: { divergence: "bullish", text: "A lower swing low with a higher RSI." },
          what_to_wait_for: { condition: "A commitment candle at the buyer area." },
          scenarios: { primary: "The leg resumes from the buyer area.", alternative: "The pullback deepens to the lower buyer area." },
          evidence: [{ name: "Higher high and higher low", present: true }, { name: "Commitment candle", present: false }] } };
const LIMIT = { ...CARD, status: "limit", card: null, message: "Limit reached: your plan allows 3 chart analyses a day and you have used them. The code reading above is complete on its own." };

async function open(page: Page, theme: "light" | "dark", source: "atp" | "engine" = "atp", card: Record<string, unknown> = CARD): Promise<{ cards: number }> {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, read_chart: true, copilot_workspace: true }, brokers: CONNECTED });
  await mockCharts(page, {});
  await page.route(/\/api(?:\/v1)?\/charts\/read\?/, (r) => r.fulfill({ json: { ...READING_WITH_AREAS, tf: "5m", annotations: annotations(source) } }));
  const seen = { cards: 0 };
  await page.route(/\/api(?:\/v1)?\/chart-analyze\/workspace$/, (r) => { seen.cards += 1; return r.fulfill({ json: card }); });
  await page.goto("/copilot/read?symbol=NIFTY%2050");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  return seen;
}

const table = (page: Page) => page.locator("[data-workspace-annotations]");

for (const [width, height] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`Copilot workspace annotations ${width} ${theme}`, () => {
      test.use({ viewport: { width, height }, deviceScaleFactor: 1, ...(width < 768 ? { isMobile: true, hasTouch: true } : {}) });

      test(`one reading drawn and tabled, source labelled (${width} ${theme})`, async ({ page }) => {
        const seen = await open(page, theme);
        const ws = page.locator("[data-copilot-workspace='NIFTY 50']");
        await expect(ws.locator("[data-embedded-chart='NIFTY 50'] canvas").first()).toBeVisible({ timeout: 15_000 });
        await expect(table(page)).toHaveAttribute("data-workspace-annotations", "atp", { timeout: 15_000 });
        for (const row of ["trend-1w", "trend-1d", "event", "above", "below", "divergence", "level", "swings"]) {
          await expect(table(page).locator(`[data-row='${row}']`)).toBeVisible();
        }
        await expect(table(page).locator("[data-row='above'] td")).toContainText("seller area 24700.00 - 24780.00");
        await expect(page.locator("[data-workspace-source]")).toContainText("ATP's chart readers");
        // M75 W-4: the AI card started by itself after the code reading - one call
        await expect(page.locator("[data-ai-card='ready']")).toBeVisible({ timeout: 15_000 });
        await expect(page.locator("[data-ai-row='watch']")).toContainText("A commitment candle at the buyer area.");
        await page.waitForTimeout(400);
        expect(seen.cards).toBe(1);
        if (width < 768) await expectPhoneLayout(page);
        await page.waitForTimeout(400);
        if (OUT) {
          await ws.locator("[data-workspace-chart]").screenshot({ path: `${OUT}/after-chart-${width}-${theme}.png`, animations: "disabled" });
          await page.locator("[data-workspace-analysis]").screenshot({ path: `${OUT}/after-table-${width}-${theme}.png`, animations: "disabled" });
          await page.screenshot({ path: `${OUT}/after-${width}-${theme}.png`, animations: "disabled" });
        }
      });
    });
  }
}

test.describe("Copilot workspace AI card: a cap hit", () => {
  test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true });

  test("the plan's limit: one plain line, the code reading stays", async ({ page }) => {
    await open(page, "dark", "atp", LIMIT);
    await expect(page.locator("[data-ai-card='limit']")).toContainText("The code reading above is complete on its own.", { timeout: 15_000 });
    await expect(table(page)).toBeVisible();
    await expectPhoneLayout(page);
    if (OUT) await page.locator("[data-workspace-analysis]").screenshot({ path: `${OUT}/after-limit-390-dark.png`, animations: "disabled" });
  });
});

test.describe("Copilot workspace annotations: the engine source", () => {
  test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true });

  test("an engine reading without BOS / divergence / level says so, never filled from ATP", async ({ page }) => {
    await open(page, "light", "engine");
    await expect(table(page)).toHaveAttribute("data-workspace-annotations", "engine", { timeout: 15_000 });
    await expect(table(page).locator("[data-row='event'] td")).toHaveText("Not given by this source");
    await expect(table(page).locator("[data-row='divergence'] td")).toHaveText("Not given by this source");
    await expect(page.locator("[data-workspace-source]")).toContainText("signal engine");
    await expectPhoneLayout(page);
    if (OUT) await page.locator("[data-workspace-analysis]").screenshot({ path: `${OUT}/after-engine-390-light.png`, animations: "disabled" });
  });
});
