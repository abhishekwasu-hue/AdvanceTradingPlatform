import { expect, test, type Page } from "@playwright/test";
import { DEMO_ENTRY, DEMO_LIST, DEMO_STATS, DEMO_WEEK } from "../src/journal/journal.fixture";
import type { ImportPreview, JournalEntryFull, TradeChart } from "../src/journal/types";
import { mockApi } from "./mockApi";
import { UPSTOX } from "./settingsMocks";

/**
 * M88 (`ui_v2` + `discipline_engine`), dark theme, at 390 and 1440, on made-up answers: Journal › Trades › "Import from
 * a file" - choose a synthetic CSV, the preview (counts, columns, problems, trades), Import, the Stats tab reading the
 * imported trades, then an imported trade's "Show on chart" in place. No sideways scroll. `JOURNAL_SHOTS=<dir>` saves
 * each screen as a PNG there (the before / after comparison; nothing is committed). `M88_BEFORE=1` only shots the list
 * (run against the build before this change).
 */

const SIZES = [{ width: 390, height: 844 }, { width: 1440, height: 900 }];
const FEATURES = { ui_v2: true, discipline_engine: true, phase0_paper_only: true };
const SHOTS = process.env.JOURNAL_SHOTS;
const BEFORE = !!process.env.M88_BEFORE;
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAGklEQVR4nGNgYPj/n4GBgYGJAQIYGRkZGBgAJMgDA2QnUGMAAAAASUVORK5CYII=", "base64");
const CSV = [
  "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time",
  "DEMOX,INE000X00001,2032-03-15,NSE,EQ,EQ,buy,false,10,100.00,T1,O1,2032-03-15T09:20:00",
  "DEMOX,INE000X00001,2032-03-15,NSE,EQ,EQ,buy,false,10,110.00,T2,O2,2032-03-15T10:00:00",
  "DEMOX,INE000X00001,2032-03-16,NSE,EQ,EQ,sell,false,20,120.00,T3,O3,2032-03-16T11:00:00",
  "DEMOY,INE000Y00001,2032-03-16,NSE,EQ,EQ,sell,false,,200.00,T4,O4,2032-03-16T09:30:00",
].join("\n");

const PREVIEW: ImportPreview = {
  file: { name: "tradebook.csv", size: CSV.length, hash: "made-up" },
  layout: { id: "zerodha", label: "Zerodha Console tradebook" }, shape: "fills",
  pairing: "The fills of one symbol are paired first-in, first-out: a trade runs from its first fill until the position is back to zero, at the average entry and exit prices of its fills.",
  headers: CSV.split("\n")[0].split(","),
  mapping: { symbol: "symbol", side: "trade_type", quantity: "quantity", price: "price", value: null, time: "order_execution_time", date: "trade_date",
    entry_time: null, entry_price: null, exit_time: null, exit_price: null, charges: null, stop_loss: null, setup: null, notes: null, exchange: "exchange",
    fill_id: "trade_id", status: null },
  fields: [["symbol", "Symbol"], ["side", "Side"], ["quantity", "Quantity"], ["price", "Price (one fill)"], ["time", "Fill time"], ["date", "Fill date"],
    ["charges", "Charges"], ["stop_loss", "Your stop"]].map(([key, label]) => ({ key, label })),
  counts: { rows: 4, problems: 1, trades: 1, duplicates: 0, to_import: 1, open: 0, date_only: 0 },
  problems: [{ row: 5, reason: "No quantity." }],
  trades: [{ symbol: "DEMOX", exchange: "NSE", instrument_kind: "UNDERLYING", direction: "LONG", quantity: 20, entry_time: "2032-03-15T03:50:00+00:00",
    entry_price: 105, exit_time: "2032-03-16T05:30:00+00:00", exit_price: 120, charges: 0, pnl: 300, stop_loss: null, rows: [2, 3, 4], date_only: false,
    duplicate: false }],
  already_imported: null, limits: { max_bytes: 5242880, max_rows: 5000, max_trades: 2000 },
};

