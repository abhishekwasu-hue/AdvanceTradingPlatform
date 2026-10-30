import { expect, test, type Page } from "@playwright/test";
import { DEMO_ENTRY, DEMO_LIST, DEMO_STATS, DEMO_WEEK } from "../src/journal/journal.fixture";
import { mockApi } from "./mockApi";
import { UPSTOX } from "./settingsMocks";

/**
 * M62 JN-3 / JN-4 / JN-5 (`ui_v2` + `discipline_engine`), on made-up answers (src/journal/journal.fixture.ts): the
 * Journal page at 390 and 1440 - the list first, the entry editor sheet, the outside-ATP form, the Stats tab, the
 * weekly review and the CSV export, with no sideways scroll. `JOURNAL_SHOTS=<dir>` also saves each screen as a PNG
 * there (the before / after comparison; nothing is committed).
 */

const SIZES = [{ width: 390, height: 844 }, { width: 1440, height: 900 }];
const FEATURES = { ui_v2: true, discipline_engine: true, phase0_paper_only: true };
const SHOTS = process.env.JOURNAL_SHOTS;
// A 4 x 3 PNG (a made-up picture): the chart snapshots and the attachment.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAGklEQVR4nGNgYPj/n4GBgYGJAQIYGRkZGBgAJMgDA2QnUGMAAAAASUVORK5CYII=", "base64");
const SETTINGS = {
  settings: { revenge_window_minutes: 30, revenge_size_multiple: 1.5, fomo_chase_r: 0.25, overtrading_daily_norm: 0, overtrading_lookback_days: 20,
    overtrading_multiple: 2, overtrading_min_norm: 3, event_kinds: [], count_unconfirmed: false, weights: {}, custom_tags: ["late entry", "fear", "moved too fast"],
    checklist_rules: ["Trend agrees", "Waited for the close", "Size from my settings"], attachment_max_mb: 5 },
  defaults: {}, ranges: { attachment_max_mb: [0.5, 8] }, components: {}, tags: [],
};

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false, animations: "disabled" });
}

async function noSidewaysScroll(page: Page) {
  const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(r.scroll, "page wider than the screen").toBeLessThanOrEqual(r.width);
}

async function openJournal(page: Page) {
  await page.clock.install({ time: new Date("2032-03-17T18:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["journal"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: FEATURES, brokers: [], connections: [UPSTOX] });
  const asked: string[] = [];
  await page.route(/\/api(?:\/v1)?\/journal\/entries(\?.*)?$/, (r) => { asked.push(r.request().url()); return r.fulfill({ json: DEMO_LIST }); });
  await page.route(/\/api(?:\/v1)?\/journal\/stats(\?.*)?$/, (r) => r.fulfill({ json: DEMO_STATS }));
  await page.route(/\/api(?:\/v1)?\/journal\/week(\?.*)?$/, (r) => r.fulfill({ json: DEMO_WEEK }));
  await page.route(/\/api(?:\/v1)?\/journal\/settings$/, (r) => r.fulfill({ json: SETTINGS }));
  await page.route(/\/api(?:\/v1)?\/journal\/trades\/2$/, (r) => r.fulfill({ json: DEMO_ENTRY }));
  await page.route(/\/api(?:\/v1)?\/journal\/(trades|manual)\/\d+\/chart\?at=(entry|exit)$/, (r) => r.fulfill({ body: PNG, contentType: "image/png" }));
  await page.route(/\/api(?:\/v1)?\/journal\/attachments\/\d+$/, (r) => r.fulfill({ body: PNG, contentType: "image/png" }));
  await page.route(/\/api(?:\/v1)?\/journal\/export\?.*$/, (r) => r.fulfill({ body: "date_ist,symbol\n", contentType: "text/csv",
    headers: { "content-disposition": 'attachment; filename="journal-all_today.csv"' } }));
  await page.goto("/analytics/journal");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  return asked;
}

for (const size of SIZES) {
  const phone = size.width < 1000;
  test.describe(`journal v2 ${size.width}`, () => {
    test.use({ viewport: size, isMobile: phone, hasTouch: phone, deviceScaleFactor: 1 });

    test("list, editor, outside-ATP form, stats, weekly review and export", async ({ page }) => {
      const asked = await openJournal(page);
      const list = page.locator("[data-block='journal-list']");
      await expect(list).toBeVisible();
      await expect(list.locator("[data-journal-row]")).toHaveCount(4);
      await expect(list.locator(phone ? "[data-journal-cards]" : "[data-journal-table]")).toBeVisible();
      await expect(list).toContainText("+₹1,23,457");
      await expect(list).toContainText("Outside ATP");
      await expect(page.locator("[data-page-subtitle]")).toHaveCount(0);           // no page-top help
      await noSidewaysScroll(page);
      await shot(page, `after-list-${size.width}`);

      await list.locator("[data-journal-row='trade-2']").first().click();
      const editor = page.locator("[data-entry-editor='trade-2']");
      await expect(editor).toBeVisible();
      await expect(editor).toContainText("Plan (as recorded)");
      await expect(editor.locator("[data-rule]")).toHaveCount(3);
      await expect(editor.locator("img").first()).toBeVisible();
      await shot(page, `after-editor-${size.width}`);
      await page.keyboard.press("Escape");
      await expect(editor).toHaveCount(0);

      await page.locator("[data-add-manual]").click();
      await expect(page.locator("[data-manual-form]")).toBeVisible();
      await page.locator("[data-manual-save]").click();
      await expect(page.locator("[data-manual-problems]")).toContainText("Enter the symbol.");
      await page.keyboard.press("Escape");

      const download = page.waitForEvent("download");
      await page.locator("[data-export='csv']").click();
      expect((await download).suggestedFilename()).toBe("journal-all_today.csv");
      expect(asked.length).toBeGreaterThan(0);

      await page.locator("#journal-tab-stats").click();
      const stats = page.locator("[data-journal-stats-v2]");
      await expect(stats).toBeVisible();
      await expect(stats).toContainText("40.0%");
      await expect(stats).toContainText("3.33");
      await noSidewaysScroll(page);
      await shot(page, `after-stats-${size.width}`);

      await page.locator("#journal-tab-review").click();
      const review = page.locator("[data-weekly-review]");
      await expect(review).toBeVisible();
      await expect(review.locator("[data-component]")).toHaveCount(5);
      await expect(review.locator("[data-help-button]")).toHaveCount(5);
      await expect(review.locator("[data-week-row]")).toHaveCount(3);
      await noSidewaysScroll(page);
      await shot(page, `after-review-${size.width}`);
    });
  });
}
