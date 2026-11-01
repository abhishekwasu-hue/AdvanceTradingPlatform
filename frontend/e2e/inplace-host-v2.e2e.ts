import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { mockCopilotNumbers } from "./copilotMocks";

/**
 * M75 NAV-1: the in-place host (docs/design/M75_NO_NAVIGATION.md section 4) in a real browser, on made-up replies.
 * NAV-1 converts no button yet, so a panel is opened the two ways the host reads it: a deep link (`?panel=` in the
 * address) and an in-app history entry on the same path (what `useInPlace().open()` pushes).
 * - The URL PATH stays the same while a panel is open; Back / Escape close it; the tab underneath keeps its scroll and
 *   its half-typed question (it is never unmounted).
 * - Focus is trapped in the panel and Escape closes only the top layer of a stack.
 * - Phone: a bottom sheet; desktop: a side panel. Opening the full route directly still shows the full page.
 * `INPLACE_SHOTS=<dir>` writes phone 390 and desktop 1440 screenshots there (evidence only, no baselines).
 */

const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown> };
const SHOTS = process.env.INPLACE_SHOTS;
/** Made-up: one connected broker (so no "connect your broker" prompt opens over the page). */
const UPSTOX = { broker: "upstox", account_label: "primary", name: "Upstox", status: "connected",
  message: "Connected for market data, charts, the scanner, the option chain and paper trading.", data_only: true, renew: false };

async function openCopilot(page: Page, path = "/copilot") {
  await page.clock.install({ time: new Date("2026-10-21T09:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true }, brokers: [], connections: [UPSTOX] });
  await mockCopilotNumbers(page);
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, (r) =>
    r.fulfill({ json: r.request().url().endsWith("/history") ? { turns: [], keep: 20 } : F.home }));
  await page.goto(path);
  await page.waitForLoadState("networkidle");
}

/** What `useInPlace().open()` does: one history entry on the SAME path with `?panel=` (the router reads it on popstate). */
async function pushPanel(page: Page, value: string) {
  await page.evaluate((v) => {
    const s = (window.history.state ?? {}) as { usr?: Record<string, unknown>; idx?: number };
    const usr = { ...(s.usr ?? {}), atpInPlace: Number(s.usr?.atpInPlace ?? 0) + 1 };
    const q = new URLSearchParams(window.location.search);
    q.append("panel", v);
    window.history.pushState({ usr, key: `p${Date.now()}`, idx: (s.idx ?? 0) + 1 }, "", `${window.location.pathname}?${q.toString()}`);
    window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
  }, value);
}

const panel = (page: Page, kind: string) => page.locator(`[data-inplace-panel='${kind}']`);
const question = (page: Page) => page.locator("[data-page='copilot-v2'] textarea").first();

for (const size of [{ name: "phone", width: 390, height: 844, shape: "bottom" }, { name: "desktop", width: 1440, height: 900, shape: "right" }] as const) {
  test.describe(`in-place host ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, deviceScaleFactor: 1 });

    test("the path stays the same while a panel is open; Back closes it; the tab keeps its scroll and typed text", async ({ page }) => {
      await openCopilot(page);
      await expect(page.locator("[data-page='copilot-v2']")).toBeVisible({ timeout: 15_000 });
      await question(page).fill("half-typed question");
      await expect(question(page)).toHaveValue("half-typed question");
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      const scrollBefore = await page.evaluate(() => window.scrollY);

      await pushPanel(page, "broker");
      await expect(panel(page, "broker")).toBeVisible();
      await expect(panel(page, "broker")).toHaveAttribute("data-inplace-shape", size.shape);
      expect(new URL(page.url()).pathname).toBe("/copilot");
      expect(new URL(page.url()).searchParams.getAll("panel")).toEqual(["broker"]);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/inplace-broker-${size.width}.png` });

      await page.goBack();
      await expect(panel(page, "broker")).toHaveCount(0);
      expect(new URL(page.url()).pathname).toBe("/copilot");
      expect(new URL(page.url()).search).toBe("");
      await expect(question(page)).toHaveValue("half-typed question");
      expect(await page.evaluate(() => window.scrollY)).toBe(scrollBefore);
    });

    test("focus is trapped; Escape closes only the top layer; focus and the address come back", async ({ page }) => {
      await openCopilot(page);
      await expect(page.locator("[data-page='copilot-v2']")).toBeVisible({ timeout: 15_000 });
      await question(page).focus();
      await pushPanel(page, "chart:NIFTY:15min");
      await expect(panel(page, "chart")).toBeVisible();
      await pushPanel(page, "broker");
      await expect(panel(page, "broker")).toBeVisible();
      expect(new URL(page.url()).searchParams.getAll("panel")).toEqual(["chart:NIFTY:15min", "broker"]);

      for (let i = 0; i < 8; i += 1) {
        await page.keyboard.press("Tab");
        const inside = await page.evaluate(() => !!document.activeElement?.closest("[role='dialog']")?.querySelector("[data-inplace-panel='broker']"));
        expect(inside).toBe(true);
      }
      await page.keyboard.press("Escape");
      await expect(panel(page, "broker")).toHaveCount(0);
      await expect(panel(page, "chart")).toBeVisible();
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/inplace-chart-${size.width}.png` });
      expect(new URL(page.url()).searchParams.getAll("panel")).toEqual(["chart:NIFTY:15min"]);
      await page.keyboard.press("Escape");
      await expect(panel(page, "chart")).toHaveCount(0);
      expect(new URL(page.url()).pathname).toBe("/copilot");
      expect(new URL(page.url()).search).toBe("");
      await expect.poll(() => page.evaluate(() => document.activeElement?.tagName)).toBe("TEXTAREA");
    });

    test("a deep link with ?panel= opens the panel on load; closing it keeps the path", async ({ page }) => {
      await openCopilot(page, "/copilot?panel=broker");
      await expect(panel(page, "broker")).toBeVisible({ timeout: 15_000 });
      await expect(page.locator("[data-page='copilot-v2']")).toBeAttached();
      await page.locator("[role='dialog']").getByRole("button", { name: "Close" }).first().click();
      await expect(panel(page, "broker")).toHaveCount(0);
      expect(new URL(page.url()).pathname).toBe("/copilot");
      expect(new URL(page.url()).search).toBe("");
    });

    test("the full route opened directly still shows the full page", async ({ page }) => {
      await openCopilot(page, "/broker");
      await expect(page.locator("[data-broker-page]")).toBeVisible({ timeout: 15_000 });
      await expect(page.locator("[data-inplace-panel]")).toHaveCount(0);
    });
  });
}
