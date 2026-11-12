import { expect, test } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading } from "./tradingMocks";

// Owner, 12 Nov (phone screenshot, My trades > Paper account and tools): the PAPER account figures broke inside the
// number ("9,89,107.3 / 8") and the net P&L ran past the card edge; the ladder said "LIVE (approval-only) · not yet
// enabled". Each figure stays on ONE line inside its tile at 360 / 390 / 1440, and the ladder's LIVE step follows the ONE
// `live_trading` switch (LIVE-1). Made-up figures of the screenshot's length.
const SIZES = [
  { width: 360, height: 780, phone: true }, { width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false },
];
const ACCOUNT = { ...(T.paper_account as Record<string, unknown>), starting_capital: 1_000_000, balance: 989_107.38, equity: 987_412.9,
  realised: { gross: -9_100.1, charges: 1_792.5, net: -10_892.6 }, unrealised: -1_694.48, closed_trades: 41, open_positions: 2 };
const LADDER = { ...(T.paper_ladder as Record<string, unknown>), stages: [
  { key: "PAPER", label: "PAPER", status: "current" }, { key: "LIVE", label: "LIVE", status: "switch" }] };

for (const size of SIZES) {
  for (const live of [false, true]) {
    test.describe(`PAPER account card ${size.width} live_trading ${live ? "on" : "off"}`, () => {
      test.use(size.phone ? { viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size });

      test("figures on one line inside their tiles; the LIVE step says what the switch does", async ({ page }) => {
        await page.addInitScript(() => {
          try { localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ }
        });
        await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
        const features = { ...(T.features as Record<string, boolean>), paper_account: true, live_trading: live, phase0_paper_only: !live };
        await mockApi(page, { features, brokers: T.token_status as unknown[] });
        await mockTrading(page, { paperAccount: true });
        await page.route(/\/api(?:\/v1)?\/paper\/account$/, (r) => r.fulfill({ json: ACCOUNT }));
        await page.route(/\/api(?:\/v1)?\/paper\/ladder$/, (r) => r.fulfill({ json: LADDER }));
        await page.goto("/positions");
        await page.waitForLoadState("networkidle");
        const card = page.getByTestId("paper-account");
        await card.scrollIntoViewIfNeeded();
        await expect(page.getByTestId("paper-tile-value").first()).toHaveText("₹9,89,107.38");

        const report = await card.evaluate((root) => {
          const box = root.getBoundingClientRect();
          return [...root.querySelectorAll<HTMLElement>("[data-testid=paper-tile-value]")].map((v) => {
            const r = v.getBoundingClientRect();
            const tile = v.closest<HTMLElement>("[data-testid=paper-tile]")!.getBoundingClientRect();
            const line = parseFloat(getComputedStyle(v).lineHeight);
            return { text: v.textContent ?? "", oneLine: r.height < line * 1.5, fitsText: v.scrollWidth <= v.clientWidth + 1,
              inTile: r.right <= tile.right + 1 && r.left >= tile.left - 1, inCard: tile.right <= box.right + 1 };
          });
        });
        for (const v of report) {
          expect(v.oneLine, `${v.text} on one line`).toBe(true);
          expect(v.fitsText, `${v.text} not cut off`).toBe(true);
          expect(v.inTile, `${v.text} inside its tile`).toBe(true);
          expect(v.inCard, `${v.text} tile inside the card`).toBe(true);
        }
        await expect(page.getByTestId("paper-tile-value").nth(2)).toHaveText(/−₹10,892\.60/);
        await expect(page.getByTestId("paper-tile-value").nth(2).locator("[data-signed=down]")).toHaveClass(/text-down/);

        const step = page.getByTestId("ladder-live");
        await expect(step).toHaveText(live ? "Live trading available - choose LIVE on any order" : "Live trading is switched off by the admin");
        await expect(page.getByTestId("paper-ladder")).not.toContainText("approval-only");
        await expect(page.getByTestId("paper-ladder")).not.toContainText("not yet enabled");
        if (size.phone) await expectPhoneLayout(page);
      });
    });
  }
}
