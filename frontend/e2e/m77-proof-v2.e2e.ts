import { expect, test, type Locator, type Page } from "@playwright/test";
import { mockChartReading, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M77 SX-8 (owner order M77 PD-1..PD-5; flag `study_export`): the proof shots of the study export in the app, on made-up
 * answers only (no backend; no real account, price or id): the workspace's "Save study" and "My studies" buttons, the
 * sheet (timeframes, PDF / PNG / CSV with their hints, the 11 section ticks - all on, his title and notes), the saved
 * state, My studies in the workspace and in the Journal, and "Your data" in Settings › Account (the bulk export names
 * the saved studies). The sample PDF's pages come from the backend builder (app/study/pdf.py) and sit beside these on
 * docs/design/M77_PROOF.html. `SX8_SHOTS=<folder>` writes `<part>-<width>-<theme>.png` (no baseline is stored).
 */
const SHOTS = process.env.SX8_SHOTS;
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const study = (id: number, title: string | null, symbol: string, tfs: string[], created: string, size: number) => ({
  id, title, symbol, exchange: "NSE", instrument_kind: "Index", timeframes: tfs, created_at: created, fingerprint: `f${id}`.padEnd(12, "0"),
  size_bytes: size, reading_source: "ATP structure readers",
  sections: ["cover", "charts", "structure", "areas", "ai_card", "option_context", "trade_plan", "pre_trade_check", "scenarios", "similar_setups", "notes"] });
const STUDIES = [study(9, "Bank look before the weekly expiry", "NIFTY BANK", ["1d", "1h", "15m"], "2026-10-19T05:20:00+00:00", 741_100),
  study(7, null, "NIFTY 50", ["15m"], "2026-10-17T09:40:00+00:00", 220_400), study(4, "Range top", "NIFTY 50", ["1h", "5m"], "2026-10-12T06:05:00+00:00", 318_000)];

async function open(page: Page, theme: "light" | "dark", path: string): Promise<{ saves: unknown[] }> {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["journal", "account"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, read_chart: true, copilot_workspace: true, study_export: true, discipline_engine: true }, brokers: CONNECTED });
  await mockCharts(page, {});
  await mockChartReading(page);
  const seen = { saves: [] as unknown[] };
  await page.route(/\/api(?:\/v1)?\/studies(\?.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      seen.saves.push(route.request().postDataJSON());
      return route.fulfill({ status: 201, json: { ...STUDIES[0], id: 10 } });
    }
    return route.fulfill({ json: { items: STUDIES, total: STUDIES.length } });
  });
  await page.route(/\/api(?:\/v1)?\/studies\/\d+\/[a-z]+(\?.*)?$/, (route) => route.fulfill({ status: 200, body: "file",
    headers: { "content-type": "application/octet-stream", "content-disposition": 'attachment; filename="study-nifty-50-20261019-1050.pdf"' } }));
  // The workspace's M76 parts, quiet (no events, every watch card off), so the pictures show the study parts.
  await page.route(/\/api(?:\/v1)?\/charts\/workspace\/events\?/, (r) => r.fulfill({ json: { underlying: "NIFTY", from: "2026-10-19",
    until: "2026-11-06", expiries: [], events: [], more: 0, calendar_empty: true, message: null, source: "Your events calendar", blocks: false } }));
  await page.route(/\/api(?:\/v1)?\/charts\/workspace\/watch$/, (r) => r.fulfill({ json: { version: 0, square_off_time: null,
    telegram: { connected: false, reaches_you: false }, blocks: false, prefs: { telegram: false, area_entry: false, area_rejection: false,
      strike_points: false, strike_points_n: null, iv_spike: false, iv_spike_points: null, margin_use: false, margin_use_pct: null,
      cut_off_reminder: false, cut_off_minutes: null, areas: [] } } }));
  await page.route(/\/api(?:\/v1)?\/journal\/[a-z]+(\?.*)?$/, (r) => r.fulfill({ status: 404, json: { detail: "Not in this proof" } }));
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  return seen;
}

