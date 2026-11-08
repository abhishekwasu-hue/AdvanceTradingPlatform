import { expect, test } from "@playwright/test";
import { mockApi } from "./mockApi";

/**
 * M85 (owner): More › All pages lists the Journal with its icon; tapping it opens My trades › Results › Journal
 * (/positions/results/journal) in the same tab, and the sheet's page search finds it by "journal".
 * The shots go to the M85 compare page (not baselines): set M85_SHOTS to a folder to write them.
 */
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

test.describe("M85 Journal in More", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("More › All pages shows the Journal; search finds it; a tap opens the journal address here", async ({ page }) => {
    await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
    await mockApi(page, { features: { ui_v2: true, discipline_engine: true }, brokers: CONNECTED });
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: "More pages" }).click();
    const sheet = page.getByRole("dialog", { name: "All pages" });
    await expect(sheet).toBeVisible();
    if (process.env.M85_SHOTS) await page.screenshot({ path: `${process.env.M85_SHOTS}/${process.env.M85_TAG ?? "after"}-390.png` });
    const tile = sheet.getByRole("link", { name: /^Journal/ });
    await expect(tile).toBeVisible();
    await expect(tile.locator("svg").first()).toBeVisible();                       // its icon
    await sheet.getByRole("searchbox", { name: "Search pages" }).fill("journal");
    await expect(sheet.getByRole("link", { name: /^Journal/ }).first()).toBeVisible();
    if (process.env.M85_SHOTS) await page.screenshot({ path: `${process.env.M85_SHOTS}/after-390-search.png` });
    await sheet.getByRole("link", { name: /^Journal/ }).first().click();
    await expect(page).toHaveURL(/\/positions\/results\/journal$/);
    await expect(page.getByRole("dialog", { name: "All pages" })).toHaveCount(0);
  });
});
