import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fixtureItem, fixtureStatus } from "../src/chartAnalyze/fixtures";
import type { AnalysisItem, AnalyzeStatus } from "../src/chartAnalyze/types";
import { mockCopilotNumbers } from "./copilotMocks";
import { drawnChartPng } from "./drawnChart";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M59 CR-2: a chart in the Copilot's Ask box. The attach button ("Upload a chart" / "Paste a TradingView snapshot
 * link") sends the chart through the same chart_analyze routes as the Analyze a chart page (answered here by the fake
 * engine's made-up result), and the turn shows the annotated chart and the reading, then the Copilot's own structure
 * card for the symbol the picture shows (CR-1). CR-3: with the engine missing a user has no button (and the plain line
 * if the engine goes while the flag is on); the admin gets the owner action. M59_SHOTS=<dir> saves 390 / 1440 shots.
 */
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as {
  home: Record<string, unknown>; answers: Record<string, Record<string, unknown>>;
};
const PNG = drawnChartPng();
const ADMIN_ME = { id: 1, email: "ops@example.com", tenant_id: 1, role: "SUPER_ADMIN", mfa_enabled: true, email_verified: true, scopes: [] };
const MISSING: AnalyzeStatus["engine"] = { installed: false, message: "Analysis engine not installed" };
const ADMIN_MISSING: AnalyzeStatus["engine"] = { ...MISSING, reason: "the private package is not installed on this server",
  owner_steps: "The owner adds the host's read-only deploy key and ATP_PRIVATE_REF (a tag of atp-private), then runs the deploy." };
const CARD = {
  ...F.answers["Read the NIFTY chart"],
  structure_card: {
    symbol: "NIFTY", price: 24850.5, timeframe: "Daily",
    timeframes: [
      { tf: "1w", label: "Weekly", ready: true, trend: "sideways", weak: false, protected: null, last_break: null },
      { tf: "1d", label: "Daily", ready: true, trend: "up", weak: false, protected: 24310, last_break: { label: "Swing break", price: 24790 } },
      { tf: "1h", label: "1-hour", ready: false, trend: null, weak: false, protected: null, last_break: null },
      { tf: "15m", label: "15-minute", ready: true, trend: "up", weak: false, protected: 24730.5, last_break: null },
    ],
    areas_above: [{ side: "supply", from: 25080, to: 25160, where: "above", distance: 229.5 }],
    areas_below: [{ side: "demand", from: 24520, to: 24610, where: "below", distance: 240.5 }],
    swing_levels: [{ side: "high", price: 25010.5 }, { side: "low", price: 24600.5 }],
    watch: "Whether the daily protected level holds or breaks on a closed bar, and how the price behaves when it reaches the nearest area.",
  },
};

/** The fake engine's made-up result for a chart whose picture reads "NIFTY". */
function analysed(id: number): AnalysisItem {
  const item = fixtureItem(id, "forming");
  return { ...item, result: { ...item.result!, recognised: { ...item.result!.recognised, symbol: "NIFTY" } } };
}

interface Seen { adds: { link: string | null; file: boolean }[]; analyzes: number; asked: string[]; wording: boolean[] }

async function open(page: Page, { chart = true, engine = fixtureStatus().engine, admin = false }: { chart?: boolean; engine?: AnalyzeStatus["engine"]; admin?: boolean } = {}): Promise<Seen> {
  const seen: Seen = { adds: [], analyzes: 0, asked: [], wording: [] };
  await page.clock.install({ time: new Date("2026-10-21T09:00:00+05:30") });
  await page.addInitScript(() => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* none */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, ...(chart ? { chart_analyze: true } : {}) }, brokers: [] });
  if (admin) await page.route(/\/api(?:\/v1)?\/auth\/me$/, (route: Route) => route.fulfill({ json: ADMIN_ME }));
  await mockCopilotNumbers(page);
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, (route) => {
    const r = route.request();
    if (r.url().endsWith("/history")) return route.fulfill({ json: { turns: [], keep: 20 } });
    if (r.method() === "GET") return route.fulfill({ json: F.home });
    const { message, wording } = r.postDataJSON() as { message: string; wording?: boolean };
    seen.asked.push(message);
    seen.wording.push(wording !== false);
    return route.fulfill({ json: message === "Read the NIFTY chart" ? CARD : F.answers["Should I buy RELIANCE today?"] });
  });
  await page.route(/\/api(?:\/v1)?\/chart-analyze\/.*/, async (route: Route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api(?:\/v1)?\/chart-analyze/, "");
    const method = route.request().method();
    if (path === "/status") return route.fulfill({ json: fixtureStatus({ engine }) });
    if (path === "/items" && method === "POST") {
      const body = route.request().postDataBuffer()?.toString("latin1") ?? "";
      const link = /name="link"\r\n\r\n([^\r]*)/.exec(body)?.[1] ?? null;
      seen.adds.push({ link, file: /name="file"/.test(body) });
      return route.fulfill({ status: 201, json: fixtureItem(8, null) });
    }
    if (path === "/items/8/analyze") { seen.analyzes += 1; return route.fulfill({ json: analysed(8) }); }
    if (path === "/items/8/image") return route.fulfill({ body: PNG, contentType: "image/png" });
    return route.fulfill({ json: { items: [] } });
  });
  await page.goto("/copilot");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await expect(page.locator("[data-page='copilot-v2']")).toBeVisible({ timeout: 15_000 });
  return seen;
}

