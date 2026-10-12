import { expect, test, type Page } from "@playwright/test";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";

/**
 * U5 D5: the Screener page's quality bar on the result views (U5 D3a / D3b), against the production build with every
 * /api call answered here (no backend):
 *  - keyboard only: a starter, the symbols, Run, then every result view through the tablist;
 *  - accessibility: axe-core (the engine Lighthouse's accessibility audit runs) finds no serious or critical issue in
 *    the results column with each view shown (the spec's "Lighthouse accessibility >= 95" - OPEN_QUESTIONS D5-1, D5-2);
 *  - reduce motion: with the trader's setting or the system's, the result board does not animate;
 *  - visual regression: the results column in each view, dark / light / colour-blind, compared with the committed
 *    images (DejaVu Sans, frozen clock). After an intended change: `npx playwright test e2e/screenerViews.e2e.ts
 *    --project=desktop --update-snapshots`, check the images, commit.
 */
const require_ = createRequire(import.meta.url);
const AXE = readFileSync(require_.resolve("axe-core/axe.min.js"), "utf8");
const read = (path: string): unknown => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
const registry = (read("../src/screener/builderTexts.json") as { registry: unknown }).registry;
const taxonomy = read("../src/screener/taxonomySample.json");

const SYMBOLS = ["ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT"];
const RESULTS = SYMBOLS.map((s, i) => ({ symbol: s, matched: i % 2 === 0, reason: i === 5 ? "not enough history on 1d" : null }));
const RUN = {
  run_id: 7, preview: false, text: "RSI(14) > 60 AND close > EMA(close, 20)", base_tf: "1d", data_source: "broker:test", scanned: SYMBOLS.length,
  matched: RESULTS.filter((r) => r.matched).map((r) => r.symbol), results: RESULTS, disclaimer: "Research only - not a recommendation.",
  funnel: { universe: SYMBOLS.length, with_data: 5, stages: [{ text: "RSI(14) > 60", survivors: 4 }, { text: "close > EMA(close, 20)", survivors: 3 }] },
};
const day = (i: number) => Date.UTC(2026, 9, 1 + i, 10) / 1000;
const CONTEXT = {
  as_of: "2026-10-08T05:00:00Z", exchange: "NSE", note: "Sector and market cap from the company reference data; closes from the shared daily history.",
  symbols: SYMBOLS.map((s, i) => ({
    symbol: s, sector: i < 3 ? "Energy" : i < 5 ? "IT" : "Unclassified", industry: null, market_cap: i === 4 ? null : 1000 * (6 - i), cap_category: null,
    closes: Array.from({ length: 6 }, (_, k) => ({ t: day(k), c: 100 + k * (i % 2 ? -1 : 1) })), change_pct: i === 5 ? null : (i % 2 ? -1.2 : 0.8) * (i + 1),
  })),
};
const QUADS = { "true,true": "Leading", "true,false": "Weakening", "false,false": "Lagging", "false,true": "Improving" } as const;
const ORBIT = [[1.5, 1.0], [1.2, -0.8], [-1.4, -1.1], [-0.9, 0.7], [0.6, 1.6]];          // where each symbol ends, around (100, 100)
const RRG = {
  as_of: "2026-10-08T05:00:00Z", benchmark: "Nifty 50", benchmark_key: "NSE_INDEX:NIFTY50", weeks: 10, reason: null, note: "An open approximation.",
  symbols: SYMBOLS.map((s, i) => {
    if (i === 5) return { symbol: s, quadrant: null, tail: [] };
    const [dx, dy] = ORBIT[i];
    const tail = Array.from({ length: 5 }, (_, k) => ({ week: `2026-W${36 + k}`, rs_ratio: 100 + dx * (0.4 + 0.15 * k), rs_momentum: 100 + dy * (0.4 + 0.15 * k) }));
    const last = tail[4];
    return { symbol: s, quadrant: QUADS[`${last.rs_ratio >= 100},${last.rs_momentum >= 100}` as keyof typeof QUADS], tail };
  }),
};

async function screener(page: Page, appearance: Record<string, unknown> = { theme: "dark" }) {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  await page.addInitScript((a) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify(a));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, appearance);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page);
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/taxonomy$/.test(url)) return route.fulfill({ json: taxonomy });
    if (/\/legends$/.test(url)) return route.fulfill({ status: 503, headers: { "X-Feature-Disabled": "screener_legends" }, json: { detail: "disabled" } });
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: RUN.text, plan: { cost: 1, lookback: {} } } });
    if (/\/run$/.test(url)) {
      const body = route.request().postDataJSON() as { preview?: boolean };
      return body.preview ? route.fulfill({ json: { ...RUN, run_id: null, preview: true, matched_count: RUN.matched.length } }) : route.fulfill({ json: RUN });
    }
    if (/\/board\/context$/.test(url)) return route.fulfill({ json: CONTEXT });
    if (/\/board\/rrg$/.test(url)) return route.fulfill({ json: RRG });
    if (/\/board\/template-runs$/.test(url)) return route.fulfill({ json: { runs: {} } });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.goto("/screener");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await expect(page.getByText("Start with a template or add your first condition.")).toBeVisible();
}

