import { expect, test, type Page } from "@playwright/test";
import { DEMO_LIST } from "../src/journal/journal.fixture";
import type { JournalPlan, JournalToday, ProofPage } from "../src/journal/types";
import { mockApi } from "./mockApi";
import { UPSTOX } from "./settingsMocks";

/**
 * P2-8 J4 + J5 + J6 (`ui_v2` + `discipline_engine`), on made-up answers: the Journal opens on Today (his if-then plans,
 * a plan a stop hit brought back, his goals, his last good decision), My proof, Plans and "Send my journal", at 390
 * and 1440, light and dark, with no sideways scroll. `J46_SHOTS=<dir>` saves each screen; `J46_PHASE=before` only takes
 * the picture of what the page opened on before this PR.
 */

const SIZES = [{ width: 390, height: 844 }, { width: 1440, height: 900 }];
const SHOTS = process.env.J46_SHOTS;
const BEFORE = process.env.J46_PHASE === "before";
const IF_THEN: JournalPlan = { id: 1, kind: "IF_THEN", if_text: "a trade stops me out", then_text: "I take a short break before the next entry",
  trigger: "STOP_HIT", metric: null, metric_label: null, target_pct: null, period: null, active: true };
const IF_THEN_2: JournalPlan = { ...IF_THEN, id: 3, if_text: "I feel unease after entry", then_text: "I check the stop and wait 5 minutes before any change", trigger: "NONE" };
const GOAL_PLAN: JournalPlan = { id: 2, kind: "GOAL", if_text: null, then_text: null, trigger: "NONE", metric: "PLAN_FOLLOWED", metric_label: "Plan followed",
  target_pct: 90, period: "MONTH", active: true };
const GOAL = { id: 2, period: "MONTH" as const, metric: "PLAN_FOLLOWED" as const, label: "Plan followed", target_pct: 90, done: 7, base: 9, pct: null, reached: false, small: true };
const FACT = { key: "stop_kept:trade:2", kind: "stop_kept", text: "Stop was kept on DEMOB.", at: "2032-03-16T05:10:00Z", entry_kind: "trade" as const, entry_id: 2, chart: null };
const TODAY: JournalToday = { plans: [IF_THEN, IF_THEN_2], reminders: [{ ...IF_THEN, because: "Stop hit on DEMOB", entry_kind: "trade", entry_id: 2 }],
  goals: [GOAL], last_good: FACT, after_loss: "In your last 9 trades you followed your plan in 7." };
const PROOF: ProofPage = { days: 90, last_good: FACT, after_loss: TODAY.after_loss, items: [
  { ...FACT, own: false, pinned: true },
  { key: "plan_on_loss:trade:2", kind: "plan_on_loss", text: "Plan followed on a loss (DEMOB): a process win.", at: "2032-03-16T05:10:00Z", entry_kind: "trade", entry_id: 2, chart: null, own: false, pinned: false },
  { key: "no_revenge:trade:3", kind: "no_revenge", text: "No hurry after the loss on DEMOB: next entry DEMOC without a revenge tag.", at: "2032-03-16T08:45:00Z", entry_kind: "trade", entry_id: 3, chart: null, own: false, pinned: false },
  { key: "streak:trade:1", kind: "streak", text: "5 trades in a row with your plan followed (to DEMOA).", at: "2032-03-15T06:00:00Z", entry_kind: "trade", entry_id: 1, chart: null, own: false, pinned: false },
  { key: "own:abc", id: 9, kind: "own", text: "Skipped a setup that was not mine", at: "2032-03-14", entry_kind: null, entry_id: null, chart: null, own: true, pinned: false }] };
const DAILY = { day: "2032-03-17", trades: 0, closed: 0, net_pnl: null, plan: { yes: 0, partly: 0, no: 0, answered: 0, unanswered: 0, rate: null },
  feelings: [], confirmed_tags: [], done_well: null, lines: ["No trades today."], gate: { ready: false, closed_total: 9, needed: 20, note: "Not enough trades yet (9 of 20)." }, entries: [] };

