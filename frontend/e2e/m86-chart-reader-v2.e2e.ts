import { expect, test, type Page, type Route } from "@playwright/test";
import { fixtureStatus } from "../src/chartAnalyze/fixtures";
import { fixtureAbhiResult, fixtureBookResult, fixtureStrategies } from "../src/chartReader/fixtures";
import type { ReaderRequest, ReaderStrategies } from "../src/chartReader/types";
import { pickOption } from "./combobox";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M86 W3: the chart reader's cards inside the Analyze-a-chart panel (flag `chart_reader`), against MOCKED answers built
 * from made-up fixtures (src/chartReader/fixtures.ts) - no real read, no real book content (public repository).
 * - Nothing is read on its own: the list of strategies is loaded, a read runs only on the button.
 * - "Abhi's method" card: trend / areas / correction / commitment chips, the sentences, label, cost, cached.
 * - "Book strategies": each read opens its own card (they stack, each closes); the two-chart note.
 * - Not available, refused and error states; the flag off hides it all.
 * - 390 (phone rules: no sideways scroll, 44 px targets) and 1440; no buy / sell / hold / target / stop-loss words.
 * M86_SHOTS=<dir> saves the dark 390 / 1440 shots there (docs/design/m86/ for the PR).
 */

const ON: MockOptions = { features: { ui_v2: true, chart_analyze: true, chart_reader: true }, brokers: [] };
const READER_OFF: MockOptions = { features: { ui_v2: true, chart_analyze: true }, brokers: [] };
const BANNED = /\b(buy|buys|buying|bought|sell|sells|selling|sold|hold|holds|holding|held|target|targets|stop[\s-]?loss|SL|strike|strikes|lots?)\b/i;

interface Calls { reads: ReaderRequest[]; lists: number }
type ReadMode = "ok" | "refused" | "error";

