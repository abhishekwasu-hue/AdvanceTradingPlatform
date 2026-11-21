import { expect, test, type Page } from "@playwright/test";
import { DEMO_ENTRY, DEMO_LIST, DEMO_WEEK } from "../src/journal/journal.fixture";
import type { Checkin, DailyCard, MonthlyReview, WeeklyExtras } from "../src/journal/types";
import { mockApi } from "./mockApi";
import { UPSTOX } from "./settingsMocks";

/**
 * P2-8 J2 + J3 (`ui_v2` + `discipline_engine`), on made-up answers: the check-in prompt card, the check-in in the
 * entry sheet, and the Day / Week / Month reviews (the first-20 note, small samples labelled), at 390 and 1440, light
 * and dark, with no sideways scroll. `J23_SHOTS=<dir>` saves each screen; `J23_PHASE=before` only takes pictures of
 * the build before this PR (no check-in, the weekly review only).
 */

const SIZES = [{ width: 390, height: 844 }, { width: 1440, height: 900 }];
const SHOTS = process.env.J23_SHOTS;
const BEFORE = process.env.J23_PHASE === "before";
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAGklEQVR4nGNgYPj/n4GBgYGJAQIYGRkZGBgAJMgDA2QnUGMAAAAASUVORK5CYII=", "base64");
const FEELINGS = ["calm", "confident", "unsure", "anxious", "excited", "fearful", "FOMO"];
const GATE = { ready: false, closed_total: 7, needed: 20, note: "Not enough trades yet (7 of 20)." };
const PLAN = { yes: 2, partly: 1, no: 0, answered: 3, unanswered: 1, rate: null };

const CHECKIN: Checkin = { feeling_before: "calm", confidence: 4, feeling_after: "unsure", plan_followed: "PARTLY", mistake: "early_exit",
  lesson: "same entry, let the exit rule work", setup_name: null, setup_name_suggested: "Seller area",
  notes: [{ at: "2032-03-16T04:20:00Z", text: "unease after entry" }], feelings: FEELINGS,
  mistakes: ["revenge_trade", "fomo_entry", "averaging_loser", "moved_stop_against", "no_stop", "overtrading", "early_exit", "held_through_event", "other"],
  plan_answers: ["YES", "PARTLY", "NO"], answered: true };
const DAILY: DailyCard = { day: "2032-03-17", trades: 3, closed: 2, net_pnl: 820, plan: PLAN, feelings: [{ feeling: "calm", count: 2 }, { feeling: "unsure", count: 1 }],
  confirmed_tags: [{ tag: "early_exit", label: "Early exit", count: 1 }], done_well: "Stop kept on all 2 trades.", lines: [], gate: GATE,
  entries: [{ kind: "trade", id: 1, symbol: "DEMOA", pnl: 1360 }, { kind: "trade", id: 2, symbol: "DEMOB", pnl: -540 }] };
const EXTRAS: WeeklyExtras = { week: "2032-W11", gate: GATE, plan: PLAN,
  feelings: [{ value: "calm", trades: 3, wins: 2, losses: 1, net_pnl: 1080, avg_pnl: 360, win_rate: null, small: true },
    { value: "anxious", trades: 2, wins: 0, losses: 2, net_pnl: -790, avg_pnl: -395, win_rate: null, small: true }],
  feelings_after: [], confidence: [{ value: 2, trades: 2, wins: 0, losses: 2, net_pnl: -790, avg_pnl: -395, win_rate: null, small: true },
    { value: 4, trades: 3, wins: 2, losses: 1, net_pnl: 1080, avg_pnl: 360, win_rate: null, small: true }],
  mistake: { value: "early_exit", label: "Early exit", count: 2 },
  lines: ["Plan followed: 2 of 3 answered.", "Most frequent mistake you named: Early exit (2 times).", "Largest move against you while open: ₹750 on DEMOB."],
  notable: [{ kind: "trade", id: 1, symbol: "DEMOA", pnl: 1360, chart: null }, { kind: "trade", id: 2, symbol: "DEMOB", pnl: -540, chart: null }],
  done_well: "Plan followed on a losing trade (DEMOB).", question: "What is the one thing to repeat next week?", answer: "waiting for the close" };
const MONTH: MonthlyReview = { month: "2032-03", gate: GATE, trades: 7, closed: 6, net_pnl: 2140, plan: PLAN,
  weeks: [{ week: "2032-W10", score: 72, plan: PLAN, trades: 3 }, { week: "2032-W11", score: 81, plan: PLAN, trades: 4 }],
  setups: [{ setup: "Buyer area", trades: 4, net_pnl: 2600, win_rate: null, small: true }, { setup: "Seller area", trades: 2, net_pnl: -460, win_rate: null, small: true }],
  best_setup: null, worst_setup: null,
  lines: ["7 trades, 6 closed; result +₹2,140.", "Plan followed: 2 of 3 answered.", "Best setup by your numbers: Buyer area (+₹2,600, 4 trades, small sample)."],
  done_well: "Stop kept on all 6 trades.", question: "Which one habit from this month do you want to keep?", answer: null };

