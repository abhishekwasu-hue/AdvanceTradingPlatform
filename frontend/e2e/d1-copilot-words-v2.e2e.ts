import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";

/**
 * Day-one fix 5 (docs/design/DAY_ONE_TRADER_TEST.md finding 7): the Copilot answers in a beginner's words. The answer to
 * "Find stocks in a daily uptrend near a demand zone" lists each stage in the reader's plain words (no screen code), its
 * assumption without "the function's default", and its link says "Open Find setups"; the full view of a symbol names a
 * break of structure in words; the market line has no "Dow structure" and no ISO date. The answers are the backend's
 * own rule answers on its synthetic eval fixtures (e2e/fixtures/copilot_tools_v2.json, copilot_chat_v2.json).
 * With D1F_SHOTS=<folder> each step is saved as `<step>-<width>-<theme>.png`; no baseline is stored.
 */

const PACK = (() => {
  const src = readFileSync(new URL("../../backend/app/platform/trader_pack.py", import.meta.url), "utf8");
  const body = /PACK: Tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/.exec(src)?.[1] ?? "";
  return [...body.replace(/#.*$/gm, "").matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
})();
const FEATURES: Record<string, boolean> = Object.fromEntries(PACK.map((f) => [f, true]));
const SHOTS = process.env.D1F_SHOTS;
const T = JSON.parse(readFileSync(new URL("./fixtures/copilot_tools_v2.json", import.meta.url), "utf8")) as { answers: Record<string, unknown> };
const CHAT = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown> };
const QUESTION = "Find stocks in a daily uptrend near a demand zone";
const TOKEN = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null, needs_login: false,
  oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const body = (route: Route): unknown => { try { return route.request().postDataJSON(); } catch { return route.request().postData(); } };

async function mockWorld(page: Page) {
  await mockApi(page, { features: FEATURES, brokers: TOKEN });
  await mockDashboard(page, { state: "empty" });
  const me = { ...(F.me as Record<string, unknown>), role: "USER", scopes: ["trading:write", "alerts:write"] };
  await page.route(api("/auth/me$"), (r) => r.fulfill({ json: me }));
  await page.route(api("/system/features$"), (r) => r.fulfill({ json: FEATURES }));
  await page.route(api("/broker/token-status$"), (r) => r.fulfill({ json: TOKEN }));
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

}

async function settle(page: Page) {
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`Day-one fix 5: Copilot words ${size.width} ${theme}`, () => {
      test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

      test("a scan answer and a full view without code or jargon", async ({ page }) => {
        const shot = async (step: string) => {
          if (SHOTS) await page.screenshot({ path: `${SHOTS}/${step}-${size.width}-${theme}.png`, fullPage: true, animations: "disabled" });
        };
        await page.addInitScript((t) => {
          try {
            localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
            localStorage.setItem("atp_install_dismissed", "1");
          } catch { /* storage unavailable */ }
        }, theme);
        await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
        await mockWorld(page);
        await page.goto("/copilot");
        await settle(page);
        await expect(page.locator("main")).not.toContainText("Dow structure");
        await expect(page.locator("main")).not.toContainText("2026-10-20");

        await page.locator("#copilot-question").fill(QUESTION);
        await page.getByRole("button", { name: "Ask", exact: true }).click();
        const answer = page.locator("[data-answer]").last();
        await expect(answer).toContainText("2 of 4 symbols in My swing list pass this screen");
        await expect(answer).toContainText("The market-structure trend is up: 3 left");
        await expect(answer).toContainText("(the usual setting)");
        await expect(answer.getByRole("link", { name: "Open Find setups" })).toBeVisible();
        for (const code of ["==", "UPTREND", "NearZone(", "function's default", "Screener)"]) await expect(answer).not.toContainText(code);
        await answer.scrollIntoViewIfNeeded();
        await shot("01-copilot-scan-answer");

        await page.locator("#copilot-question").fill("Full view of NIFTY");
        await page.getByRole("button", { name: "Ask", exact: true }).click();
        const view = page.locator("[data-answer]").last();
        await expect(view).toContainText("break past the last swing (a break of structure)");
        await expect(view).not.toContainText("last BOS");
        await view.scrollIntoViewIfNeeded();
        await shot("02-copilot-full-view");
      });
    });
  }
}
