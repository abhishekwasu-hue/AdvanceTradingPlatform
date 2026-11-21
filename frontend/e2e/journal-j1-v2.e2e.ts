import { expect, test, type Page } from "@playwright/test";
import { DEMO_ENTRY, DEMO_LIST } from "../src/journal/journal.fixture";
import type { JournalEntryFull, JournalListRow } from "../src/journal/types";
import { mockApi } from "./mockApi";
import { UPSTOX } from "./settingsMocks";

/**
 * P2-8 J1 (`ui_v2` + `discipline_engine`), on made-up answers: a spread is ONE row and ONE entry (combined P&L, credit,
 * max loss planned vs taken, every leg), the plan-vs-actual lines, the "From your broker" label and the "Sync from
 * broker" button, at 390 and 1440, light and dark, with no sideways scroll. `J1_SHOTS=<dir>` saves each screen there;
 * `J1_PHASE=before` answers as the backend on main does (one row per leg, no new fields) and only takes the pictures.
 */

const SIZES = [{ width: 390, height: 844 }, { width: 1440, height: 900 }];
const SHOTS = process.env.J1_SHOTS;
const BEFORE = process.env.J1_PHASE === "before";
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAGklEQVR4nGNgYPj/n4GBgYGJAQIYGRkZGBgAJMgDA2QnUGMAAAAASUVORK5CYII=", "base64");

const base = DEMO_LIST.entries[3];
const leg = (id: number, symbol: string, direction: string, price: number, pnl: number): JournalListRow => ({
  ...base, id, unit: "g:G1", symbol, direction, setup: "Bull put spread", entry_price: price, pnl, r_multiple: null, outcome: pnl > 0 ? "win" : "loss",
  leg_group_id: "G1", entry_time: "2032-03-17T04:20:00Z", chart: `/journal/trades/${id}/chart?at=entry`, tags: [], user_tags: [],
});
const LEGS = [leg(31, "DEMOIDX 100 PE", "SHORT", 50, 500), leg(32, "DEMOIDX 90 PE", "LONG", 20, -150)];
const BROKER_ROW: JournalListRow = { ...DEMO_LIST.entries[0], id: 5, unit: "m:5", symbol: "DEMOE", origin: "BROKER",
  source_label: "From your broker - done outside ATP", entry_time: "2032-03-17T06:10:00Z", pnl: 410, r_multiple: null, outcome: "win" };
const SPREAD = { group_id: "G1", structure: "Bull put spread", underlying: "DEMOIDX", legs: 2, closed: true, net_pnl: 350, credit: 1500,
  credit_kind: "credit" as const, planned_max_loss: 3500, actual_loss: null, charges: 20 };
const FOLDED: JournalListRow = { ...LEGS[0], symbol: "DEMOIDX Bull put spread", pnl: 350, outcome: "win", spread: SPREAD,
  legs: LEGS.map((l) => ({ id: l.id, symbol: l.symbol, direction: l.direction, quantity: 50, entry_price: l.entry_price, exit_price: null, pnl: l.pnl })) };

const list = () => (BEFORE
  ? { ...DEMO_LIST, total: 6, entries: [...LEGS, ...DEMO_LIST.entries.slice(0, 2), ...DEMO_LIST.entries.slice(2)] }
  : { ...DEMO_LIST, total: 6, entries: [BROKER_ROW, FOLDED, ...DEMO_LIST.entries] });
const entry = (): JournalEntryFull => {
  const plain: JournalEntryFull = { ...DEMO_ENTRY, id: 31, symbol: "DEMOIDX 100 PE", direction: "SHORT", setup: "Bull put spread", pnl: 500, r_multiple: null,
    plan_view: { ...DEMO_ENTRY.plan_view, setup: "Bull put spread", area: null }, attachments: [], notes: null };
  if (BEFORE) return plain;
  return { ...plain, pnl: 350, snapshots: { entry: true, exit: true }, plan_actual: ["Filled at 49.60; your signal's price was 50.00 (-0.40 per unit)."],
    spread: { ...SPREAD, leg_rows: FOLDED.legs ?? [] } };
};

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
  await page.route(/\/api(?:\/v1)?\/journal\/entries(\?.*)?$/, (r) => r.fulfill({ json: list() }));
  await page.route(/\/api(?:\/v1)?\/journal\/settings$/, (r) => r.fulfill({ json: { settings: { custom_tags: [], checklist_rules: [] }, defaults: {}, ranges: {}, components: {}, tags: [] } }));
  await page.route(/\/api(?:\/v1)?\/journal\/trades\/31$/, (r) => r.fulfill({ json: entry() }));
  await page.route(/\/api(?:\/v1)?\/journal\/broker-sync$/, (r) => r.fulfill({ json: { brokers: 1, read: 3, matched: 1, added: 1, closed: 0, already: 0, messages: [], entry_ids: [5] } }));
  await page.route(/\/api(?:\/v1)?\/journal\/(trades|manual)\/\d+\/chart\?at=(entry|exit)$/, (r) => r.fulfill({ body: PNG, contentType: "image/png" }));
  await page.goto("/analytics/journal");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  // P2-8 J6: the Journal opens on Today; these checks start on Trades (every build has that tab)
  await page.locator("#journal-tab-trades").click();
}

async function noSidewaysScroll(page: Page) {
  const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(r.scroll, "page wider than the screen").toBeLessThanOrEqual(r.width);
}

for (const size of SIZES) {
  const phone = size.width < 1000;
  for (const theme of ["light", "dark"] as const) {
    test.describe(`journal J1 ${size.width} ${theme}`, () => {
      test.use({ viewport: size, isMobile: phone, hasTouch: phone, deviceScaleFactor: 1 });

      test("a spread as one entry, plan vs actual, synced trades", async ({ page }) => {
        const phase = BEFORE ? "before" : "after";
        await open(page, theme);
        const listCard = page.locator("[data-block='journal-list']");
        await expect(listCard).toBeVisible();
        await noSidewaysScroll(page);
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/${phase}-list-${size.width}-${theme}.png`, animations: "disabled" });
        if (!BEFORE) {
          await expect(listCard.locator("[data-spread-row]")).toHaveCount(1);
          await expect(listCard).toContainText("From your broker");
          await page.locator("[data-broker-sync]").click();
          await expect(page.getByText("1 trade added. 1 matched your ATP trades.")).toBeVisible();
        }
        await listCard.locator("[data-journal-row='trade-31']").first().click();
        const editor = page.locator("[data-entry-editor='trade-31']");
        await expect(editor).toBeVisible();
        if (!BEFORE) {
          await expect(editor.locator("[data-sheet-section='spread']")).toContainText("Credit received");
          await expect(editor.locator("[data-spread-legs] li")).toHaveCount(2);
          await expect(editor.locator("[data-plan-actual='diff']")).toContainText("your signal's price was 50.00");
          await editor.locator("[data-sheet-section='spread']").scrollIntoViewIfNeeded();
        }
        await noSidewaysScroll(page);
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/${phase}-entry-${size.width}-${theme}.png`, animations: "disabled" });
      });
    });
  }
}