async function open(page: Page, theme: "light" | "dark") {
  await page.clock.install({ time: new Date("2032-03-17T18:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["journal"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, discipline_engine: true, phase0_paper_only: true }, brokers: [], connections: [UPSTOX] });
  await page.route(/\/api(?:\/v1)?\/journal\/entries(\?.*)?$/, (r) => r.fulfill({ json: DEMO_LIST }));
  await page.route(/\/api(?:\/v1)?\/journal\/settings$/, (r) => r.fulfill({ json: { settings: { custom_tags: [], checklist_rules: [], feelings: FEELINGS }, defaults: {}, ranges: {}, components: {}, tags: [] } }));
  await page.route(/\/api(?:\/v1)?\/journal\/trades\/2$/, (r) => r.fulfill({ json: { ...DEMO_ENTRY, attachments: [] } }));
  await page.route(/\/api(?:\/v1)?\/journal\/trades\/2\/checkin$/, (r) => r.fulfill({ json: CHECKIN }));
  await page.route(/\/api(?:\/v1)?\/journal\/checkins\/waiting$/, (r) => r.fulfill({ json: { waiting: [
    { kind: "trade", id: 2, symbol: "DEMOB", moment: "close", mode: "PAPER", at: "2032-03-16T05:10:00Z" },
    { kind: "trade", id: 3, symbol: "DEMOC", moment: "open", mode: "PAPER", at: "2032-03-16T08:45:00Z" }] } }));
  await page.route(/\/api(?:\/v1)?\/journal\/week(\?.*)?$/, (r) => r.fulfill({ json: DEMO_WEEK }));
  await page.route(/\/api(?:\/v1)?\/journal\/daily(\?.*)?$/, (r) => r.fulfill({ json: DAILY }));
  await page.route(/\/api(?:\/v1)?\/journal\/review\/extras(\?.*)?$/, (r) => r.fulfill({ json: EXTRAS }));
  await page.route(/\/api(?:\/v1)?\/journal\/monthly(\?.*)?$/, (r) => r.fulfill({ json: MONTH }));
  await page.route(/\/api(?:\/v1)?\/journal\/(trades|manual)\/\d+\/chart\?at=(entry|exit)$/, (r) => r.fulfill({ body: PNG, contentType: "image/png" }));
  await page.goto("/analytics/journal");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
}

async function noSidewaysScroll(page: Page) {
  const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(r.scroll, "page wider than the screen").toBeLessThanOrEqual(r.width);
}

for (const size of SIZES) {
  const phone = size.width < 1000;
  for (const theme of ["light", "dark"] as const) {
    test.describe(`journal J2 J3 ${size.width} ${theme}`, () => {
      test.use({ viewport: size, isMobile: phone, hasTouch: phone, deviceScaleFactor: 1 });

      test("check-in prompt, check-in, day / week / month reviews", async ({ page }) => {
        const phase = BEFORE ? "before" : "after";
        const shot = async (name: string) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${phase}-${name}-${size.width}-${theme}.png`, animations: "disabled" }); };
        await open(page, theme);
        await expect(page.locator("[data-block='journal-list']")).toBeVisible();
        if (!BEFORE) await expect(page.locator("[data-block='waiting-checkins'] [data-waiting]")).toHaveCount(2);
        await noSidewaysScroll(page);
        await shot("trades");

        await page.locator("[data-journal-row='trade-2']").first().click();
        const editor = page.locator("[data-entry-editor='trade-2']");
        await expect(editor).toBeVisible();
        if (!BEFORE) {
          const section = editor.locator("[data-sheet-section='checkin']");
          await expect(section.locator("[data-checkin-field='plan_followed'] [aria-pressed='true']")).toHaveText("Partly");
          await expect(section).toContainText("unease after entry");
          await section.scrollIntoViewIfNeeded();
        }
        await noSidewaysScroll(page);
        await shot("checkin");
        await page.keyboard.press("Escape");

        await page.locator("#journal-tab-review").click();
        await expect(page.locator("[data-weekly-review]")).toBeVisible();
        if (!BEFORE) {
          const extras = page.locator("[data-block='weekly-extras']");
          await expect(extras).toContainText("Not enough trades yet (7 of 20).");
          await expect(extras).toContainText("small sample");
          await extras.scrollIntoViewIfNeeded();
        }
        await noSidewaysScroll(page);
        await shot("week");
        if (BEFORE) return;

        await page.locator("[data-period='day']").click();
        await expect(page.locator("[data-block='daily-card']")).toContainText("Done well: Stop kept on all 2 trades.");
        await noSidewaysScroll(page);
        await shot("day");
        await page.locator("[data-period='month']").click();
        await expect(page.locator("[data-block='monthly-review']")).toContainText("81 / 100");
        await noSidewaysScroll(page);
        await shot("month");
      });
    });
  }
}