async function upload(page: Page) {
  await page.getByRole("button", { name: "Add a chart" }).click();
  await expect(page.locator("[data-attach-menu]")).toContainText("Alt+S");
  await page.locator("[data-attach-file]").setInputFiles({ name: "drawn-chart.png", mimeType: "image/png", buffer: PNG });
}

for (const [width, height] of [[390, 844], [1440, 900]] as const) {
  test.describe(`v2 copilot - M59 CR-2 a chart in the Ask box, ${width}`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });

    test("upload: the annotated chart and the reading, then ATP's own structure card - in one thread turn", async ({ page }) => {
      const seen = await open(page);
      await upload(page);
      const turn = page.locator("[data-turn]").last();
      await expect(turn).toContainText("Analyze my chart");
      await expect(turn.locator("[data-chart-turn='done'] [data-testid='analysis-result']")).toBeVisible();
      await expect(turn.locator("[data-block='ca-annotated']")).toBeVisible();
      await expect(turn.locator("[data-chart-reading] [data-structure-card='NIFTY']")).toBeVisible();
      await expect(turn.locator("[data-not-ready='1h']")).toHaveText("1-hour: data not ready");
      await expect(turn.locator("a[data-chart-open-page]")).toHaveAttribute("href", "/chart-analyze");
      await expect(turn.getByRole("button", { name: /\b(buy|sell)\b|place order/i })).toHaveCount(0);
      expect(seen.adds).toEqual([{ link: null, file: true }]);
      expect(seen.analyzes).toBe(1);
      expect(seen.asked).toEqual(["Read the NIFTY chart"]);
      expect(seen.wording).toEqual([false]);                       // no AI wording (no metered AI call) until the user taps
      if (width === 390) await expectPhoneLayout(page);
      const dir = process.env.M59_SHOTS;
      if (dir) {
        await page.waitForTimeout(800);
        await page.screenshot({ path: `${dir}/copilot-attach-result-${width}.png`, fullPage: true });
      }
    });
  });
}

test.describe("v2 copilot - M59 CR-2 attach, 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("the AI words come only on the user's tap: 'Explain in words' asks the ordinary question", async ({ page }) => {
    const seen = await open(page);
    await upload(page);
    const explain = page.locator("[data-explain-words]");
    await expect(explain).toBeVisible();
    expect(seen.wording).toEqual([false]);
    await explain.click();
    await expect(page.locator("[data-turn]")).toHaveCount(2);
    expect(seen.asked).toEqual(["Read the NIFTY chart", "Read the NIFTY chart"]);
    expect(seen.wording).toEqual([false, true]);
  });

  test("the menu above the Ask box: two choices and the Alt+S line", async ({ page }) => {
    await open(page);
    await page.getByRole("button", { name: "Add a chart" }).click();
    const menu = page.locator("[data-attach-menu]");
    await expect(menu.getByRole("menuitem")).toHaveText(["Upload a chart", "Paste a TradingView snapshot link"]);
    await expectPhoneLayout(page);
    const dir = process.env.M59_SHOTS;
    if (dir) await page.screenshot({ path: `${dir}/copilot-attach-menu-390.png` });
  });

  test("a TradingView snapshot link goes through the same pipeline", async ({ page }) => {
    const seen = await open(page);
    await page.getByRole("button", { name: "Add a chart" }).click();
    await page.getByRole("menuitem", { name: "Paste a TradingView snapshot link" }).click();
    await page.getByLabel("TradingView snapshot link").fill("https://www.tradingview.com/x/Zx9Abc12/");
    await page.locator("[data-attach-go]").click();
    const turn = page.locator("[data-turn]").last();
    await expect(turn).toContainText("Analyze my TradingView snapshot");
    await expect(turn.locator("[data-testid='analysis-result']")).toBeVisible();
    expect(seen.adds).toEqual([{ link: "https://www.tradingview.com/x/Zx9Abc12/", file: false }]);
  });

  test("CR-3: a user with the engine missing has no attach button; if the engine goes while on, the plain line", async ({ page }) => {
    await open(page, { chart: false, engine: MISSING });
    await expect(page.getByRole("button", { name: "Add a chart" })).toHaveCount(0);
    await page.close();
  });

  test("CR-3: the flag on but the engine gone - a user's turn says it is being switched on, no internal names", async ({ page }) => {
    const seen = await open(page, { engine: MISSING });
    await upload(page);
    const turn = page.locator("[data-chart-turn='unavailable']");
    await expect(turn).toHaveAttribute("data-audience", "user");
    await expect(turn).toContainText("Chart upload analysis is being switched on");
    await expect(turn).not.toContainText(/owner action|private package|ATP_PRIVATE_REF/);
    expect(seen.adds).toEqual([]);
  });

  test("CR-3: the admin with the engine missing gets the button and the owner action", async ({ page }) => {
    await open(page, { chart: false, engine: ADMIN_MISSING, admin: true });
    await upload(page);
    const turn = page.locator("[data-chart-turn='unavailable']");
    await expect(turn).toHaveAttribute("data-audience", "admin");
    await expect(turn).toContainText("Chart analysis engine not installed — owner action");
    await expect(turn).toContainText("ATP_PRIVATE_REF");
    const dir = process.env.M59_SHOTS;
    if (dir) await page.screenshot({ path: `${dir}/copilot-attach-admin-missing-390.png`, fullPage: true });
  });
});
