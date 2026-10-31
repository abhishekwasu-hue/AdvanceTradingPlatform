import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { DEMO_ACTIVATION } from "../src/growth/growth.fixture";
import { openAdmin, type Theme } from "./adminMocks";
import { mockApi } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Readiness C13 (learning and growth), on the server's own payloads (e2e/fixtures/learn_v2.json and site_v2.json, pinned
 * by backend/tests/test_c13_learning.py):
 * - the Learn page at /learn (`ui_v2` + `learn`), one lesson open, with one video link and the course link set;
 * - the public Features page at /features (no sign-in; `marketing_site`), its SEO tags;
 * - "Invite a friend" on Settings > Account & plan (`referrals`);
 * - Admin > Users & business: the growth funnel and the Learn videos / Invite a friend settings.
 * Baselines (NEW): `learn-v2-`, `site-features-v2-`, `invite-v2-` and `admin-growth-v2-<360|390|1440>-<light|dark>.png`.
 * Fonts are pinned to DejaVu Sans (settle). After an intended change:
 * `npx playwright test --project=phone e2e/learn-growth-v2.e2e.ts --update-snapshots`, check the images, commit.
 */
const LEARN = JSON.parse(readFileSync(new URL("./fixtures/learn_v2.json", import.meta.url), "utf8")) as {
  lessons: { id: string; video_url: string | null }[]; course_url: string | null;
};
const SITE = JSON.parse(readFileSync(new URL("./fixtures/site_v2.json", import.meta.url), "utf8")) as unknown;
const LEARN_SET = { ...LEARN, course_url: "https://course.example.com/",
  lessons: LEARN.lessons.map((l) => (l.id === "structure-divergence" ? { ...l, video_url: "https://videos.example.com/divergence" } : l)) };
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const THEMES: Theme[] = ["light", "dark"];
const SIZES = [
  { name: "360", viewport: { width: 360, height: 800 }, phone: true },
  { name: "390", viewport: { width: 390, height: 844 }, phone: true },
  { name: "1440", viewport: { width: 1440, height: 900 }, phone: false },
] as const;
const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };

async function prepare(page: Page, theme: Theme, features: Record<string, boolean>) {
  await page.clock.install({ time: new Date("2026-10-23T18:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  return mockApi(page, { features, brokers: CONNECTED });
}

async function settle(page: Page) {
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

async function openLearn(page: Page, theme: Theme, features: Record<string, boolean> = { ui_v2: true, learn: true }) {
  await prepare(page, theme, features);
  await page.route(/\/api(?:\/v1)?\/learn$/, (r) => (features.learn ? r.fulfill({ json: LEARN_SET }) : r.fulfill({ status: 404, json: { detail: "Not found" } })));
  await page.goto("/learn");
  await settle(page);
}

async function openSite(page: Page, theme: Theme, path = "/features", on = true) {
  await page.addInitScript((t) => { try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: t })); } catch { /* none */ } }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await page.route("**/api/**", (r) => r.fulfill({ status: 401, json: { detail: "Not signed in" } }));
  await page.route(/\/api(?:\/v1)?\/site\/pages$/, (r) => (on ? r.fulfill({ json: SITE }) : r.fulfill({ status: 404, json: { detail: "Not found" } })));
  await page.goto(path);
  await settle(page);
}

for (const size of SIZES) {
  test.describe(`C13 ${size.name}`, () => {
    test.use(size.phone ? { viewport: size.viewport, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size.viewport });

    for (const theme of THEMES) {
      test(`Learn looks the same (${theme})`, async ({ page }) => {
        await openLearn(page, theme);
        await page.locator("[data-lesson='structure-divergence'] button[aria-expanded]").click();
        await expect(page.locator("[data-video-link]")).toBeVisible();
        await expect(page).toHaveScreenshot(`learn-v2-${size.name}-${theme}.png`, { ...SHOT, fullPage: true });
      });

      test(`the public Features page looks the same (${theme})`, async ({ page }) => {
        await openSite(page, theme);
        await expect(page.locator("[data-site-page='features']")).toBeVisible();
        await expect(page).toHaveScreenshot(`site-features-v2-${size.name}-${theme}.png`, { ...SHOT, fullPage: true });
      });

      test(`Invite a friend looks the same (${theme})`, async ({ page }) => {
        await prepare(page, theme, { ui_v2: true, referrals: true });
        await page.route(/\/api(?:\/v1)?\/growth\/referral$/, (r) => r.fulfill({ json: {
          code: "K7Q2M9XD", link: "https://aialgodesk.tech/?ref=K7Q2M9XD", reward_text: "You both get one month of the Pro plan.", joined: 2 } }));
        await page.goto("/settings/account");
        await settle(page);
        const card = page.locator("[data-block='invite-friend']");
        await expect(card).toContainText("2 people joined through your link.");
        await card.scrollIntoViewIfNeeded();
        await expect(card).toHaveScreenshot(`invite-v2-${size.name}-${theme}.png`, SHOT);
      });

      test(`Admin growth funnel and settings look the same (${theme})`, async ({ page }) => {
        await openAdmin(page, "/admin?section=users", { theme, features: { ui_v2: true, phase0_paper_only: true, growth_v2: true },
          routes: (p) => p.route(/\/api(?:\/v1)?\/admin\/console\/activation(\?.*)?$/, (r) => r.fulfill({ json: DEMO_ACTIVATION })) });
        await expect(page.locator("[data-growth-funnel]")).toBeVisible();
        await expect(page.locator("[data-console-card='learn-videos']")).toBeVisible();
        await page.addStyleTag({ content: "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }" });
        // Below the fold on a phone an element shot is taken from the wrong place: clip the full page to each block.
        await page.addStyleTag({ content: "nav[aria-label='Quick navigation'] { display: none !important; }" });
        for (const [selector, name] of [["[data-growth-funnel]", "admin-growth-funnel"], ["[data-console-card='referral-settings']", "admin-referral"]] as const) {
          const clip = await page.locator(selector).evaluate((el) => { const r = el.getBoundingClientRect();
            return { x: r.left + window.scrollX, y: r.top + window.scrollY, width: r.width, height: r.height }; });
          await expect(page).toHaveScreenshot(`${name}-v2-${size.name}-${theme}.png`, { ...SHOT, fullPage: true, clip });
        }
      });
    }

    if (size.phone) {
      test("phone layout of Learn and the public page", async ({ page }) => {
        await openLearn(page, "light");
        await expectPhoneLayout(page);
        // The public page has no app navigation: no sideways scroll, and every link and button a 44 px target.
        await openSite(page, "light", "/how-it-works");
        const report = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth, width: window.innerWidth,
          small: [...document.querySelectorAll("a, button")].filter((e) => e.getBoundingClientRect().height < 43.5)
            .map((e) => (e.textContent ?? "").trim().slice(0, 30)),
        }));
        expect(report.scrollWidth).toBeLessThanOrEqual(report.width);
        expect(report.small).toEqual([]);
      });
    }
  });
}

