import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { mockCopilotNumbers } from "./copilotMocks";

/**
 * M75 NAV-2 (docs/design/M75_NO_NAVIGATION.md, the Copilot rows): the Copilot's buttons open in place, on made-up
 * replies. Each converted button is clicked; the URL PATH stays the same while its panel is open, and closing the panel
 * returns to the same state (the answer and the half-typed question are still there, the address is as before).
 * Phone 390 (bottom sheet) and desktop 1440 (side panel). `INPLACE_SHOTS=<dir>` writes evidence screenshots there.
 */

const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as {
  home: Record<string, unknown>; answers: Record<string, unknown>;
};
const SHOTS = process.env.INPLACE_SHOTS;
/** Made-up: one connected broker (so no "connect your broker" prompt opens over the page). */
const UPSTOX = { broker: "upstox", account_label: "primary", name: "Upstox", status: "connected",
  message: "Connected for market data, charts, the scanner, the option chain and paper trading.", data_only: true, renew: false };
/** Made-up: its broker session (the older Copilot tools sit behind the broker gate, in a panel too). */
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

async function open(page: Page, path = "/copilot") {
  await page.clock.install({ time: new Date("2026-10-21T09:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, read_chart: true }, brokers: CONNECTED, connections: [UPSTOX] });
  await mockCopilotNumbers(page);
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, (route) => {
    const request = route.request();
    if (request.url().endsWith("/history")) return route.fulfill({ json: { turns: [], keep: 20 } });
    if (request.method() === "GET") return route.fulfill({ json: F.home });
    const message = (request.postDataJSON() as { message: string }).message;
    return route.fulfill({ json: F.answers[message] ?? F.answers["Should I buy RELIANCE today?"] });
  });
  await page.goto(path);
  await page.waitForLoadState("networkidle");
}

const panel = (page: Page, kind: string) => page.locator(`[data-inplace-panel='${kind}']`);
const question = (page: Page) => page.locator("#copilot-question");
const path = (page: Page) => new URL(page.url()).pathname;
const search = (page: Page) => new URL(page.url()).search;

async function ask(page: Page, q: string) {
  await expect(page.locator("[data-page='copilot-v2']")).toBeVisible({ timeout: 15_000 });
  await question(page).fill(q);
  await question(page).press("Enter");
  await expect(page.locator("[data-turn] [data-answer]").last()).toBeVisible();
}

/** Closes the top panel with Escape and checks the tab came back as it was. */
async function closeBack(page: Page, kind: string, at: string) {
  await page.keyboard.press("Escape");
  await expect(panel(page, kind)).toHaveCount(0);
  expect(path(page)).toBe(at);
  expect(search(page)).toBe("");
}

for (const size of [{ width: 390, height: 844, shape: "bottom" }, { width: 1440, height: 900, shape: "right" }] as const) {
  test.describe(`Copilot buttons open in place ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, deviceScaleFactor: 1 });

    test("Open chart (the answer's chart): the chart beside the chat; closing keeps the answer and the typed question", async ({ page }) => {
      await open(page);
      await ask(page, "Read the SENSEX chart");
      await question(page).fill("half-typed question");
      await page.locator("[data-turn] a[data-action='chart']").click();
      await expect(panel(page, "chart")).toBeVisible();
      await expect(panel(page, "chart")).toHaveAttribute("data-inplace-shape", size.shape);
      expect(path(page)).toBe("/copilot");
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/nav2-chart-${size.width}.png` });
      await closeBack(page, "chart", "/copilot");
      await expect(page.locator("[data-turn]")).toHaveCount(1);
      await expect(question(page)).toHaveValue("half-typed question");
    });

    test("the answer's action (Open the trade coach) opens the Coach in place", async ({ page }) => {
      await open(page);
      await ask(page, "Review my last trade");
      await page.locator("[data-turn] a[data-action='page']").click();
      await expect(panel(page, "copilot").locator("[data-copilot-folded='coach']")).toBeVisible({ timeout: 15_000 });
      expect(path(page)).toBe("/copilot");
      await closeBack(page, "copilot", "/copilot");
      await expect(page.locator("[data-turn]")).toHaveCount(1);
    });

    test("Connect (no AI key) opens the AI key in place", async ({ page }) => {
      await open(page);
      await expect(page.locator("[data-no-key] [data-connect]")).toBeVisible({ timeout: 15_000 });
      await page.locator("[data-no-key] [data-connect]").click();
      await expect(panel(page, "ai-key")).toBeVisible();
      await expect(panel(page, "ai-key")).toHaveAttribute("data-inplace-shape", "modal");
      expect(path(page)).toBe("/copilot");
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/nav2-ai-key-${size.width}.png` });
      await closeBack(page, "ai-key", "/copilot");
    });

    test("Advanced: an older Copilot tool opens beside the chat (its own body, no second tab bar)", async ({ page }) => {
      await open(page);
      await ask(page, "Read the NIFTY chart");
      await page.locator("[data-details='copilot.chat.advanced'] summary").click();
      await page.locator("#copilot-more ~ ul a[href='/copilot/market-pulse']").click();
      const tool = panel(page, "copilot");
      await expect(tool.locator("[data-copilot-tool='market-pulse']")).toBeVisible({ timeout: 15_000 });
      await expect(tool.getByTestId("tab-coach")).toHaveCount(0);
      expect(path(page)).toBe("/copilot");
      expect(new URL(page.url()).searchParams.getAll("panel")).toEqual(["copilot:market-pulse"]);
      if (SHOTS) { await page.waitForTimeout(1500); await page.screenshot({ path: `${SHOTS}/nav2-tool-${size.width}.png` }); }   // after the cards' entry motion
      await closeBack(page, "copilot", "/copilot");
      await expect(page.locator("[data-turn]")).toHaveCount(1);
    });

    test("Ask AI on another page: Ask the Copilot opens the chat in place (the composer inside the panel)", async ({ page }) => {
      await open(page, "/charts/NIFTY");
      await page.locator("[data-ask-ai-open]").click();
      await page.getByRole("dialog", { name: "Ask AI" }).locator("[data-ask-option='copilot']").click();
      const chat = panel(page, "copilot");
      await expect(chat.locator("[data-page='copilot-v2']")).toBeVisible({ timeout: 15_000 });
      await expect(chat.locator("[data-page='copilot-v2']")).toHaveAttribute("data-placement", "inline");
      expect(path(page)).toBe("/charts/NIFTY");
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/nav2-ask-ai-${size.width}.png` });
      await closeBack(page, "copilot", "/charts/NIFTY");
    });

    test("a deep link to the full route still shows the full page", async ({ page }) => {
      await open(page, "/copilot/market-pulse");
      await expect(page.locator("[data-testid='copilot-page']")).toBeVisible({ timeout: 15_000 });
      await expect(page.locator("[data-inplace-panel]")).toHaveCount(0);
    });
  });
}