const SETTINGS = {
  settings: { revenge_window_minutes: 30, revenge_size_multiple: 1.5, fomo_chase_r: 0.25, overtrading_daily_norm: 0, overtrading_lookback_days: 20,
    overtrading_multiple: 2, overtrading_min_norm: 3, event_kinds: [], count_unconfirmed: false, weights: {}, custom_tags: ["late entry"],
    checklist_rules: ["Trend agrees"], attachment_max_mb: 5, feelings: ["calm", "confident", "unsure", "anxious", "excited", "fearful", "FOMO"],
    min_closed_for_review: 20 },
  defaults: {}, ranges: { attachment_max_mb: [0.5, 8], min_closed_for_review: [5, 200] }, components: {}, tags: [],
};

async function open(page: Page, theme: "light" | "dark") {
  await page.clock.install({ time: new Date("2032-03-17T08:30:00+05:30") });
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
  await page.route(/\/api(?:\/v1)?\/journal\/settings$/, (r) => r.fulfill({ json: SETTINGS }));
  await page.route(/\/api(?:\/v1)?\/journal\/checkins\/waiting$/, (r) => r.fulfill({ json: { waiting: [] } }));
  await page.route(/\/api(?:\/v1)?\/journal\/today$/, (r) => r.fulfill({ json: TODAY }));
  await page.route(/\/api(?:\/v1)?\/journal\/daily(\?.*)?$/, (r) => r.fulfill({ json: DAILY }));
  await page.route(/\/api(?:\/v1)?\/journal\/proof$/, (r) => r.fulfill({ json: PROOF }));
  await page.route(/\/api(?:\/v1)?\/journal\/plans$/, (r) => r.fulfill({ json: { plans: [IF_THEN, IF_THEN_2, GOAL_PLAN], goals: [GOAL] } }));
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
    test.describe(`journal J4-J6 ${size.width} ${theme}`, () => {
      test.use({ viewport: size, isMobile: phone, hasTouch: phone, deviceScaleFactor: 1 });

      test("Today, My proof, Plans, Send my journal", async ({ page }) => {
        const phase = BEFORE ? "before" : "after";
        const shot = async (name: string) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${phase}-${name}-${size.width}-${theme}.png`, animations: "disabled" }); };
        await open(page, theme);
        await expect(page.locator("#journal-tab-trades")).toBeVisible();
        if (BEFORE) { await shot("open"); return; }
        const today = page.locator("[data-block='journal-today']");
        await expect(today).toContainText("I take a short break before the next entry");
        await expect(page.locator("[data-block='plan-reminders']")).toContainText("Stop hit on DEMOB");
        await expect(page.locator("[data-block='last-good']")).toContainText("Stop was kept on DEMOB.");
        await noSidewaysScroll(page);
        await shot("open");

        await page.locator("#journal-tab-proof").click();
        await expect(page.locator("[data-proof]")).toHaveCount(5);
        await page.locator("[data-block='my-proof']").scrollIntoViewIfNeeded();
        await noSidewaysScroll(page);
        await shot("proof");

        await page.locator("#journal-tab-plans").click();
        await expect(page.locator("[data-plan]")).toHaveCount(3);
        await expect(page.locator("[data-block='process-goals']")).toContainText("Plan followed: 7 of 9 this month; your goal 90%");
        await noSidewaysScroll(page);
        await shot("plans");

        await page.locator("#journal-tab-settings").click();
        const delivery = page.locator("[data-block='journal-delivery']");
        for (const key of ["telegram", "daily", "weekly", "checkin_prompts"]) {
          const box = delivery.locator(`[data-delivery='${key}']`);
          await expect(box).not.toBeChecked();                                    // everything OFF until he turns it on
          await box.check();
        }
        await delivery.scrollIntoViewIfNeeded();
        await noSidewaysScroll(page);
        await shot("delivery");
      });
    });
  }
}