const IMPORTED: JournalEntryFull = {
  ...DEMO_ENTRY, kind: "manual", id: 41, manual: true, imported: true, source: "OUTSIDE_ATP", source_label: "Imported - done outside ATP",
  symbol: "DEMOX", mode: null, direction: "LONG", setup: null, entry_time: "2032-03-15T03:50:00+00:00", exit_time: "2032-03-16T05:30:00+00:00",
  plan_view: { setup: null, timeframe: null, area: null, invalidation: null, target: null, entry: 105, entry_source: "manual" },
  execution: { entry_price: 105, quantity: 20, slippage: null, exit_price: 120, exit_reason: null }, pnl: 300, r_multiple: null, tags: [],
  user_tags: [], attachments: [], notes: null,
  charts: { entry: "/api/journal/manual/41/chart?at=entry", exit: "/api/journal/manual/41/chart?at=exit", trade: "/api/journal/manual/41/trade-chart",
    trade_image: "/api/journal/manual/41/trade-chart.png" },
} as JournalEntryFull;

function chart(): TradeChart {
  const candles = Array.from({ length: 40 }, (_, i) => {
    const day = new Date(Date.UTC(2032, 1, 6 + i, 18, 30));
    const base = 95 + Math.round(10 * Math.sin(i / 4)) + i * 0.4;
    return { timestamp: day.toISOString().replace(".000Z", "+00:00"), open: base, high: base + 4, low: base - 3, close: base + (i % 3 === 0 ? -1 : 2), volume: 1000 + i };
  });
  const at = (iso: string) => candles.filter((c) => Date.parse(c.timestamp) <= Date.parse(iso)).at(-1)?.timestamp ?? null;
  return {
    symbol: "DEMOX", exchange: "NSE", direction: "LONG", timeframe: "day", timeframe_label: "Daily candles",
    note: "Daily candles from the end-of-day exchange files - intraday candles are not stored for this trade, so each mark sits on the candle of its day.",
    candles, levels: [], missing: false, missing_text: null, image: "/api/journal/manual/41/trade-chart.png",
    marks: [{ kind: "entry", label: "Entry", at: IMPORTED.entry_time as string, price: 105, side: "BUY", bar: at(IMPORTED.entry_time as string) },
      { kind: "exit", label: "Exit", at: IMPORTED.exit_time as string, price: 120, side: "SELL", bar: at(IMPORTED.exit_time as string) }],
  };
}

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
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["journal"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: FEATURES, brokers: [], connections: [UPSTOX] });
  const state = { imported: false, posted: [] as string[] };
  const list = () => (state.imported ? { ...DEMO_LIST, total: DEMO_LIST.total + 1, entries: [{ ...DEMO_LIST.entries[0], kind: "manual", id: 41, manual: true,
    source: "OUTSIDE_ATP", symbol: "DEMOX", setup: null, pnl: 300, r_multiple: null }, ...DEMO_LIST.entries] } : DEMO_LIST);
  const stats = () => (state.imported ? { ...DEMO_STATS, totals: { ...DEMO_STATS.totals, trades: 7, closed: 6, wins: 3, outside_atp: 2, win_rate: 50, net_pnl: 650 } } : DEMO_STATS);
  await page.route(/\/api(?:\/v1)?\/journal\/entries(\?.*)?$/, (r) => r.fulfill({ json: list() }));
  await page.route(/\/api(?:\/v1)?\/journal\/stats(\?.*)?$/, (r) => r.fulfill({ json: stats() }));
  await page.route(/\/api(?:\/v1)?\/journal\/week(\?.*)?$/, (r) => r.fulfill({ json: DEMO_WEEK }));
  await page.route(/\/api(?:\/v1)?\/journal\/settings$/, (r) => r.fulfill({ json: { settings: { custom_tags: [], checklist_rules: [] }, defaults: {}, ranges: {}, components: {}, tags: [] } }));
  await page.route(/\/api(?:\/v1)?\/journal\/import\/preview$/, (r) => { state.posted.push("preview"); return r.fulfill({ json: PREVIEW }); });
  await page.route(/\/api(?:\/v1)?\/journal\/import$/, (r) => {
    state.posted.push("import"); state.imported = true;
    return r.fulfill({ json: { imported: 1, duplicates: 0, problems: 1, import_id: 7, already_imported: false, entry_ids: [41] } });
  });
  await page.route(/\/api(?:\/v1)?\/journal\/manual\/41$/, (r) => r.fulfill({ json: IMPORTED }));
  await page.route(/\/api(?:\/v1)?\/journal\/manual\/41\/trade-chart$/, (r) => r.fulfill({ json: chart() }));
  await page.route(/\/api(?:\/v1)?\/journal\/manual\/41\/trade-chart\.png$/, (r) => r.fulfill({ body: PNG, contentType: "image/png" }));
  await page.route(/\/api(?:\/v1)?\/journal\/(trades|manual)\/\d+\/chart\?at=(entry|exit)$/, (r) => r.fulfill({ body: PNG, contentType: "image/png" }));
  await page.goto("/analytics/journal");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  return state;
}

