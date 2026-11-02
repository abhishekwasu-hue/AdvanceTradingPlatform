import { expect, test, type Page } from "@playwright/test";
import { mockChartReading, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M77 SX-7 (owner order M77 PD-1 / PD-4; flag `study_export`): "Save study" and "My studies" in the Copilot's chart
 * workspace, in a browser with every reply made up here (no backend). Proofs:
 * - the sheet opens over the chart in the same tab (the address never changes on any tap); every section is ticked,
 *   his open chart's timeframe is ticked, PDF is chosen;
 * - Save sends references and his own words only (no number from the page) and downloads the chosen file;
 * - My studies lists his studies with their downloads and a confirmed Delete; 360 / 390 px: no sideways scroll.
 * `STUDY_OUT=<folder>` also writes screenshots (the comparison page), never baselines.
 */
const OUT = process.env.STUDY_OUT;
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const STUDY = { id: 7, title: "Bank look", symbol: "NIFTY 50", exchange: "NSE", instrument_kind: "Index", timeframes: ["1d", "15m"],
  sections: ["cover", "charts", "structure", "areas", "notes"], created_at: "2026-10-19T05:20:00+00:00", fingerprint: "abc123abc123",
  size_bytes: 120000, reading_source: "ATP structure readers" };

interface Seen { saves: unknown[]; files: string[]; deletes: number[] }

async function open(page: Page, theme: "light" | "dark", on = true): Promise<Seen> {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, read_chart: true, copilot_workspace: true, study_export: on }, brokers: CONNECTED });
  await mockCharts(page, {});
  await mockChartReading(page);
  const seen: Seen = { saves: [], files: [], deletes: [] };
  await page.route(/\/api(?:\/v1)?\/studies(\?.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      seen.saves.push(route.request().postDataJSON());
      return route.fulfill({ status: 201, json: { ...STUDY, id: 8, title: null, timeframes: ["15m"] } });
    }
    return route.fulfill({ json: { items: seen.deletes.includes(7) ? [] : [STUDY], total: seen.deletes.includes(7) ? 0 : 1 } });
  });
  await page.route(/\/api(?:\/v1)?\/studies\/\d+(\/[a-z]+)?(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "DELETE") {
      seen.deletes.push(Number(url.pathname.split("/").pop()));
      return route.fulfill({ json: { deleted: true } });
    }
    seen.files.push(url.pathname.replace(/^.*\/studies/, "") + url.search);
    const kind = url.pathname.split("/").pop();
    return route.fulfill({ status: 200, body: "file", headers: { "content-type": "application/octet-stream",
      "content-disposition": `attachment; filename="study-nifty-50-20261019-1050.${kind === "csv" ? "zip" : kind}"` } });
  });
  await page.goto("/copilot/read?symbol=NIFTY%2050");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  return seen;
}

for (const [width, height] of [[360, 800], [390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`Save study ${width} ${theme}`, () => {
      test.use({ viewport: { width, height }, deviceScaleFactor: 1, ...(width < 768 ? { isMobile: true, hasTouch: true } : {}) });

      test(`before: the flag off shows no study buttons (${width} ${theme})`, async ({ page }) => {
        await open(page, theme, false);
        await expect(page.locator("[data-copilot-workspace='NIFTY 50'] [data-embedded-chart='NIFTY 50'] canvas").first()).toBeVisible({ timeout: 15_000 });
        await expect(page.locator("[data-workspace-study]")).toHaveCount(0);
        if (OUT) await page.screenshot({ path: `${OUT}/before-workspace-${width}-${theme}.png` });
      });

      test(`save in place, then My studies (${width} ${theme})`, async ({ page }) => {
        const seen = await open(page, theme);
        const path = new URL(page.url()).pathname + new URL(page.url()).search;
        const ws = page.locator("[data-copilot-workspace='NIFTY 50']");
        await expect(ws.locator("[data-embedded-chart='NIFTY 50'] canvas").first()).toBeVisible({ timeout: 15_000 });
        if (OUT) await page.screenshot({ path: `${OUT}/after-workspace-${width}-${theme}.png` });
        await page.locator("[data-open-save-study]").click();
        const sheet = page.locator("[data-save-study]");
        await expect(sheet).toBeVisible();
        await expect(sheet.locator("[data-study-section]:checked")).toHaveCount(11);         // all on (PD-1)
        await expect(sheet.locator("[data-study-tf='5m']")).toBeChecked();                 // the chart he has open
        await expect(sheet.locator("[data-study-format='pdf']")).toBeChecked();
        if (width < 768) await expectPhoneLayout(page);
        if (OUT) await page.screenshot({ path: `${OUT}/after-save-sheet-${width}-${theme}.png` });
        await sheet.locator("[data-study-tf='1d']").check();
        await sheet.locator("[data-study-section='scenarios']").uncheck();
        await sheet.locator("[data-study-notes]").fill("My own words");
        const download = page.waitForEvent("download");
        await page.locator("[data-study-save]").click();
        expect((await download).suggestedFilename()).toBe("study-nifty-50-20261019-1050.pdf");
        await expect(page.locator("[data-study-status='done']")).toContainText("Saved to My studies");
        expect(seen.saves).toHaveLength(1);
        const body = seen.saves[0] as Record<string, unknown>;
        expect(Object.keys(body).sort()).toEqual(["instrument", "notes", "sections", "timeframes"]);
        expect(body).toMatchObject({ instrument: "NSE:NIFTY 50", timeframes: ["1d", "5m"], notes: "My own words" });
        expect(body.sections).not.toContain("scenarios");
        expect(seen.files).toEqual(["/8/pdf"]);
        expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe(path);   // the same tab, the same address
        await page.keyboard.press("Escape");
        await page.locator("[data-open-my-studies]").click();
        const row = page.locator("[data-study-row='7']");
        await expect(row).toBeVisible();
        await expect(row).toContainText("Bank look");
        if (width < 768) await expectPhoneLayout(page);
        if (OUT) await page.screenshot({ path: `${OUT}/after-my-studies-${width}-${theme}.png` });
        const csv = page.waitForEvent("download");
        await row.locator("[data-study-download='csv']").click();
        expect((await csv).suggestedFilename()).toBe("study-nifty-50-20261019-1050.zip");
        await row.locator("[data-study-delete]").click();
        await page.locator("[data-study-delete-confirm]").click();
        await expect(page.locator("[data-my-studies='ready']")).toContainText("No saved studies");
        expect(seen.deletes).toEqual([7]);
        expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe(path);
      });
    });
  }
}
