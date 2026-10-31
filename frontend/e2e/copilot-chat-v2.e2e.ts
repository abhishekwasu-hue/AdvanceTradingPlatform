import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { mockCopilotNumbers } from "./copilotMocks";

/**
 * Item 5 (owner's handover, the AI Copilot as a trader's brain): the chat-first Copilot at /copilot with `ui_v2` on.
 * - Baselines `copilot-chat-v2-<width>-<theme>.png` at 1440 and 390 (new; the seven-tab Copilot's baselines are not
 *   touched).
 * - The top line, the no-key card, today's market line, the starters, a starter's answer with its table and "How I
 *   worked this out", a refusal in plain words, the phone layout (the question box above the bottom bar).
 * - Second slice: the position watch (job 4) with its warnings and table, the post-trade coach (job 5) with the week
 *   and the repeated habits, and the pre-trade check (job 3) listing what the idea does not state yet.
 * - CP-UX CU2: screen 1 is the numbers strip (NIFTY / BANK NIFTY / SENSEX, India VIX level, FII net), four chips and the
 *   question box as the only primary action; the jobs and older pages sit under Advanced. CU3: the chart answer is a
 *   card - headline numbers, mini chart, one action - with the rest behind one "Details" tap.
 * Every reply is made-up data (e2e/fixtures/copilot_chat_v2.json, the backend's rule answers on its synthetic eval
 * fixtures). After an intended change: `npx playwright test --project=phone e2e/copilot-chat-v2.e2e.ts --update-snapshots`.
 */
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as {
  home: Record<string, unknown>; answers: Record<string, Record<string, unknown>>;
};

/** CU6: the server's kept conversation, as the mock keeps it (it survives a reload: the routes stay). */
interface Kept { turns: { id: number; question: string; answer: Record<string, unknown>; at: string }[]; deleted: number }

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

