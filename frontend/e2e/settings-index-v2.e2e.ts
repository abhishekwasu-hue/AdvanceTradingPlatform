import { expect, test, type Page } from "@playwright/test";
import { expectPhoneLayout } from "./phoneLayout";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { AI_CLAUDE, AI_FAILED, AI_NONE, FEATURES, TELEGRAM_PLATFORM, UPSTOX, ZERODHA_LOGIN, openSettings, type Theme } from "./settingsMocks";
import { warnOnManySolidBrand } from "./solidBrand";

// Chart + colour order Part 3 (CC-8): a warning annotation, never a failure, when a screen shows more than one solid
// brand button (the one main action per screen).
test.afterEach(async ({ page }, testInfo) => { await warnOnManySolidBrand(page, testInfo); });

/**
 * Settings redesign part B ("Settings = a short index with status", behind `ui_v2`), on made-up replies:
 * - the index: one row per thing with its live status chip, the search (row names and the settings inside them), the
 *   old `/settings#<part>` links forwarding to the new pages, "Prepare for LIVE" only once Phase 0 is over, personal API
 *   keys only with `public_api`, and the four tour targets on the rows;
 * - with `ui_v2` off, today's Settings page, and the new addresses going back to it.
 * Baselines (new UI only): `settings-index-v2-<width>-<theme>.png` and `settings-<section>-v2-<width>-<theme>.png`
 * (ai, alerts, appearance, help, advanced, account) at 360, 390 and 1440, light and dark.
 * After an intended change: `npx playwright test --project=phone e2e/settings-index-v2.e2e.ts --update-snapshots`.
 */

const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const index = (page: Page) => page.locator("[data-settings-index]");
const row = (page: Page, id: string) => page.locator(`[data-row='${id}']`);

const SIZES = [
  { width: 360, height: 780, phone: true },
  { width: 390, height: 844, phone: true },
  { width: 1440, height: 900, phone: false },
] as const;
const THEMES: Theme[] = ["light", "dark"];
const SECTIONS = [
  { name: "ai", path: "/settings/ai", ready: "[data-connected-line]" },
  { name: "alerts", path: "/settings/alerts", ready: "#telegram" },
  { name: "appearance", path: "/settings/appearance", ready: "#appearance" },
  { name: "help", path: "/settings/help", ready: "[data-tips-row]" },
  { name: "advanced", path: "/settings/advanced", ready: "#webhook" },
  { name: "account", path: "/settings/account", ready: "#plan" },
] as const;
const FULL = { ai: AI_CLAUDE, channels: [TELEGRAM_PLATFORM], connections: [UPSTOX], tips: "on" as const };

