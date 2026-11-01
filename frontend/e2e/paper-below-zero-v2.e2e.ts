import { expect, test } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading } from "./tradingMocks";

// PAPER-NR (owner, 1 Nov): a short practice balance never refuses a PAPER entry, so the balance may go below zero -
// the PAPER account card shows it red with "below zero - practice only" (v2 pages), at phone and desktop width.
const SIZES = [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }];
for (const size of SIZES) {
  test.describe(`PAPER-NR balance below zero ${size.width}`, () => {
    test.use(size.phone ? { viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size });

    test("the balance below zero is red and says so", async ({ page }) => {
      await page.addInitScript(() => {
        try { localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ }
      });
      await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
      await mockApi(page, { features: { ...(T.features as Record<string, boolean>), paper_account: true }, brokers: T.token_status as unknown[] });
      await mockTrading(page, { paperAccount: true });
      const account = { ...(T.paper_account as Record<string, unknown>), balance: -12_450.5, equity: -12_900 };
      await page.route(/\/api(?:\/v1)?\/paper\/account$/, (r) => r.fulfill({ json: account }));
      await page.goto("/positions");
      await page.waitForLoadState("networkidle");
      const note = page.getByTestId("paper-below-zero");
      await expect(note).toHaveText("below zero - practice only");
      await expect(note).toHaveClass(/text-down/);
      await note.scrollIntoViewIfNeeded();
      if (size.phone) await expectPhoneLayout(page);
      if (process.env.PNR_SHOTS) {
        await page.getByTestId("paper-account").screenshot({ path: `${process.env.PNR_SHOTS}/paper-below-zero-${size.width}.png` });
      }
    });
  });
}