async function runByKeyboard(page: Page) {
  const starter = page.getByRole("button", { name: "Momentum", exact: true });
  await starter.focus();
  await page.keyboard.press("Enter");
  await page.locator("#screener-symbols").focus();
  await page.keyboard.type(SYMBOLS.join(" "));
  await expect(page.getByTestId("run-scan")).toBeEnabled();
  await expect(page.getByText(/^Matched \d+ of/).first()).toBeVisible();          // the live preview has answered
  await page.getByTestId("run-scan").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("tablist", { name: "Result view" })).toBeVisible();
}

async function axeProblems(page: Page): Promise<string[]> {
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async () => {
    const w = window as unknown as { axe: { run: (ctx: unknown, opts: unknown) => Promise<{ violations: { id: string; impact: string; nodes: unknown[] }[] }> } };
    // the results column (the D3 views and the board); the page-wide primary-button contrast is OPEN_QUESTIONS D5-2
    const r = await w.axe.run(document.querySelector(".screener-results") ?? document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    return r.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => `${v.id}: ${(v.nodes as { target: unknown }[]).map((x) => JSON.stringify(x.target)).join(", ")}`);
  });
}

const VIEWS = ["Table", "Heatmap", "Chart grid", "RRG"] as const;

// Desktop only: the phone layout (its own Run bar and stacked columns) has its own suite (mobile.e2e.ts).
test.beforeEach(async ({ page: _page }, info) => { test.skip(info.project.name !== "desktop", "desktop layout"); });

test("keyboard only: a scan, then every result view through the tablist", async ({ page }) => {
  await screener(page);
  await runByKeyboard(page);
  const tabs = page.getByRole("tablist", { name: "Result view" });
  for (const name of VIEWS) {
    const tab = tabs.getByRole("tab", { name });
    await tab.focus();
    await page.keyboard.press("Enter");
    await expect(tab).toHaveAttribute("aria-selected", "true");
  }
  await tabs.getByRole("tab", { name: "Heatmap" }).click();
  await expect(page.getByTestId("heatmap-view").getByRole("img", { name: /^ALPHA: matched/ })).toBeVisible();
  await tabs.getByRole("tab", { name: "Chart grid" }).click();
  await expect(page.getByTestId("chart-grid").locator("li")).toHaveCount(RESULTS.filter((r) => r.matched).length);
  await tabs.getByRole("tab", { name: "RRG" }).click();
  await expect(page.getByTestId("rrg-view").getByRole("img", { name: /Relative rotation against Nifty 50: ALPHA Leading, BRAVO Weakening, CHARLIE Lagging, DELTA Improving/ })).toBeVisible();
});

test("accessibility: no serious or critical axe issue with each view shown", async ({ page }) => {
  await screener(page);
  await runByKeyboard(page);
  for (const name of VIEWS) {
    await page.getByRole("tablist", { name: "Result view" }).getByRole("tab", { name }).click();
    expect(await axeProblems(page), name).toEqual([]);
  }
});

test("reduce motion: the result board does not animate", async ({ page }) => {
  await screener(page, { theme: "dark", reduceMotion: true });
  await runByKeyboard(page);
  const anim = await page.locator(".board-settle").first().evaluate((el) => getComputedStyle(el).animationName);
  expect(anim).toBe("none");
});

for (const appearance of [{ theme: "dark" }, { theme: "light" }, { theme: "dark", colorBlind: true }]) {
  const tag = `${appearance.theme}${"colorBlind" in appearance ? "-cvd" : ""}`;
  test(`visual: the result views (${tag})`, async ({ page }) => {
    await screener(page, { ...appearance, reduceMotion: true });
    await runByKeyboard(page);
    for (const name of ["Heatmap", "RRG"] as const) {
      await page.getByRole("tablist", { name: "Result view" }).getByRole("tab", { name }).click();
      const section = page.getByRole("region", { name });
      await expect(section).toBeVisible();
      await expect(section).toHaveScreenshot(`screener-${name.toLowerCase()}-${tag}.png`, { maxDiffPixelRatio: 0.002 });
    }
  });
}