async function open(page: Page, { options = ON, list = fixtureStrategies(), mode = "ok" as ReadMode, cachedRepeat = false } = {}): Promise<Calls> {
  const calls: Calls = { reads: [], lists: 0 };
  await page.clock.install({ time: new Date("2026-10-20T10:30:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, options);
  await page.route(/\/api(?:\/v1)?\/chart-analyze\/.*/, (route: Route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api(?:\/v1)?\/chart-analyze/, "");
    if (path === "/status") return route.fulfill({ json: fixtureStatus() });
    return route.fulfill({ json: { items: [] } });
  });
  await page.route(/\/api(?:\/v1)?\/chart-reader\/strategies$/, (route: Route) => { calls.lists += 1; return route.fulfill({ json: list }); });
  await page.route(/\/api(?:\/v1)?\/chart-reader\/read$/, async (route: Route) => {
    const body = route.request().postDataJSON() as ReaderRequest;
    calls.reads.push(body);
    if (mode === "error") return route.fulfill({ status: 500, json: { detail: "Example server problem" } });
    if (body.strategy === "ABHI") {
      if (mode === "refused") return route.fulfill({ json: fixtureAbhiResult({ status: "refused", read: null }) });
      const repeat = calls.reads.filter((r) => r.strategy === "ABHI").length > 1;
      return route.fulfill({ json: fixtureAbhiResult(cachedRepeat && repeat ? { cached: true, cost_usd: 0 } : {}) });
    }
    return route.fulfill({ json: fixtureBookResult(body.strategy) });
  });
  return calls;
}

async function shot(page: Page, name: string) {
  const dir = process.env.M86_SHOTS;
  if (!dir) return;
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.getByTestId("chart-reader").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${dir}/${name}`, fullPage: true });
}

async function expectNoCallWords(page: Page) {
  const text = await page.locator("main").innerText();
  expect(text.match(BANNED)?.[0] ?? null, "a call word in the rendered text").toBeNull();
}

async function expectNoSidewaysScroll(page: Page) {
  const { scroll, width } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(scroll).toBeLessThanOrEqual(width);
}

async function readAll(page: Page, calls: Calls) {
  const reader = page.getByTestId("chart-reader");
  await expect(reader).toBeVisible();
  // nothing is read on its own: only the free list, the estimate before any read
  await expect(page.getByTestId("reader-estimate")).toHaveText("est. $0.07 per read");
  expect(calls.lists).toBe(1);
  expect(calls.reads).toHaveLength(0);
  const readButton = page.getByTestId("reader-abhi-read");
  await expect(readButton).toBeDisabled();                                  // no symbol yet
  await page.getByTestId("reader-symbol").fill("demo");
  await readButton.click();

  const abhi = page.getByTestId("reader-abhi");
  await expect(abhi.getByTestId("abhi-read")).toBeVisible();
  expect(calls.reads[0]).toEqual({ strategy: "ABHI", source: "lake", symbol: "DEMO" });
  await expect(abhi.locator('[data-chip="trend-up"]')).toHaveText(/Up/);
  await expect(abhi.locator('[data-chip="area-buyer"]')).toBeVisible();
  await expect(abhi.locator('[data-chip="area-seller"]')).toBeVisible();
  await expect(abhi).toContainText("101.20 – 102.80");
  await expect(abhi).toContainText("109.10 – 110.40");
  await expect(abhi.locator('[data-chip="correction-yes"]')).toBeVisible();
  await expect(abhi.locator('[data-chip="momentum-yes"]')).toBeVisible();
  await expect(abhi.locator('[data-chip="commitment-no"]')).toBeVisible();
  await expect(abhi).toContainText("Example area of interest");
  await expect(abhi.locator('[data-chip="reader-label"]')).toHaveText("Advanced analysis (code+AI)");
  await expect(abhi.getByTestId("reader-cost")).toContainText("Cost $0.07");
  await expect(abhi).not.toContainText("Placeholder: each swing");         // evidence behind (?)

  // two book strategies, each its own card (stacked), never inside Abhi's card
  const pick = page.getByTestId("reader-book-pick");
  await pickOption(page, pick, "DEMO_ONE");
  await page.getByTestId("reader-book-read").click();
  await pickOption(page, pick, "DEMO_TWO");
  await page.getByTestId("reader-book-read").click();
  const books = page.getByTestId("reader-book");
  await expect(books).toHaveCount(2);
  await expect(books.nth(0).getByRole("heading")).toHaveText("A. Writer, Example Book One (1901) strategy");
  await expect(books.nth(1).getByRole("heading")).toHaveText("B. Author, Example Book Two (1902) strategy");
  for (const status of ["met", "not_met", "unclear"]) await expect(books.nth(0).locator(`[data-chip="condition-${status}"]`)).toBeVisible();
  await expect(books.nth(0).getByTestId("condition-counts")).toHaveText("1 met · 1 not met · 1 unclear");
  await expect(books.nth(0).locator('[data-chip="side-long"]')).toBeVisible();
  await expect(books.nth(0)).toContainText("Bar 42");
  await expect(books.nth(0).locator('[data-chip="invalidated-no"]')).toBeVisible();
  await expect(books.nth(0).getByTestId("reader-two-chart-note")).toHaveCount(0);
  await expect(books.nth(1).getByTestId("reader-two-chart-note")).toBeVisible();
  await expect(abhi.getByTestId("reader-book")).toHaveCount(0);
  expect(calls.reads.map((r) => r.strategy)).toEqual(["ABHI", "DEMO_ONE", "DEMO_TWO"]);
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  test.describe(`M86 chart reader, ${size.width} x ${size.height}`, () => {
    test.use(size.phone ? { viewport: { width: size.width, height: size.height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 }
      : { viewport: { width: size.width, height: size.height } });

    test("Abhi's card, two book cards, the chips, no call words, no sideways scroll", async ({ page }) => {
      const calls = await open(page);
      await page.goto("/chart-analyze");
      await readAll(page, calls);
      await expectNoCallWords(page);
      await expectNoSidewaysScroll(page);
      if (size.phone) await expectPhoneLayout(page);
      await shot(page, `m86-chart-reader-${size.width}-dark.png`);

      // each book card closes on its own; Abhi's card stays
      await page.getByRole("button", { name: "Close A. Writer, Example Book One (1901) strategy" }).click();
      await expect(page.getByTestId("reader-book")).toHaveCount(1);
      await expect(page.getByTestId("reader-abhi").getByTestId("abhi-read")).toBeVisible();

      // the cleaning counts sit behind (?) only
      await page.getByTestId("reader-abhi").getByRole("button", { name: "Help: About this read" }).click();
      await expect(page.getByTestId("reader-about").first()).toContainText("Sentences removed: 1 · Time words flagged: 2");
    });

    test("a repeated read of the same chart shows cached at no cost", async ({ page }) => {
      const calls = await open(page, { cachedRepeat: true });
      await page.goto("/chart-analyze");
      await page.getByTestId("reader-symbol").fill("DEMO");
      await page.getByTestId("reader-abhi-read").click();
      await expect(page.getByTestId("reader-abhi").getByTestId("abhi-read")).toBeVisible();
      await page.getByRole("button", { name: "Read again" }).click();
      await expect(page.getByTestId("reader-abhi").locator('[data-chip="reader-cached"]')).toBeVisible();
      await expect(page.getByTestId("reader-abhi").getByTestId("reader-cost")).toContainText("Cost $0.00 · cached");
      expect(calls.reads).toHaveLength(2);
    });

    test("not available: the reason in plain words, no read button", async ({ page }) => {
      const list: ReaderStrategies = fixtureStrategies({ available: false, reason: "Example reason: switched off for now." });
      await open(page, { list });
      await page.goto("/chart-analyze");
      await expect(page.getByTestId("reader-unavailable")).toContainText("The chart reader is not available right now.");
      await expect(page.getByTestId("reader-unavailable")).toContainText("Example reason: switched off for now.");
      await expect(page.getByTestId("reader-abhi-read")).toHaveCount(0);
      await expectNoSidewaysScroll(page);
      if (size.phone) await expectPhoneLayout(page);
    });

    test("refused and error answers in plain words", async ({ page }) => {
      await open(page, { mode: "refused" });
      await page.goto("/chart-analyze");
      await page.getByTestId("reader-symbol").fill("DEMO");
      await page.getByTestId("reader-abhi-read").click();
      await expect(page.getByTestId("reader-refused")).toContainText("The AI declined to read this chart.");
      await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
      await expectNoCallWords(page);
    });

    test("a failed call says the read did not finish", async ({ page }) => {
      await open(page, { mode: "error" });
      await page.goto("/chart-analyze");
      await page.getByTestId("reader-symbol").fill("DEMO");
      await page.getByTestId("reader-abhi-read").click();
      await expect(page.getByTestId("reader-error")).toContainText("The read did not finish. Please try again.");
    });

    test("the flag off: no chart reader at all, nothing asked", async ({ page }) => {
      const calls = await open(page, { options: READER_OFF });
      await page.goto("/chart-analyze");
      await expect(page.getByTestId("chart-analyze-page")).toBeVisible();
      await expect(page.getByTestId("chart-reader")).toHaveCount(0);
      expect(calls.lists).toBe(0);
    });
  });
}
