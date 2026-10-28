import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { pickOption } from "./combobox";
import { mockApi, type MockOptions } from "./mockApi";
import { cardOverflows, expectNoSidewaysOverflow } from "./phoneLayout";
import { closeScreenerMore, screenerTool } from "./screenerTools";

/**
 * Owner order SCR-FIX, PR E2: Find setups at 390 px, dark with colour v2 and light. On each: a formula block with a long
 * formula, the Conditions footer, the evidence dialog, the Telegram-unlinked state and the phone's bottom bar. The page
 * never scrolls sideways (document scrollWidth <= clientWidth) and no element runs past its card's right edge
 * (e2e/phoneLayout.ts `cardOverflows`). E1: one "not advice" line on the page, one in the evidence dialog.
 * Symbols are made up; no baselines here (the guard is the assertion).
 */
const read = (path: string): unknown => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
const registry = (read("../src/screener/builderTexts.json") as { registry: unknown }).registry;
const PACK = read("../src/screener/evidenceSample.json") as Record<string, unknown>;
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const SYMBOLS = ["ALPHAINFRASTRUCTURE", "BRK", "GAMMA", "DELTA"];
const LONG_FORMULA = "high - low < Min(high - low, 6)[1] AND close > Max(high, 20)[1] AND volume > 2 * SMA(volume, 20)";

const VARIANTS = [
  { label: "dark, colour v2", theme: "dark", colourV2: true },
  { label: "light", theme: "light", colourV2: false },
] as const;

async function open(page: Page, theme: "dark" | "light", colourV2: boolean, pack: Record<string, unknown> = PACK) {
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["screener"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const options: MockOptions = {
    features: { ui_v2: true, screener_v2: true, screener_gallery: true, screener_builder_u2: true, ...(colourV2 ? { colour_v2: true } : {}) },
    brokers: CONNECTED,
  };
  await mockApi(page, options);
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
    if (/\/evidence\/11(\?.*)?$/.test(url)) return route.fulfill({ json: pack });
    if (/\/run$/.test(url)) {
      const body = route.request().postDataJSON() as { source: string; symbols: string[]; preview?: boolean };
      if (body.preview) return route.fulfill({ status: 503, json: { detail: "no preview here" } });
      return route.fulfill({ json: {
        run_id: 9, preview: false, text: body.source, base_tf: "1d", data_source: "broker:upstox", scanned: body.symbols.length, matched: ["BRK"],
        results: body.symbols.map((s) => ({ symbol: s, matched: s === "BRK", reason: s === "BRK" ? null : "not enough bars for the formula on the daily chart",
          evidence_id: s === "BRK" ? 11 : null })),
        funnel: { universe: body.symbols.length, with_data: body.symbols.length, stages: [{ text: body.source, survivors: 1 }] },
        disclaimer: "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations.",
      } });
    }
    return route.fulfill({ status: 503, headers: { "X-Feature-Disabled": "screener_legends" }, json: { detail: "disabled" } });
  });
  await page.goto("/screener");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await expect(page.getByText("Start with a template or add your first condition.")).toBeVisible();
}

/** Own symbols (one long name), Advanced open, a formula block holding a long formula, then Run. */
async function formulaScan(page: Page) {
  await pickOption(page, page.getByTestId("universe-select"), "custom");
  await page.locator("#screener-symbols").fill(SYMBOLS.join(", "));
  await screenerTool(page, "screener-advanced");
  await closeScreenerMore(page);
  await pickOption(page, page.getByRole("combobox", { name: "Kind of condition to add" }).first(), "formula");
  await page.getByRole("button", { name: "Add a condition" }).first().click();
  const formula = page.getByRole("textbox", { name: "Formula" }).first();
  await formula.fill(LONG_FORMULA);
  await formula.blur();
  await page.getByTestId("run-scan-mobile").click();
  await expect(page.getByTestId("show-on-chart").first()).toBeVisible();
}

/** The visible "not advice" lines (Disclaimer / PageFootnote) inside `scope`. */
async function disclaimers(page: Page, scope: string): Promise<number> {
  return page.locator(`${scope} [data-disclaimer]`).evaluateAll((all) => all.filter((e) => (e as HTMLElement).offsetParent !== null).length);
}

test.describe("SCR-FIX E2: Find setups on a 390 px phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("the card guard itself, on a hand-written page, so it can never pass by accident", async ({ page }) => {
    await page.setContent(`<!doctype html><html><body style="margin:0"><main>
      <section aria-label="Fits" style="width:300px"><p>Short.</p></section>
      <section aria-label="Spills" style="width:300px"><div style="width:340px">too wide</div></section>
      <section aria-label="Scrolls" style="width:300px"><div style="overflow-x:auto"><table style="width:600px"><tr><td>wide</td></tr></table></div></section>
      <section aria-label="Folded" style="width:300px"><details><summary>Details</summary><div style="width:600px">hidden</div></details></section>
    </main></body></html>`);
    expect(await cardOverflows(page)).toEqual(["Spills: DIV 'too wide' runs 40 px past"]);
  });

  for (const v of VARIANTS) {
    test(`${v.label}: the formula block, the Conditions footer and the bottom bar stay inside their cards`, async ({ page }) => {
      await open(page, v.theme, v.colourV2);
      await expectNoSidewaysOverflow(page);
      await formulaScan(page);
      await expect(page.getByRole("region", { name: "Conditions" }).getByRole("button", { name: "Add a condition" })).toBeVisible();
      await expect(page.getByTestId("screener-bottom-bar")).toBeVisible();
      await expect(page.getByTestId("screener-bottom-bar")).toContainText("Matched 1 of 4");
      await expectNoSidewaysOverflow(page);
      // E1: the page's one "not advice" line is its footnote; the run's own note waits in its Details
      expect(await disclaimers(page, "main")).toBe(1);
      await expect(page.locator("main [data-page-footnote]")).toHaveCount(1);
      await expect(page.locator("[data-details='screener.results.about']")).toBeAttached();
    });

    test(`${v.label}: the evidence dialog fits the phone, with one "not advice" line`, async ({ page }) => {
      await open(page, v.theme, v.colourV2);
      await formulaScan(page);
      await page.getByTestId("show-on-chart").first().click();
      const dialog = page.getByRole("dialog", { name: "Why BRK matched" });
      await expect(dialog.getByTestId("evidence-view")).toBeVisible();
      await expectNoSidewaysOverflow(page, "[role='dialog']");
      expect(await disclaimers(page, "[role='dialog']")).toBe(1);
      await expect(dialog.locator("[data-details='screener.evidence.about']")).toBeAttached();
    });

    // PR C (claude/scr-fix-c-chart-telegram) adds `telegram_linked` and the connect row; this base has neither.
    test.fixme(`${v.label}: Telegram not linked - the connect row fits the dialog`, async ({ page }) => {
      await open(page, v.theme, v.colourV2, { ...PACK, telegram_linked: false, telegram_reason: "not_linked",
        telegram_message: "Connect your Telegram to get this chart there." });
      await formulaScan(page);
      await page.getByTestId("show-on-chart").first().click();
      const dialog = page.getByRole("dialog", { name: "Why BRK matched" });
      await expect(dialog.getByTestId("telegram-connect")).toBeVisible();
      await expectNoSidewaysOverflow(page, "[role='dialog']");
    });
  }
});