test.describe("C13 behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Learn: a topic, a search, the glossary tab and a lesson's next step", async ({ page }) => {
    await openLearn(page, "light");
    await page.getByRole("group", { name: "Topics" }).getByRole("button", { name: "Risk and your own settings" }).click();
    await expect(page.locator("[data-lesson]")).toHaveCount(3);
    // M60: Lessons and Words explained are tabs of the shell's Learn row, each at its own address.
    await page.getByRole("navigation", { name: "Learn" }).getByRole("link", { name: "Words explained" }).click();
    await expect(page).toHaveURL(/\/learn\/words$/);
    await page.getByPlaceholder("Search the words").fill("open interest");
    await expect(page.locator("[data-term='oi']")).toBeVisible();
    await expect(page.locator("[data-term='stop']")).toHaveCount(0);
    await page.getByRole("navigation", { name: "Learn" }).getByRole("link", { name: "Lessons" }).click();
    await expect(page).toHaveURL(/\/learn$/);
    await page.getByPlaceholder("Search the lessons").fill("");
    await page.getByRole("group", { name: "Topics" }).getByRole("button", { name: "All topics" }).click();
    await page.locator("[data-lesson='structure-trend'] button[aria-expanded]").click();
    await page.locator("[data-lesson='structure-trend']").getByRole("link", { name: "Open Charts" }).click();
    await expect(page).toHaveURL(/\/charts$/);
  });

  test("Learn: in the sidebar only while `learn` is on; its address says Coming soon while off", async ({ page }) => {
    await openLearn(page, "light", { ui_v2: true, learn: false });
    await expect(page.locator("[data-page='learn']")).toHaveCount(0);
    await expect(page.getByText("Coming soon")).toBeVisible();
    await expect(page.locator("nav a[href='/learn']")).toHaveCount(0);
  });

  test("the public page sets its SEO tags; unpublished it says it does not exist", async ({ page }) => {
    await openSite(page, "light", "/how-it-works");
    await expect(page).toHaveTitle("How it works · AiAlgoDesk");
    await expect(page.locator("link[rel='canonical']")).toHaveAttribute("href", "https://aialgodesk.tech/how-it-works");
    await expect(page.locator("meta[property='og:title']")).toHaveAttribute("content", "How it works · AiAlgoDesk");
    await page.getByRole("link", { name: "About" }).first().click();
    await expect(page.locator("[data-site-page='about']")).toBeVisible();
    const off = await page.context().newPage();
    await openSite(off, "light", "/features", false);
    await expect(off.getByText("This page does not exist.")).toBeVisible();
  });

  test("an invite link's code is claimed once after sign-in", async ({ page }) => {
    await prepare(page, "light", { ui_v2: true, referrals: true });
    const claims: unknown[] = [];
    await page.route(/\/api(?:\/v1)?\/growth\/referral\/claim$/, (r) => { claims.push(r.request().postDataJSON()); return r.fulfill({ json: { claimed: true } }); });
    await page.goto("/?ref=K7Q2-M9XD");
    await settle(page);
    await expect.poll(() => claims.length).toBe(1);
    expect(claims[0]).toEqual({ code: "K7Q2M9XD" });
    await page.goto("/");
    await settle(page);
    expect(claims).toHaveLength(1);
    expect(await page.evaluate(() => localStorage.getItem("atp.referral.code"))).toBeNull();
  });
});

/** UX-11 PR 5 (owner, 27 Oct): numbers first - every Learn card passes the text budget; the visible-word count is printed. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`v2 Learn text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await openLearn(page, "light");
      console.log(`[words] Learn ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
