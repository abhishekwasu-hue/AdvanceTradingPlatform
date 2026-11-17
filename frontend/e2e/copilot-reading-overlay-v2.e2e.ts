import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { madeUpBars, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { mockCopilotNumbers } from "./copilotMocks";

/**
 * Owner 15 Nov ("Copilot chart war analysis anotation karun dile pahije" - the Copilot must annotate its analysis on
 * the chart): a chart answer draws its own reading on its mini chart and on the larger chart "Open chart" opens - the
 * protected levels per timeframe, the last break at its bar, the areas as bands and the trend per timeframe in a legend
 * - with one "ATP reading" toggle (on by default). With no area found, the headline says so in a plain line.
 * `READING_SHOTS=<dir>` writes the evidence screenshots; `READING_STAGE=before` runs the journey on the build before
 * this change (evidence only).
 */
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown>; answers: Record<string, Record<string, unknown>> };
const SHOTS = process.env.READING_SHOTS;
const BEFORE = process.env.READING_STAGE === "before";
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

/** Made-up: a reading placed on the made-up daily candles (their price range and one of their bars). */
function answer() {
  const bars = madeUpBars("day");
  const highs = bars.map((b) => b.high), lows = bars.map((b) => b.low);
  const top = Math.max(...highs), bottom = Math.min(...lows), span = top - bottom;
  const at = (f: number) => Math.round((bottom + span * f) * 20) / 20;
  const breakBar = bars[Math.floor(bars.length / 2)];
  const base = F.answers["Read the NIFTY chart"];
  return {
    ...base,
    headline: { ...(base.headline as Record<string, unknown>), price: at(0.5), trend: "sideways", weak: false, area: null },
    structure_card: {
      symbol: "NIFTY", price: at(0.5), timeframe: "Daily",
      timeframes: [
        { tf: "1w", label: "Weekly", ready: true, trend: "down", weak: true, protected: at(0.8),
          last_break: { label: "Break against the trend", price: at(0.8), time: Date.parse(breakBar.timestamp) / 1000, direction: "down" } },
        { tf: "1d", label: "Daily", ready: true, trend: "sideways", weak: false, protected: null, last_break: null },
        { tf: "1h", label: "1-hour", ready: false, trend: null, weak: false, protected: null, last_break: null },
        { tf: "15m", label: "15-minute", ready: true, trend: "up", weak: false, protected: at(0.25), last_break: null },
      ],
      areas_above: [{ side: "supply", from: at(0.62), to: at(0.68), where: "above", distance: 10, kind: "band", timeframe: "1w", state: "fresh", context: true }],
      areas_below: [{ side: "demand", from: at(0.32), to: at(0.36), where: "below", distance: 10, kind: "band", timeframe: "1w", state: "tested", context: true }],
      swing_levels: [],
      watch: "How the price behaves when it reaches the nearest area, and which side of it the next bar closes on.",
    },
  };
}

async function open(page: Page) {
  await page.clock.install({ time: new Date("2026-10-21T09:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, read_chart: true }, brokers: CONNECTED });
  await mockCopilotNumbers(page);
  await mockCharts(page, { market: "closed" });
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, (route) => {
    const request = route.request();
    if (request.url().endsWith("/history")) return route.fulfill({ json: { turns: [], keep: 20 } });
    if (request.method() === "GET") return route.fulfill({ json: F.home });
    return route.fulfill({ json: answer() });
  });
  await page.goto("/copilot");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; transition: none !important; animation: none !important; }" });
}

for (const size of [{ name: "1440", width: 1440, height: 900, phone: false }, { name: "390", width: 390, height: 844, phone: true }] as const) {
  test.describe(`owner 15 Nov the answer's reading on its chart ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

    test("levels, the break, the areas and the trend legend on the answer chart and on Open chart; one toggle", async ({ page }) => {
      await open(page);
      await expect(page.locator("[data-page='copilot-v2']")).toBeVisible({ timeout: 15_000 });
      await page.locator("#copilot-question").fill("Read the NIFTY chart");
      await page.locator("#copilot-question").press("Enter");
      const turn = page.locator("[data-turn] [data-answer]").last();
      const mini = turn.locator("[data-mini-chart] [data-embedded-chart]");
      await expect(mini.locator("canvas").first()).toBeVisible({ timeout: 15_000 });
      await page.waitForTimeout(500);
      const below = turn.locator("[data-mini-chart]");
      await below.evaluate((el) => el.scrollIntoView({ block: "center" }));
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/${BEFORE ? "before" : "after"}-answer-${size.name}.png` });
      await page.locator("[data-turn] a[data-action='chart']").last().click();
      const sheet = page.locator("[data-inplace-panel='chart']");
      await expect(sheet.locator("canvas").first()).toBeVisible({ timeout: 15_000 });
      await page.waitForTimeout(500);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/${BEFORE ? "before" : "after"}-open-chart-${size.name}.png` });
      if (BEFORE) return;
      // the sheet carries the same reading, its toggle on
      await expect(sheet.getByTestId("atp-reading-legend")).toContainText("Weekly ▼ Down · weak");
      await page.keyboard.press("Escape");
      await expect(sheet).toHaveCount(0);
      // the answer chart: the headline's plain no-area line, the legend in the answer's words, the toggle on, then off
      await expect(turn.locator("[data-headline-area='none']")).toHaveText("No area found on the daily chart yet");
      const legend = turn.getByTestId("atp-reading-legend");
      await expect(legend).toContainText("Weekly ▼ Down · weak");
      await expect(legend).toContainText("Daily Sideways");
      await expect(legend).toContainText("15-minute ▲ Up");
      await expect(legend).not.toContainText("1-hour");
      expect(await legend.innerText()).not.toMatch(/\b(buy|sell|target|stop)\b/i);
      const toggle = turn.getByTestId("atp-reading-button");
      if (await toggle.isVisible()) {
        await expect(toggle).toHaveAttribute("aria-pressed", "true");
        await toggle.click();
      } else {
        // the phone's compact bar folds the toggle into its More menu
        await turn.locator("[data-embedded-chart] [aria-label='More chart controls']").first().click();
        await page.getByTestId("atp-reading-button").click();
      }
      await expect(legend).toHaveCount(0);
      const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(sideways).toBeLessThanOrEqual(1);
    });
  });
}
