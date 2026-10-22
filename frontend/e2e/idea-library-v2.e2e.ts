import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Idea library slice 1 at /ideas (ui_v2 + idea_library): the method cards T1-T8, the education frame, the beginner
 * questions, a card's full idea, "Try as a scan" into Find setups, and nothing at all while the flag is off.
 * - Baselines (NEW): `idea-library-v2-{1440,390,360}-{light,dark}.png`. Other baselines are untouched.
 * - Slice 2 baselines (NEW): `idea-library-adopt-v2-{1440,390}-{light,dark}.png` (a mapped idea's card, IDEA-1).
 * The library is the server's own payload (e2e/fixtures/ideas_v2.json, checked by backend/tests/test_idea_library.py);
 * the stages reply is what the server answers for T5's first scan. No real account, symbol or price anywhere.
 * After an intended change: `npx playwright test --project=phone e2e/idea-library-v2.e2e.ts --update-snapshots`.
 */

const LIB = JSON.parse(readFileSync(new URL("./fixtures/ideas_v2.json", import.meta.url), "utf8")) as {
  ideas: { id: string; name: string; scans: { id: string; available: boolean }[] }[];
};
const registry = (JSON.parse(readFileSync(new URL("../src/screener/builderTexts.json", import.meta.url), "utf8")) as { registry: unknown }).registry;
const T5_STAGES = {
  name: null, base_tf: "15m", text: "RetestEntry(\"up\", \"swing\", 10, TRUE)", version: "screenql/1",
  stages: [{ text: "RetestEntry(\"up\", \"swing\", 10, TRUE)", wrapped: "RetestEntry(\"up\", \"swing\", 10, TRUE)",
    ast: { k: "Call", name: "RetestEntry", args: [{ k: "Str", v: "up" }, { k: "Str", v: "swing" }, { k: "Num", v: 10.0 }, { k: "Bool", v: true }], kwargs: {}, offset: 0, tf: null } }],
};
const ON = { idea_library: true, screener_v2: true, screener_builder_u2: true };

interface Sent { stages: unknown[] }

/** Slice 2 (IDEA-1): the library with T5 mapped onto a template the organisation can adopt. No idea is mapped on main
 * today (none shares a template's structure), so this is the state the card takes once one is. */
const MAPPED = { ...LIB, ideas: LIB.ideas.map((i) => i.id === "T5"
  ? { ...i, adopt: { available: true, template: "D1", timeframe: "15m", params: { period: 12 } } } : i) };

async function open(page: Page, path: string, o: { theme?: "light" | "dark"; features?: Record<string, boolean>; library?: unknown } = {}): Promise<Sent> {
  const sent: Sent = { stages: [] };
  await page.addInitScript((theme) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, o.theme ?? "light");
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const features = { ...(F.features as Record<string, boolean>), ...(o.features ?? ON) };
  await mockApi(page, { features, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { me: true });
  await page.route(api("/ideas(/[A-Za-z0-9]+)?$"), (route) => {
    if (!features.idea_library) return route.fulfill({ status: 404, headers: { "X-Feature-Disabled": "idea_library" }, json: { detail: "Not found" } });
    const m = /\/ideas\/([A-Za-z0-9]+)$/.exec(route.request().url());
    if (!m) return route.fulfill({ json: o.library ?? LIB });
    const idea = LIB.ideas.find((i) => i.id === m[1]);
    return idea ? route.fulfill({ json: idea }) : route.fulfill({ status: 404, json: { detail: "no such idea" } });
  });
  await page.route(/\/api(\/v1)?\/screener\//, (route) => {
    const r = route.request();
    const url = r.url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/stages$/.test(url)) { sent.stages.push(r.postDataJSON()); return route.fulfill({ json: T5_STAGES }); }
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: T5_STAGES.text, plan: { cost: 1, lookback: {} } } });
    return route.fulfill({ status: 503, headers: { "X-Feature-Disabled": "screener_gallery" }, json: { detail: "disabled" } });
  });
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return sent;
}

const SHOT = { animations: "disabled" as const, fullPage: true, maxDiffPixelRatio: 0.02, timeout: 15_000 };

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true },
  { name: "360", width: 360, height: 780, mobile: true }]) {
  test.describe(`Idea library ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as const) {
      test(`the page looks the same (${theme})`, async ({ page }) => {
        await open(page, "/ideas", { theme });
        await expect(page.locator("[data-idea]")).toHaveCount(8);
        await expect(page).toHaveScreenshot(`idea-library-v2-${size.name}-${theme}.png`, SHOT);
      });
    }

    if (size.mobile) {
      test("no sideways scroll, 44 px targets, nothing hidden behind the bottom bar", async ({ page }) => {
        await open(page, "/ideas");
        await page.locator("[data-idea='T1'] summary").click();
        await expectPhoneLayout(page);
      });
    }
  });
}

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true }]) {
  test.describe(`Idea library adopt ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as const) {
      test(`a mapped idea's card with Adopt as a paper strategy looks the same (${theme})`, async ({ page }) => {
        await open(page, "/ideas", { theme, library: MAPPED, features: { ...ON, strategy_templates: true } });
        const card = page.locator("[data-idea='T5']");
        await card.locator("summary").click();
        await expect(card.locator("[data-state='adopt-ready']")).toBeVisible();
        await expect(card).toHaveScreenshot(`idea-library-adopt-v2-${size.name}-${theme}.png`, SHOT);
      });
    }

    if (size.mobile) {
      test("adopt: no sideways scroll, 44 px targets", async ({ page }) => {
        await open(page, "/ideas", { library: MAPPED, features: { ...ON, strategy_templates: true } });
        await expectPhoneLayout(page);
      });
    }
  });
}