async function hideNav(page: Page): Promise<void> {
  await page.addStyleTag({ content: "nav[aria-label='Quick navigation'] { visibility: hidden !important; }" });
}

async function shot(target: Locator | Page, name: string, width: number, theme: string): Promise<void> {
  if (!SHOTS) return;
  if ("screenshot" in target && "goto" in target) {
    await target.screenshot({ path: `${SHOTS}/${name}-${width}-${theme}.png`, animations: "disabled" });
    return;
  }
  const el = target as Locator;
  await hideNav(el.page());
  await el.evaluate((e) => e.scrollIntoView({ block: e.getBoundingClientRect().height < window.innerHeight ? "center" : "start" }));
  await el.screenshot({ path: `${SHOTS}/${name}-${width}-${theme}.png`, animations: "disabled" });
}

for (const [width, height] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`M77 proof ${width} ${theme}`, () => {
      test.use({ viewport: { width, height }, deviceScaleFactor: 1, ...(width < 768 ? { isMobile: true, hasTouch: true } : {}) });

      test(`Save study sheet, saved state and My studies in the workspace (${width} ${theme})`, async ({ page }) => {
        const seen = await open(page, theme, "/copilot/read?symbol=NIFTY%2050");
        const ws = page.locator("[data-copilot-workspace='NIFTY 50']");
        await expect(ws.locator("[data-embedded-chart='NIFTY 50'] canvas").first()).toBeVisible({ timeout: 15_000 });
        await expect(page.locator("[data-workspace-study]")).toBeVisible();
        await shot(page, "workspace", width, theme);

        await page.locator("[data-open-save-study]").click();
        const sheet = page.locator("[data-save-study]");
        await expect(sheet).toBeVisible();
        await expect(sheet.locator("[data-study-section]:checked")).toHaveCount(11);           // PD-1: all on by default
        await expect(sheet.locator("[data-study-format='pdf']")).toBeChecked();
        if (width < 768) await expectPhoneLayout(page);
        await shot(page, "sheet-top", width, theme);
        await sheet.locator("[data-study-section]").last().scrollIntoViewIfNeeded();
        await shot(page, "sheet-sections", width, theme);

        await sheet.locator("[data-study-tf='1d']").check();
        await sheet.locator("[data-study-title]").fill("Bank look before the weekly expiry");
        await sheet.locator("[data-study-notes]").fill("My own words: wait for the commitment candle at the buyer area.");
        const download = page.waitForEvent("download");
        await page.locator("[data-study-save]").click();
        await download;
        await expect(page.locator("[data-study-status='done']")).toContainText("Saved to My studies");
        expect(seen.saves).toHaveLength(1);
        await page.locator("[data-study-status='done']").scrollIntoViewIfNeeded();
        await shot(page, "sheet-saved", width, theme);
        await page.keyboard.press("Escape");

        await page.locator("[data-open-my-studies]").click();
        await expect(page.locator("[data-study-row='9']")).toContainText("Bank look before the weekly expiry");
        await expect(page.locator("[data-study-row]")).toHaveCount(3);
        if (width < 768) await expectPhoneLayout(page);
        await shot(page, "my-studies", width, theme);
      });

      test(`My studies in the Journal and the bulk export in Settings › Account (${width} ${theme})`, async ({ page }) => {
        await open(page, theme, "/analytics/journal");
        await page.getByRole("tab", { name: /studies/i }).click();
        const list = page.locator("[data-my-studies]");
        await expect(list.locator("[data-study-row]")).toHaveCount(3, { timeout: 15_000 });
        if (width < 768) await expectPhoneLayout(page);
        await shot(list, "studies-tab", width, theme);

        await page.goto("/settings/account");
        await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
        const data = page.locator("[data-my-data]");
        await expect(data).toContainText("saved studies", { timeout: 15_000 });
        await shot(data, "account-export", width, theme);
      });
    });
  }
}
