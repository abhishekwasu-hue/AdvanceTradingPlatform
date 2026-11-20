import { expect, test } from "@playwright/test";
import { mockApi } from "./mockApi";

/**
 * Owner 20 Nov ("live trading cha issue tumhi solve kela nahi ka", screenshot: "Broker state uncertain since ... LIVE
 * entries blocked until position reconciliation passes"): the LIVE pause after a failed LIVE order shows in plain
 * words on My positions with ONE action - check with the broker now; a clean check opens LIVE again.
 */
const SHOTS = process.env.BROKER_CHECK_SHOTS;
const UPSTOX = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  is_default: true, capabilities: {}, needs_login: false }];

test.describe("the LIVE pause and its one way out (390)", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("paused -> check with Upstox -> the books agree -> LIVE open again", async ({ page }) => {
    let paused = true;
    const posts: string[] = [];
    await mockApi(page, { features: { ui_v2: true, live_trading: true, phase0_paper_only: false }, brokers: UPSTOX });
    await page.route(/\/api(?:\/v1)?\/reconciliation\/status$/, (r) => r.fulfill({ json: {
      broker_uncertain: paused, broker_uncertain_since: paused ? "2026-11-19T05:18:59+00:00" : null,
      broker_uncertain_reason: paused ? "Broker state uncertain since 2026-11-19T05:18:59+00:00 (order 4 FAILED: The \"product\" is invalid)" : null,
      last_reconciled_at: null, open_live_trades: 0 } }));
    await page.route(/\/api(?:\/v1)?\/reconciliation\/upstox$/, (r) => {
      posts.push(r.request().method());
      paused = false;
      return r.fulfill({ json: { broker_name: "upstox", mismatched_count: 0, items: [], accounts: ["primary"], checked_at: "2026-11-20T09:00:00Z" } });
    });
    await page.goto("/positions");
    const card = page.locator("[data-broker-check='paused']");
    await expect(card).toContainText("New LIVE orders are paused");
    await expect(card).toContainText("Exits keep running.");
    await expect(card).not.toContainText("reconciliation");                     // no developer words outside Details
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/broker-check-paused-390.png` });
    await card.locator("[data-broker-check-run='upstox']").click();
    await expect(page.locator("[data-broker-check='cleared']")).toContainText("LIVE orders are open again");
    expect(posts).toEqual(["POST"]);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/broker-check-cleared-390.png` });
  });
});