test.describe("Idea library behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("flag off: no Ideas entry and the address shows Coming soon", async ({ page }) => {
    await open(page, "/ideas", { features: { screener_v2: true } });
    await expect(page.getByText("Coming soon")).toBeVisible();
    await expect(page.locator("[data-idea]")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Ideas", exact: true })).toHaveCount(0);
  });

  test("flag on: the Ideas entry under Strategies, the frame first, a card's full idea", async ({ page }) => {
    await open(page, "/ideas");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Idea library");
    await expect(page.getByRole("link", { name: "Ideas", exact: true })).toBeVisible();
    await expect(page.locator("[data-block='idea-frame']")).toContainText("Education, not advice");
    const card = page.locator("[data-idea='T1']");
    await expect(card.getByText("In plain words")).toBeHidden();
    await card.getByText("Read the full idea").click();
    await expect(card.getByText("In plain words")).toBeVisible();
    await expect(card.getByText("Always your decision")).toBeVisible();
    await expect(card.locator("[data-state='adopt-later']")).toContainText("on paper only");
    // white box, no advice: nothing on the page reads like a trade instruction or a claim
    const text = (await page.locator("main").innerText()).replace(/\s+/g, " ");
    expect(text).not.toMatch(/\b(buy|sell|target|stop-loss|stoploss|profit|win rate)\b/i);
    expect(text).not.toMatch(/\bLIVE\b/);
  });

  test("slice 2: no idea on main is adoptable yet", async ({ page }) => {
    await open(page, "/ideas", { features: { ...ON, strategy_templates: true } });
    await expect(page.locator("[data-idea]")).toHaveCount(8);
    await expect(page.locator("[data-adopt]")).toHaveCount(0);
  });

  test("slice 2: a mapped idea is not offered while strategy templates are off", async ({ page }) => {
    await open(page, "/ideas", { library: MAPPED, features: { ...ON, strategy_templates: false } });
    await expect(page.locator("[data-idea]")).toHaveCount(8);
    await expect(page.locator("[data-adopt]")).toHaveCount(0);
  });

  test("slice 2: a mapped idea links to the Paper bot form with its template and settings", async ({ page }) => {
    await open(page, "/ideas", { library: MAPPED, features: { ...ON, strategy_templates: true } });
    const link = page.locator("[data-idea='T5'] [data-adopt='D1']");
    await expect(link).toHaveText("Adopt as a paper strategy");
    await expect(link).toHaveAttribute("href", "/deployments?template=D1&timeframe=15m&set.period=12");
    await expect(page.locator("[data-adopt]")).toHaveCount(1);
    await expect(page.locator("[data-idea='T5'] [data-block='idea-adopt']")).toContainText("runs on paper only");
  });

  test("filter by group, and the beginner questions suggest ideas to read", async ({ page }) => {
    await open(page, "/ideas");
    const groups = page.locator("[data-block='idea-groups']");
    await groups.getByRole("button", { name: /^Range/ }).click();
    await expect(page.locator("[data-idea]")).toHaveCount(1);
    await groups.getByRole("button", { name: /^All ideas/ }).click();
    await expect(page.locator("[data-idea]")).toHaveCount(8);

    const beginner = page.locator("[data-block='idea-beginner']");
    await beginner.getByRole("button", { name: "Work inside a range" }).click();
    await beginner.getByRole("button", { name: "A few times a day or less" }).click();
    await expect(page.locator("[data-block='idea-suggestions']")).toHaveCount(0);
    await beginner.getByRole("button", { name: "Yes" }).click();
    const picks = page.locator("[data-block='idea-suggestions'] a");
    await expect(picks).toHaveText(["Range edges", "Trend pullback to demand or supply", "Options seller pack"]);
    await picks.last().click();
    await expect(page).toHaveURL(/#idea-T8$/);
  });

  test("Try as a scan opens the idea's screen in Find setups with its settings as editable blocks", async ({ page }) => {
    const sent = await open(page, "/ideas");
    const t1 = page.locator("[data-idea='T1']");
    await expect(t1.locator("[data-state='scan-off']")).toBeVisible();            // market zones are off for this account
    await page.locator("[data-idea='T5'] [data-scan='above']").click();
    await expect(page).toHaveURL(/\/screener$/);
    await expect(page.getByLabel("Scan name")).toHaveValue("Breakout and retest - Retest from above");
    expect(sent.stages).toEqual([{ source: "RetestEntry(\"up\", $retest_of, $retest_max_bars, TRUE)", base_tf: "15m",
      params: { retest_of: "swing", retest_max_bars: 10 } }]);
    await expect(page.getByRole("list", { name: "Stages - every one must pass" }).locator("li")).not.toHaveCount(0);
  });
});