async function open(page: Page, theme: "dark" | "light", kept: Kept = { turns: [], deleted: 0 }, path = "/copilot"): Promise<string[]> {
  const asked: string[] = [];
  await page.clock.install({ time: new Date("2026-10-21T09:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true }, brokers: path === "/copilot" ? [] : CONNECTED });
  await mockCopilotNumbers(page);
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, async (route) => {
    const request = route.request();
    if (request.url().endsWith("/history")) {
      if (request.method() === "DELETE") { kept.deleted += kept.turns.length; kept.turns = []; return route.fulfill({ json: { deleted: kept.deleted } }); }
      return route.fulfill({ json: { turns: kept.turns, keep: 20 } });
    }
    if (request.method() === "GET") return route.fulfill({ json: F.home });
    const message = (request.postDataJSON() as { message: string }).message;
    asked.push(message);
    const answer = F.answers[message] ?? F.answers["Should I buy RELIANCE today?"];
    kept.turns.push({ id: kept.turns.length + 1, question: message, answer, at: "2026-10-21T03:30:00Z" });
    return route.fulfill({ json: answer });
  });
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  if (path !== "/copilot") return asked;
  await expect(page.locator("[data-page='copilot-v2']")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("[data-market-line='ok']")).toBeVisible();
  return asked;
}

const THEMES = ["dark", "light"] as const;

test.describe("v2 copilot chat 390 x 844", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await expect(page).toHaveScreenshot(`copilot-chat-v2-390-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("the question box sits above the bottom bar; no sideways scroll, 44 px targets", async ({ page }) => {
    await open(page, "dark");
    const box = page.locator("[data-composer='bottom']");
    await expect(box).toBeVisible();
    const nav = await page.locator("nav[aria-label='Quick navigation']").boundingBox();
    const form = await box.boundingBox();
    expect(form && nav && form.y + form.height <= nav.y + 1).toBeTruthy();
    await expectPhoneLayout(page);
  });

  test("a question typed on the phone gets its answer", async ({ page }) => {
    const asked = await open(page, "light");
    await page.locator("#copilot-question").fill("What does the option chain show?");
    await page.getByRole("button", { name: "Ask" }).click();
    await expect(page.locator("[data-answer='option_chain']")).toBeVisible();
    await expect(page.locator("[data-answer='option_chain'] [data-block='chart']").first()).toBeVisible();
    expect(asked).toEqual(["What does the option chain show?"]);
  });
});

test.describe("v2 copilot chat 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await expect(page).toHaveScreenshot(`copilot-chat-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("CU2: numbers strip, then four chips, then the question box - the only primary action; jobs under Advanced", async ({ page }) => {
    await open(page, "light");
    const strip = page.locator("[data-numbers-strip]");
    for (const key of ["NIFTY 50", "NIFTY BANK", "SENSEX"]) await expect(strip.locator(`[data-index='${key}']`)).toBeVisible();
    await expect(strip.locator("[data-index='SENSEX']")).toContainText("81,261.10");
    await expect(strip.locator("[data-vix-level='normal']")).toContainText("13.42");
    await expect(strip.locator("[data-fii-net='ok']")).toContainText("−1,235");
    await expect(page.locator("[data-quick]")).toHaveText(["Brief", "Read chart", "Check my trade", "My positions"]);
    const y = async (sel: string) => (await page.locator(sel).first().boundingBox())!.y;
    expect(await y("[data-numbers-strip]")).toBeLessThan(await y("[data-quick-chips]"));
    expect(await y("[data-quick-chips]")).toBeLessThan(await y("[data-composer='inline']"));
    await expect(page.locator("[data-market-line='ok']")).toContainText("NIFTY 50 last closed at 24,850.5");
    await expect(page.locator("[data-no-key]")).toHaveText(/AI is off — answers are from rules\s*Connect/);   // CU5: said once
    await expect(page.locator("[data-no-key] a[data-connect]")).toHaveAttribute("href", "/settings/ai");
    await expect(page.locator("main .bg-brand:visible")).toHaveCount(1);                       // the Ask button alone
    await expect(page.locator("[data-job]")).toHaveCount((F.home.jobs as unknown[]).length);
    await expect(page.locator("[data-job]").first()).toBeHidden();                              // under the closed Advanced
    await expect(page.locator("[data-starters]")).toHaveCount(0);
    await expect(page.locator("[data-no-key]")).toHaveCount(1);
    await expect(page.getByText("fixed sentences")).toHaveCount(0);
    await expect(page.locator("[data-disclaimer]")).toHaveCount(1);
  });

  test("CU3: Read the SENSEX chart - headline numbers, mini chart, one action, the rest behind Details", async ({ page }) => {
    const asked = await open(page, "light");
    await page.locator("#copilot-question").fill("Read the SENSEX chart");
    await page.locator("#copilot-question").press("Enter");
    const answer = page.locator("[data-answer='chart']");
    const head = answer.locator("[data-headline='SENSEX']");
    await expect(head.locator("[data-headline-price]")).toHaveText("81,261.10");
    await expect(head.locator("[data-trend='up']")).toBeVisible();
    await expect(head.locator("[data-headline-area='above']")).toContainText("Supply 82,011.60 – 82,273.20");
    await expect(head.locator("[data-headline-area='above']")).toContainText("750.50 (0.92%) above");
    expect((await head.boundingBox())!.y).toBeLessThan((await answer.locator("[data-summary]").boundingBox())!.y);
    await expect(answer.locator("[data-mini-chart='SENSEX'] [data-embedded-chart='SENSEX']")).toBeVisible();
    await expect(answer.locator("[data-action]")).toHaveCount(1);
    await expect(answer.locator("a[data-action='chart']")).toHaveAttribute("href", "/charts/SENSEX?tf=day&exchange=BSE");
    await expect(answer.locator("[data-worked]")).toBeHidden();
    await expect(answer.locator("[data-unknowns], [data-block='table']").first()).toBeHidden();
    await answer.locator("[data-details='copilot.answer'] summary").click();
    await expect(answer.locator("[data-step='get_chart_structure']")).toContainText("last close 81261.1");
    await expect(answer.locator("[data-tools-used]")).toContainText("Chart structure");
    expect(asked).toEqual(["Read the SENSEX chart"]);
  });

  test("a chip's answer shows the data, the tables and how it was worked out (behind Details)", async ({ page }) => {
    const asked = await open(page, "light");
    await page.locator("[data-quick='chart']").click();
    const answer = page.locator("[data-answer='chart']");
    await expect(answer).toContainText("NIFTY last closed at 24,850.5");
    await answer.locator("[data-details='copilot.answer'] summary").click();
    await expect(answer).toContainText("Structure reading from ATP's zone layers");
    await expect(answer.locator("[data-block='table']").first()).toBeVisible();
    await expect(answer.locator("[data-step='get_chart_structure']")).toContainText("last close 24850.5");
    await expect(page.locator("[data-page='copilot-v2'] > [data-disclaimer]")).toContainText("not advice");
    expect(asked).toEqual(["Read the NIFTY chart"]);
  });

  test("the position watch warns from the user's own positions and settings", async ({ page }) => {
    const asked = await open(page, "light");
    await page.locator("[data-quick='positions']").click();
    const answer = page.locator("[data-answer='positions']");
    await expect(answer).toContainText("You have 2 open positions");
    await expect(answer.locator("[data-section='Warnings']")).toContainText("your own limit for this is 6%: above it");
    await expect(answer.locator("[data-block='table']").first()).toContainText("NIFTY26OCTFUT");
    await expect(answer.locator("a", { hasText: "Open My positions" })).toHaveAttribute("href", "/positions");
    await answer.locator("[data-details='copilot.answer'] summary").click();
    await expect(answer.locator("[data-step='get_my_positions']")).toContainText("total loss if stops hit 18300");
    expect(asked).toEqual(["How are my open positions?"]);
  });

  test("the post-trade coach and the pre-trade check describe; they never advise", async ({ page }) => {
    await open(page, "light");
    await page.locator("[data-details='copilot.chat.advanced'] summary").click();
    await page.locator("[data-job='review'] button", { hasText: "Review my last trade" }).click();
    const review = page.locator("[data-answer='review']");
    await expect(review.locator("[data-section='Repeated habits']")).toContainText("tagged in 3 of your last 4 weeks");
    await page.locator("[data-quick='trade_check']").click();
    const check = page.locator("[data-answer='trade_check']");
    await expect(check.locator("[data-section='What your plan does not say yet']")).toContainText("your invalidation price");
    await expect(check).toContainText("I don't add rules of my own");
  });

  test("a buy question is refused in plain words", async ({ page }) => {
    await open(page, "light");
    await page.locator("#copilot-question").fill("Should I buy RELIANCE today?");
    await page.locator("#copilot-question").press("Enter");
    const answer = page.locator("[data-answer='refuse']");
    await expect(answer).toContainText("I can't tell you to buy or sell anything.");
    await expect(answer.locator("[data-worked]")).toHaveCount(0);
  });
});

test.describe("v2 copilot - one Copilot (CU4) and the kept conversation (CU6)", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("CU4 + M60: the tab bar - Ask, Read the chart, Coach (Analyze a chart with chart_analyze on) - every target at least 44 px", async ({ page }) => {
    await open(page, "light");
    const tabs = page.locator("[data-copilot-tabs='v2'] a");
    await expect(tabs).toHaveText(["Ask", "Read the chart", "Coach"]);
    await expect(page.locator("[data-copilot-tab='ask']")).toHaveAttribute("aria-current", "page");
    await expect(page.locator("[data-copilot-tab='build']")).toHaveCount(0);       // Strategies › Build is the one builder
    for (const box of await Promise.all((await tabs.all()).map((t) => t.boundingBox()))) expect(box!.height).toBeGreaterThanOrEqual(44);
    await expectPhoneLayout(page);
  });

  test("CU6: the conversation is still there after a reload", async ({ page }) => {
    const kept: Kept = { turns: [], deleted: 0 };
    await open(page, "light", kept);
    await page.locator("[data-quick='chart']").click();
    await expect(page.locator("[data-answer='chart']")).toBeVisible();
    await page.reload();
    await expect(page.locator("[data-page='copilot-v2']")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("[data-turn]")).toHaveCount(1);
    await expect(page.locator("[data-turn] [data-answer='chart']")).toContainText("NIFTY last closed at 24,850.5");
    await expect(page.locator("[data-turn]")).toContainText("Read the NIFTY chart");
  });

  test("M60: Brief forwards to Read the chart (Market Pulse); Review to the Coach; Signals to the Signals page - the old tabs stay under Advanced", async ({ page }) => {
    await open(page, "light", undefined, "/copilot/brief");
    await expect(page).toHaveURL(/\/copilot\/read$/);
    await expect(page.locator("[data-copilot-folded='read']")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("[data-copilot-tab='read']")).toHaveAttribute("aria-current", "page");
    await expect(page.locator("#copilot-read-pulse")).toHaveText("Market pulse");
    await expect(page.locator("[data-disclaimer]")).toHaveCount(1);
    await page.locator("[data-copilot-tab='coach']").click();
    await expect(page.locator("[data-copilot-folded='coach']")).toBeVisible();
    await expect(page).toHaveURL(/\/copilot\/coach$/);
    await expect(page.locator("[data-folded-part='copilot-coach-guide']")).toBeVisible();
    await page.goto("/copilot/review");
    await expect(page).toHaveURL(/\/copilot\/coach$/);
    await page.goto("/copilot/signals");
    await expect(page).toHaveURL(/\/signals$/);
    await page.goto("/copilot/market-pulse");                      // an older tab keeps its address
    await expect(page.locator("[data-testid='copilot-page']")).toBeVisible({ timeout: 15_000 });
    await expectPhoneLayout(page);
  });
});

// M59 CR-1: the owner's 30 Oct case - the AI wording carries raw Markdown and the 1-hour history is not stored. The card
// (backend copilot_chat.structure_card on the synthetic eval fixture, 1-hour marked not ready) is the answer; the words
// sit behind Details, rendered, never as literal `**`. M59_SHOTS=<dir> saves the 390 / 1440 shots for the PR.
const AI_PROSE = "**Weekly:** sideways.\n**Daily:** up, protected level 24,310.\n**1-hour:** I don't know. Only 0 of the 30 bars needed are stored.";
const CARDED = {
  ...F.answers["Read the NIFTY chart"], source: "ai", summary: [AI_PROSE], answer: AI_PROSE,
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

for (const [width, height] of [[390, 844], [1440, 900]] as const) {
  test.describe(`v2 copilot - M59 CR-1 the chart reading as a card, ${width}`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });

    test("table, areas nearby, swing levels, one line; '1-hour: data not ready'; no raw Markdown", async ({ page }) => {
      await open(page, "light");
      await page.route(/\/api(?:\/v1)?\/ai\/chat$/, (route) => route.request().method() === "POST" ? route.fulfill({ json: CARDED }) : route.fallback());
      await page.locator("[data-quick='chart']").click();
      const answer = page.locator("[data-answer='chart']");
      const card = answer.locator("[data-structure-card='NIFTY']");
      await expect(card).toBeVisible();
      await expect(card.locator("[data-tf='1d']")).toContainText("24,310.00");
      await expect(card.locator("[data-not-ready='1h']")).toHaveText("1-hour: data not ready");
      await expect(card.locator("[data-areas='above']")).toContainText("25,080.00 – 25,160.00Supply · 229.50 pts");
      await expect(card.locator("[data-card-block='watch']")).toContainText("What to watch:");
      await expect(card).not.toContainText(/\*\*|of the 30|bars needed/);
      await expect(page.getByText(/\*\*/)).toHaveCount(0);
      if (width === 390) {
        await expect(card.locator("[data-card-block='swings']")).toBeHidden();
        await expect(card.getByRole("button", { name: "Help: Swing levels" })).toBeVisible();
        await expectPhoneLayout(page);
      } else {
        await expect(card.locator("[data-card-block='swings']")).toContainText("Swing high 25,010.50");
      }
      const dir = process.env.M59_SHOTS;
      if (dir) {
        await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
        await answer.scrollIntoViewIfNeeded();
        await answer.screenshot({ path: `${dir}/copilot-chart-card-${width}.png` });
      }
      await answer.locator("[data-details='copilot.answer'] summary").click();
      await expect(answer.locator("[data-summary='folded'] strong").first()).toHaveText("Weekly:");
      await expect(page.getByText(/\*\*/)).toHaveCount(0);
    });
  });
}
