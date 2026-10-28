import { expect, test, type Page, type Route } from "@playwright/test";
import { CLOSING, PRE_MARKET, TODAY_PRE } from "../src/rhythm/fixtures";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { FEATURES, TELEGRAM_PLATFORM, openSettings } from "./settingsMocks";

/**
 * MO4 (Master Order 4 part 1, the daily rhythm; flags ui_v2 + daily_rhythm): the Dashboard's brief card and the
 * "Daily briefs on Telegram" choice under Settings → Alerts.
 * - Baselines `daily-rhythm-v2-<width>-<theme>.png` (the card) at 360, 390 and 1440, light and dark. Every other
 *   Dashboard baseline is untouched: their fixtures do not turn `daily_rhythm` on.
 * - The tabs read the other digests; with the flag off there is no card and no read.
 * Every reply is made-up data (src/rhythm/fixtures.ts): no real account, price or id.
 */

const THEMES = ["light", "dark"] as const;
const SIZES = [{ width: 360, height: 800, phone: true }, { width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }];

async function openDashboard(page: Page, theme: "light" | "dark", flag = true) {
  const reads: string[] = [];
  await page.clock.install({ time: new Date("2026-10-22T08:45:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const features = { ...(F.features as Record<string, boolean>), daily_rhythm: flag };
  await mockApi(page, { features, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { state: "connected", me: true });
  await page.route(api("/rhythm/today"), (route: Route) => {
    const url = new URL(route.request().url());
    reads.push(url.search);
    const kind = url.searchParams.get("kind");
    return route.fulfill({ json: kind === "closing" ? { ...TODAY_PRE, brief: CLOSING } : TODAY_PRE });
  });
  await page.goto("/");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-page='dashboard-v2']")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return reads;
}

for (const size of SIZES) {
  test.describe(`daily brief card ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`looks the same (${theme})`, async ({ page }) => {
        await openDashboard(page, theme);
        const card = page.locator("[data-block='rhythm']");
        await expect(card).toBeVisible();
        await expect(card.getByRole("heading", { name: "Before the open" })).toBeVisible();
        await card.scrollIntoViewIfNeeded();
        await expect(card).toHaveScreenshot(`daily-rhythm-v2-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }

    if (size.phone) {
      test("no sideways scroll, 44 px targets", async ({ page }) => {
        await openDashboard(page, "dark");
        await expect(page.locator("[data-block='rhythm']")).toBeVisible();
        await expectPhoneLayout(page);
      });
    }
  });
}

test.describe("daily brief card behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("opens on the digest of the moment; a tab reads another one", async ({ page }) => {
    const reads = await openDashboard(page, "light");
    const card = page.locator("[data-block='rhythm']");
    for (const s of PRE_MARKET.sections) await expect(card.locator(`[data-rhythm-section='${s.id}']`)).toBeVisible();
    await expect(card.getByRole("tab", { name: /Morning/ })).toHaveAttribute("aria-selected", "true");
    await card.getByRole("tab", { name: "Wrap" }).click();
    await expect(card.getByRole("heading", { name: "Closing wrap" })).toBeVisible();
    // UX-11b: the day's limits as a chip (Inside ✓), not a sentence.
    await expect(card.locator("[data-chip='limits-inside']")).toBeVisible();
    expect(reads).toContain("?kind=closing");
    const text = (await card.innerText()).toLowerCase();
    expect(text).not.toMatch(/\b(buy|sell|target|stop[- ]?loss|stop)\b/);
  });

  test("flag off: no card and no read", async ({ page }) => {
    const reads = await openDashboard(page, "light", false);
    await expect(page.locator("[data-block='rhythm']")).toHaveCount(0);
    expect(reads).toEqual([]);
  });
});

test.describe("Settings → Alerts: daily briefs on Telegram", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  const SETTINGS = (connected: boolean, reaches: boolean, telegram = false) => ({
    prefs: { telegram, pre_market: true, mid_session: false, closing: true, weekly: true }, version: telegram ? 1 : 0,
    telegram: { connected, reaches_you: reaches },
  });

  test("not connected: the switch waits, with one plain next step", async ({ page }) => {
    await openSettings(page, "/settings/alerts", { features: { ...FEATURES, daily_rhythm: true },
      routes: (p) => p.route(/\/api(?:\/v1)?\/rhythm\/settings$/, (r) => r.fulfill({ json: SETTINGS(false, false) })) });
    const box = page.locator("[data-rhythm-telegram]");
    await expect(box.getByRole("checkbox", { name: "Send my daily briefs to Telegram" })).toBeDisabled();
    await expect(box.getByText("Connect Telegram above first.", { exact: false })).toBeVisible();
  });

  test("connected: off by default; turning it on saves and shows the four briefs", async ({ page }) => {
    const puts: unknown[] = [];
    const reply = (r: Route) => {
      if (r.request().method() === "PUT") {
        const body = r.request().postDataJSON() as Record<string, boolean>;
        puts.push(body);
        return r.fulfill({ json: { ...SETTINGS(true, true, body.telegram), prefs: body } });
      }
      return r.fulfill({ json: SETTINGS(true, true) });
    };
    await openSettings(page, "/settings/alerts", { features: { ...FEATURES, daily_rhythm: true }, channels: [TELEGRAM_PLATFORM],
      routes: (p) => p.route(/\/api(?:\/v1)?\/rhythm\/settings$/, reply) });
    const box = page.locator("[data-rhythm-telegram]");
    const main = box.getByRole("checkbox", { name: "Send my daily briefs to Telegram" });
    await expect(main).not.toBeChecked();
    await main.click();
    await expect(main).toBeChecked();
    await expect(box.getByRole("checkbox", { name: /Mid-session pulse/ })).not.toBeChecked();
    await expect(box.getByRole("checkbox", { name: /Closing wrap/ })).toBeChecked();
    expect(puts[0]).toEqual({ telegram: true, pre_market: true, mid_session: false, closing: true, weekly: true });
    await expectPhoneLayout(page);
  });

  test("flag off: nothing here", async ({ page }) => {
    await openSettings(page, "/settings/alerts", { features: FEATURES });
    await expect(page.locator("#telegram")).toBeVisible();
    await expect(page.locator("#daily-briefs")).toHaveCount(0);
  });
});