for (const size of SIZES) {
  test.describe(`settings index ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, deviceScaleFactor: 1, ...(size.phone ? { isMobile: true, hasTouch: true } : {}) });

    for (const theme of THEMES) {
      test(`the index looks the same (${theme})`, async ({ page }) => {
        await openSettings(page, "/settings", { theme, ...FULL });
        await expect(row(page, "ai").locator("[data-chip]")).toHaveText("Connected (Claude)");
        await expect(row(page, "account").locator("[data-chip]")).toHaveText("Starter plan");
        await expect(page).toHaveScreenshot(`settings-index-v2-${size.width}-${theme}.png`, SHOT);
      });

      for (const s of SECTIONS) {
        test(`${s.name} looks the same (${theme})`, async ({ page }) => {
          await openSettings(page, s.path, { theme, ...FULL });
          await expect(page.locator(s.ready).first()).toBeVisible();
          await expect(page).toHaveScreenshot(`settings-${s.name}-v2-${size.width}-${theme}.png`, SHOT);
        });
      }
    }

    if (size.phone) {
      test("phone: no sideways scroll, 44 px targets, the chip under the name", async ({ page }) => {
        await openSettings(page, "/settings", FULL);
        await expect(index(page)).toBeVisible();
        await expectPhoneLayout(page);
        for (const link of await index(page).locator("a").all()) expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44);
        const name = await row(page, "broker").getByText("Broker", { exact: true }).boundingBox();
        const chip = await row(page, "broker").locator("[data-chip]").boundingBox();
        expect(chip!.y).toBeGreaterThan(name!.y + name!.height - 1);
      });

      for (const s of SECTIONS) {
        test(`phone: ${s.name} has no sideways scroll`, async ({ page }) => {
          await openSettings(page, s.path, FULL);
          await expect(page.locator(s.ready).first()).toBeVisible();
          await expectPhoneLayout(page);
        });
      }
    }
  });
}

test.describe("settings index behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the rows, each with its chip and where it leads", async ({ page }) => {
    await openSettings(page, "/settings", FULL);
    const rows = index(page).locator("[data-row]");
    await expect(rows).toHaveText([/Account & plan/, /Team/, /Broker/, /AI/, /Alerts/, /My trading defaults/, /Risk limits/, /Appearance/, /Help & tips/, /Set up your account/]);
    // UX3-9: Team and Risk limits left the sidebar; Settings opens them.
    await expect(row(page, "team").locator("a")).toHaveAttribute("href", "/team");
    await expect(row(page, "risk").locator("a")).toHaveAttribute("href", "/risk-management");
    await expect(row(page, "broker").locator("[data-chip]")).toHaveText("Connected (Upstox)");
    await expect(row(page, "broker").locator("a")).toHaveAttribute("href", "/broker");
    await expect(row(page, "alerts").locator("[data-chip]")).toHaveText("Telegram connected");
    await expect(row(page, "defaults").locator("[data-chip]")).toHaveText("Optional");
    await expect(row(page, "defaults").locator("a")).toHaveAttribute("href", "/settings/trading-defaults");
    await expect(row(page, "appearance").locator("[data-chip]")).toHaveText("Light · Normal text");
    await expect(row(page, "help").locator("[data-chip]")).toHaveText("Tips on");
    await expect(row(page, "account").locator("a")).toHaveAttribute("href", "/settings/account");
    await expect(row(page, "advanced").locator("a")).toHaveAttribute("href", "/settings/advanced");
    await row(page, "alerts").locator("a").click();
    await expect(page).toHaveURL(/\/settings\/alerts$/);
    await page.locator("[data-back]").click();
    await expect(page).toHaveURL(/\/settings$/);
  });

  test("the chips per state: nothing connected, a broker to renew, an AI key that failed", async ({ page }) => {
    await openSettings(page, "/settings", { ai: AI_NONE, connections: [], channels: [] });
    await expect(row(page, "broker").locator("[data-chip]")).toHaveText("Not connected");
    await expect(row(page, "ai").locator("[data-chip]")).toHaveText("Not connected");
    await expect(row(page, "alerts").locator("[data-chip]")).toHaveText("Not connected");
    const p2 = await page.context().newPage();
    await openSettings(p2, "/settings", { ai: AI_FAILED, connections: [UPSTOX, ZERODHA_LOGIN], tips: "off" });
    await expect(row(p2, "broker").locator("[data-chip]")).toHaveText("Renew today (Zerodha)");
    await expect(row(p2, "broker").locator("[data-chip]")).toHaveAttribute("data-chip", "warn");
    await expect(row(p2, "ai").locator("[data-chip]")).toHaveText("Needs a fix (Claude)");
    await expect(row(p2, "help").locator("[data-chip]")).toHaveText("Tips off");
  });

  test("without ai_one_key the AI chip reads the organisation's AI service, and the AI page shows today's cards", async ({ page }) => {
    await openSettings(page, "/settings", { features: { ...FEATURES, ai_one_key: false }, provider: { configured: true, enabled: true, provider: "anthropic" } });
    await expect(row(page, "ai").locator("[data-chip]")).toHaveText("Connected (Claude)");
    await page.goto("/settings/ai");
    await expect(page.locator("[data-key-guides]")).toBeVisible();
    await expect(page.locator("[data-connect-ai]")).toHaveCount(0);
  });

  test("search: row names and the settings inside, and it jumps there", async ({ page }) => {
    await openSettings(page, "/settings", FULL);
    const box = page.getByLabel("Search settings");
    await box.fill("telegram");
    const results = page.locator("[data-search-results]");
    await expect(results.locator("[data-hit='Connect Telegram']")).toContainText("in Alerts");
    await box.fill("ai key");
    await expect(results.locator("[data-hit='Connect an AI key']")).toBeVisible();
    await box.fill("text size");
    await expect(results.locator("[data-hit]").first()).toHaveAttribute("data-hit", "Text size");
    await box.press("Enter");
    await expect(page).toHaveURL(/\/settings\/appearance#appearance-text-size-label$/);
    await expect(page.locator("#appearance-text-size-label")).toBeInViewport();
    await page.goto("/settings");
    await page.getByLabel("Search settings").fill("zzqq");
    await expect(page.locator("[data-search-empty]")).toBeVisible();
    await page.getByRole("button", { name: "Clear the search" }).click();
    await expect(index(page)).toBeVisible();
  });

  test("search opens a fold on the page it jumps to", async ({ page }) => {
    await openSettings(page, "/settings", FULL);
    await page.getByLabel("Search settings").fill("webhook");
    await page.locator("[data-search-results] [data-hit='TradingView webhook'] a").click();
    await expect(page).toHaveURL(/\/settings\/advanced#webhook$/);
    await expect(page.locator("#webhook")).toHaveAttribute("open", "");
  });

  for (const [hash, to] of [["#alerts", /\/settings\/alerts$/], ["#ai", /\/settings\/ai$/], ["#integrations", /\/settings\/advanced#webhook$/],
    ["#advanced", /\/settings\/advanced$/], ["#go-live", /\/settings$/]] as const) {
    test(`the old /settings${hash} link opens the new page`, async ({ page }) => {
      await openSettings(page, `/settings${hash}`, FULL);
      await expect(page).toHaveURL(to);
      if (hash === "#go-live") {
        await expect(index(page)).toBeVisible();
        await expect(page.locator("main")).not.toContainText(/LIVE|Go-live/);
      }
    });
  }

  test("Phase 0: no Prepare for LIVE on the index, the Advanced page or the Setup page; after Phase 0 they come back", async ({ page }) => {
    await openSettings(page, "/settings", FULL);
    await expect(index(page)).toBeVisible();
    await expect(page.locator("[data-prepare-live]")).toHaveCount(0);
    await page.goto("/settings/advanced");
    await expect(page.locator("#webhook")).toBeVisible();
    await expect(page.locator("#go-live")).toHaveCount(0);
    await page.goto("/settings/setup");
    await expect(page.locator("[data-page='setup-v2']")).toBeVisible();
    await expect(page.locator("[data-block='prepare-live']")).toHaveCount(0);

    const p2 = await page.context().newPage();
    await openSettings(p2, "/settings", { ...FULL, features: { ...FEATURES, phase0_paper_only: false } });
    await expect(p2.locator("[data-prepare-live]")).toHaveAttribute("href", "/settings/advanced#go-live");
    await p2.goto("/settings#go-live");
    await expect(p2).toHaveURL(/\/settings\/advanced#go-live$/);
    await expect(p2.locator("#go-live")).toHaveAttribute("open", "");
    await p2.goto("/settings/setup");
    await expect(p2.locator("[data-block='prepare-live']")).toHaveAttribute("href", "/settings/advanced#go-live");
  });

  test("Advanced: personal API keys only while public_api is on; the own bot and the Vision switches are there", async ({ page }) => {
    await openSettings(page, "/settings/advanced", FULL);
    await expect(page.locator("#webhook")).toBeVisible();
    await expect(page.locator("#own-bot")).toBeVisible();
    await expect(page.locator("#vision")).toBeVisible();
    await expect(page.locator("#api-keys")).toHaveCount(0);
    const p2 = await page.context().newPage();
    await openSettings(p2, "/settings/advanced", { ...FULL, features: { ...FEATURES, public_api: true } });
    await expect(p2.locator("#api-keys")).toBeVisible();
  });

  test("Alerts: Telegram first, then the other channels folded; the own bot is not here", async ({ page }) => {
    await openSettings(page, "/settings/alerts", FULL);
    await expect(page.locator("#telegram")).toBeVisible();
    await expect(page.locator("#channels")).not.toHaveAttribute("open", "");
    await page.locator("#channels > summary").click();
    await expect(page.locator("[data-telegram-elsewhere]")).toBeVisible();
    await expect(page.locator("#alerts").getByPlaceholder(/Bot token/)).toHaveCount(0);
    await page.locator("[data-telegram-elsewhere] a").click();
    await expect(page).toHaveURL(/\/settings\/advanced#own-bot$/);
    await expect(page.locator("#own-bot")).toHaveAttribute("open", "");
    await expect(page.locator("#own-bot").getByPlaceholder(/Bot token/)).toBeVisible();
  });

  test("the four tour targets are on the index rows", async ({ page }) => {
    await openSettings(page, "/settings", FULL);
    for (const id of ["settings-appearance", "settings-alerts", "settings-ai", "settings-help"]) {
      await expect(page.locator(`[data-tour='${id}']`)).toBeVisible();
    }
  });

  test("no developer words outside Advanced", async ({ page }) => {
    for (const path of ["/settings", "/settings/ai", "/settings/appearance", "/settings/help"]) {
      await openSettings(page, path, FULL);
      await expect(page.locator("main")).not.toContainText(/\b(api_key|ui_v2|flag|webhook|JSON|HMAC|SMTP|token)\b/i);
    }
  });
});

// REV-1 M3 (review: the header avatar was cut at 375 px and the page scrolled sideways): with colour v2 and the price
// dot on, the top bar's title steps down, wraps, and when even that does not fit takes its own line under the buttons
// (M57 / #759 review: never cut) - the avatar always stays on screen.
for (const width of [375, 320]) {
  test.describe(`settings header at ${width} px`, () => {
    test.use({ viewport: { width, height: 780 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true });
    test("the avatar stays on screen, no sideways scroll", async ({ page }) => {
      await openSettings(page, "/settings", { ...FULL, features: { ...FEATURES, data_hub: true, colour_v2: true } });
      await expect(index(page)).toBeVisible();
      const avatar = await page.locator('header a[aria-label="Account"]').boundingBox();
      expect(avatar!.x + avatar!.width).toBeLessThanOrEqual(width);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    });
  });
}

test.describe("ui_v2 off", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("today's Settings page, and the new addresses go back to it", async ({ page }) => {
    await openSettings(page, "/settings", { features: {}, ...FULL });
    await expect(page.locator("[data-settings-index]")).toHaveCount(0);
    await expect(page.locator("#alerts")).toBeVisible();
    await expect(page.locator("#integrations")).toBeVisible();
    await expect(page.getByRole("link", { name: /Prepare for LIVE/ })).toBeVisible();
    await page.goto("/settings/alerts");
    await expect(page).toHaveURL(/\/settings#alerts$/);
    await page.goto("/settings/ai");
    await expect(page).toHaveURL(/\/settings#ai$/);
    await page.goto("/settings/advanced");
    await expect(page).toHaveURL(/\/settings#integrations$/);
  });
});

/** UX-11 PR 5 (owner, 27 Oct): numbers first - every Settings index card passes the text budget; the visible-word count is printed. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`v2 Settings index text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await openSettings(page, "/settings", { theme: "light", ...FULL });
      console.log(`[words] Settings index ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
