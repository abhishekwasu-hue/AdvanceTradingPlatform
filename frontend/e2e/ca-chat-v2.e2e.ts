import { expect, test, type Page, type Route } from "@playwright/test";
import { fixtureHybrid, fixtureItem, fixtureStatus } from "../src/chartAnalyze/fixtures";
import type { AnalysisItem } from "../src/chartAnalyze/types";
import { fixtureStrategies } from "../src/chartReader/fixtures";
import type { ChatTurn } from "../src/chartReader/types";
import { mockCharts } from "./chartsMocks";
import { pickOption } from "./combobox";
import { drawnChartPng } from "./drawnChart";
import { mockApi } from "./mockApi";

/**
 * CA-CHAT (owner 18 / 19 Nov): on an analysis, Setup -> "My own conditions" -> the trader writes his conditions -> each
 * comes back Met / Not met / Unclear with its evidence, the cost, and its price area drawn on the live chart. Made-up
 * answers only (no AI call, no key). With CA_CHAT_SHOTS=<folder> each size is saved.
 */
const SHOTS = process.env.CA_CHAT_SHOTS;
const PNG = drawnChartPng();
const OWN = "__own_conditions__";

function answerTurn(text: string): ChatTurn {
  return {
    question: text, at: "2026-10-20T05:00:00+00:00", cost_usd: 0.014,
    answers: [
      { condition: "Example: the price is above the last swing low", status: "met", evidence: "Example: the last swing low held and the price rose from it.", price_low: 24080, price_high: 24120 },
      { condition: "Example: volume rises on the up candles", status: "unclear", evidence: "Example: volume is not drawn on this chart.", price_low: null, price_high: null },
      { condition: "Example: a wide candle closed above the area", status: "not_met", evidence: "Example: the last candles have small bodies.", price_low: null, price_high: null },
    ],
  };
}

async function open(page: Page) {
  await page.clock.install({ time: new Date("2026-10-20T10:30:00+05:30") });
  await page.addInitScript(() => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* none */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, chart_analyze: true, chart_analyze_hybrid: true, chart_reader: true } });
  await mockCharts(page, { market: "closed" });
  const h = fixtureHybrid();
  const item: AnalysisItem = { ...fixtureItem(8, null), mode: "hybrid", status: "done", result: null, hybrid: h, label: h.label,
    summary: { setup_status: null, trend: "up", symbol: h.reading.symbol } };
  await page.route(/\/api(?:\/v1)?\/chart-analyze\/.*/, async (route: Route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api(?:\/v1)?\/chart-analyze/, "");
    if (path === "/status") return route.fulfill({ json: fixtureStatus({ modes: { image_only: true, hybrid: true } }) });
    if (path === "/items") return route.fulfill({ json: { items: [item] } });
    if (path === "/items/8") return route.fulfill({ json: item });
    if (/\/items\/8\/(image|reading\.png)$/.test(path)) return route.fulfill({ body: PNG, contentType: "image/png" });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  const posts: string[] = [];
  const turns: ChatTurn[] = [];
  await page.route(/\/api(?:\/v1)?\/chart-reader\/strategies$/, (route: Route) =>
    route.fulfill({ json: fixtureStrategies({ own_method: false, default: "DEMO_ONE", strategies: fixtureStrategies().strategies.slice(1) }) }));
  await page.route(/\/api(?:\/v1)?\/chart-reader\/items\/8\/chat$/, async (route: Route) => {
    if (route.request().method() === "POST") {
      const text = (route.request().postDataJSON() as { text: string }).text;
      posts.push(text);
      const turn = answerTurn(text);
      turns.push(turn);
      return route.fulfill({ json: { turn, turns: turns.length, estimate_usd: 0.02 } });
    }
    return route.fulfill({ json: { turns, max_turns: 30, estimate_usd: 0.02 } });
  });
  await page.goto("/chart-analyze");
  await page.getByRole("region", { name: "Your analyses" }).getByRole("button", { name: new RegExp(h.reading.symbol) }).click();
  await expect(page.locator("[data-figure='atp'] canvas").first()).toBeVisible({ timeout: 15_000 });
  return { posts };
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  test.describe(`CA-CHAT ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: SHOTS ? 2 : 1 });

    test("Setup -> My own conditions -> each condition answered on this chart, with its cost and area", async ({ page }) => {
      const { posts } = await open(page);
      const setup = page.getByTestId("reader-books");
      await expect(setup.getByRole("heading", { name: "Setup" })).toBeVisible();
      await pickOption(page, page.getByTestId("reader-book-pick"), OWN);
      await expect(page.getByTestId("reader-book-read")).toHaveCount(0);           // no strategy read for own conditions
      const chat = page.getByTestId("condition-chat");
      await expect(chat.getByTestId("chat-send")).toBeDisabled();
      await chat.getByTestId("chat-input").fill("Price above the last swing low; volume rising; a wide candle closed above");
      await chat.getByTestId("chat-send").click();
      await expect(chat.locator("[data-chat-turn]")).toHaveCount(1);
      expect(posts).toEqual(["Price above the last swing low; volume rising; a wide candle closed above"]);
      for (const s of ["met", "unclear", "not_met"]) await expect(chat.locator(`[data-chat-answer='${s}']`)).toHaveCount(1);
      await expect(chat.locator("[data-chat-range]")).toContainText("Q1.1");
      await expect(chat.locator("[data-chat-cost]")).toContainText("Cost $0.01");
      await expect(chat.getByTestId("chat-input")).toHaveValue("");
      const text = await setup.innerText();                                     // the Setup block and its chat
      expect(text.match(/\b(buy|sell|target|stop[- ]?loss)\b/i)?.[0] ?? null).toBeNull();
      const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(over).toBeLessThanOrEqual(0);
      if (SHOTS) {
        await setup.scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${SHOTS}/ca-chat-${size.width}.png`, fullPage: true });
        await setup.screenshot({ path: `${SHOTS}/ca-chat-setup-${size.width}.png` });
      }
    });
  });
}
