import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { REPORT } from "../src/dailyReport/fixture";
import { mockCopilotNumbers } from "./copilotMocks";
import { mockApi } from "./mockApi";

/**
 * M88-R: the Daily market report card in the Copilot's Read the chart tab and its full report in place (a sheet, no
 * navigation away), phone 390 and desktop, dark. Mocked API only (the stored report, no AI call). M88R_REPORT=<json>
 * shows another stored report (the proof run); M88R_SHOTS=<dir> writes the proof shots.
 */
const SHOTS = process.env.M88R_SHOTS;
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const REPORT_SHOWN = process.env.M88R_REPORT ? JSON.parse(readFileSync(process.env.M88R_REPORT, "utf8")) : REPORT;

for (const size of [{ name: "phone", width: 390, height: 844, mobile: true }, { name: "desktop", width: 1440, height: 900, mobile: false }]) {
  test(`daily report card and sheet (${size.name})`, async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile,
      deviceScaleFactor: SHOTS ? 2 : 1, serviceWorkers: "block", colorScheme: "dark" });
    const page = await context.newPage();
    await page.addInitScript(() => {
      try {
        localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
        localStorage.setItem("atp_install_dismissed", "1");
      } catch { /* intentional: storage unavailable in this browser */ }
    });
    await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
    await mockApi(page, { features: { ui_v2: true, ai_copilot: true, shared_daily_report: true }, brokers: CONNECTED });
    await mockCopilotNumbers(page);
    const reads: string[] = [];
    await page.route(/\/api(?:\/v1)?\/daily-report\/latest$/, (r) => { reads.push(r.request().url()); return r.fulfill({ json: { report: REPORT_SHOWN } }); });
    await page.goto("/copilot/read");
    const card = page.locator("[data-daily-report]");
    await expect(card).toBeVisible();
    await expect(card).toContainText("Daily market report");
    await expect(card.locator("[data-report-info]")).toHaveText(REPORT_SHOWN.info_line);
    await card.scrollIntoViewIfNeeded();
    if (SHOTS) await card.screenshot({ path: `${SHOTS}/card-${size.name}.png` });
    const before = page.url();
    await card.getByRole("button", { name: "Open the full report" }).click();
    const sheet = page.getByRole("dialog", { name: "Daily market report" });
    await expect(sheet).toBeVisible();
    const rows = sheet.locator(size.mobile ? "[data-report-card-row]" : "[data-report-row]");
    await expect(rows).toHaveCount(REPORT_SHOWN.stocks.length);
    expect(page.url()).toBe(before);                                     // in place: the tab never changed
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    if (SHOTS) {
      await page.screenshot({ path: `${SHOTS}/sheet-${size.name}.png` });
      await rows.first().scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${SHOTS}/sheet-stocks-${size.name}.png` });
    }
    expect(reads.length).toBe(1);
    await context.close();
  });
}