for (const size of SIZES) {
  const phone = size.width < 1000;
  test.describe(`M88 journal import v2 ${size.width}`, () => {
    test.use({ viewport: size, isMobile: phone, hasTouch: phone, deviceScaleFactor: 1 });

    test("import a file: preview, Import, Stats read it, Show on chart", async ({ page }) => {
      const state = await openJournal(page);
      const list = page.locator("[data-block='journal-list']");
      await expect(list).toBeVisible();
      const here = page.url();
      await shot(page, `${BEFORE ? "before" : "after"}-m88-list-${size.width}`);
      if (BEFORE) return;
      await noSidewaysScroll(page);

      await page.locator("[data-import-trades]").click();
      const form = page.locator("[data-import-form]");
      await expect(form).toBeVisible();
      await expect(page.locator("[data-import-save]")).toBeDisabled();
      await page.locator("[data-import-file]").setInputFiles({ name: "tradebook.csv", mimeType: "text/csv", buffer: Buffer.from(CSV) });
      await expect(form.locator("[data-import-layout='zerodha']")).toBeVisible();
      await expect(form.locator("[data-import-counts]")).toContainText("Rows read");
      await expect(form.locator("[data-import-problem='5']")).toContainText("No quantity.");
      await expect(form.locator("[data-import-trade='DEMOX']")).toContainText("Entry");
      expect(state.posted).toEqual(["preview"]);                                            // nothing saved by the preview
      await noSidewaysScroll(page);
      await shot(page, `after-m88-preview-${size.width}`);
      await form.locator("[data-import-columns] summary").click();
      await expect(form.locator("[data-import-map='charges']")).toBeVisible();
      await shot(page, `after-m88-columns-${size.width}`);

      await page.locator("[data-import-save]").click();
      await expect(form).toHaveCount(0);
      expect(state.posted).toEqual(["preview", "import"]);
      await expect(list).toContainText("DEMOX");

      await page.locator("#journal-tab-stats").click();
      const stats = page.locator("[data-journal-stats-v2]");
      await expect(stats).toContainText("50.0%");                                           // the imported trade is counted
      await shot(page, `after-m88-stats-${size.width}`);

      await page.locator("#journal-tab-trades").click();
      await list.locator("[data-journal-row='manual-41']").first().click();
      const editor = page.locator("[data-entry-editor='manual-41']");
      await expect(editor).toBeVisible();
      await editor.locator("[data-show-on-chart]").click();
      const tc = editor.locator("[data-trade-chart]");
      await expect(tc.locator("[data-trade-chart-tf]")).toHaveText("Daily candles");
      await expect(tc.locator("[data-trade-chart-candles='40'] canvas").first()).toBeVisible();
      await tc.scrollIntoViewIfNeeded();
      await page.waitForTimeout(300);
      await noSidewaysScroll(page);
      await shot(page, `after-m88-chart-${size.width}`);
      const download = page.waitForEvent("download");
      await tc.locator("[data-trade-chart-save]").click();
      expect((await download).suggestedFilename()).toBe("DEMOX-trade-41.png");
      expect(page.url()).toBe(here);                                                       // in place: never navigated away
    });
  });
}
