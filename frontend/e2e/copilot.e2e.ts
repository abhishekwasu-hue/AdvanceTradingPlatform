import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";

const ack = JSON.parse(readFileSync(new URL("./fixtures/ack.json", import.meta.url), "utf8")) as Record<string, unknown>;

const TABS = [
  ["market-pulse", "Market Pulse"], ["strategy-lab", "Strategy Lab"], ["idea-builder", "Idea Builder"], ["ask", "Ask Copilot"],
  ["watchtower", "Watchtower"], ["news-radar", "News Radar"], ["coach", "Coach & Scorecard"],
] as const;

test.beforeEach(async ({ page }) => { await mockApi(page); });

for (const [slug, label] of TABS) {
  test(`deep link /copilot/${slug} opens ${label}`, async ({ page }) => {
    await page.goto(`/copilot/${slug}`);
    await expect(page.getByTestId(`tab-${slug}`)).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId(`tab-panel-${slug}`)).toBeVisible();
    await expect(page.getByRole("tab", { name: new RegExp(label) })).toBeVisible();
  });
}

test("tabs change the address and back / forward follow it", async ({ page }) => {
  await page.goto("/copilot/market-pulse");
  await page.getByTestId("tab-coach").click();
  await expect(page).toHaveURL(/\/copilot\/coach$/);
  await page.getByTestId("tab-ask").click();
  await expect(page).toHaveURL(/\/copilot\/ask$/);
  await page.goBack();
  await expect(page.getByTestId("tab-coach")).toHaveAttribute("aria-selected", "true");
});

test("the old /ai-copilot addresses forward to the new tabs", async ({ page }) => {
  await page.goto("/ai-copilot/interview");
  await expect(page).toHaveURL(/\/copilot\/idea-builder$/);
  await page.goto("/ai-copilot/study");
  await expect(page).toHaveURL(/\/copilot\/strategy-lab$/);
});

test("the Watchtower badge counts the open proposals", async ({ page }) => {
  await page.goto("/copilot/market-pulse");
  await expect(page.getByTestId("tab-watchtower")).toContainText("2");
});

test("Ask Copilot shows the answer with its sources", async ({ page }) => {
  await page.goto("/copilot/ask");
  await page.getByTestId("ask-suggestion").first().click();
  await expect(page.getByTestId("ask-meta")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("ask-meta")).toContainText("Based on:");
});

test("Idea Builder: a saved profile is offered first, then bilingual questions with a progress bar", async ({ page }) => {
  await page.goto("/copilot/idea-builder");
  await page.getByTestId("idea-start").click();
  await page.getByRole("button", { name: /No, ask me again/ }).click();
  const question = page.getByTestId("idea-question");
  await expect(question).toBeVisible();
  await expect(page.getByTestId("idea-progress-label")).toContainText(/Question 1 of \d+/);
  await expect(question.locator('[lang="mr"]').first()).toBeVisible();     // the muted second-language line
});

test("Idea Builder: the second language can be switched off", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("atp_copilot_lang", JSON.stringify({ ui: "en", secondary: "off" })));
  await page.goto("/copilot/idea-builder");
  await expect(page.getByTestId("idea-start")).toBeVisible();
  await expect(page.locator('main [lang="mr"]')).toHaveCount(0);
});

test("no banned recommendation words on any tab", async ({ page }) => {
  for (const [slug] of TABS) {
    await page.goto(`/copilot/${slug}`);
    await expect(page.getByTestId(`tab-panel-${slug}`)).toBeVisible();
    const text = await page.locator("main").innerText();
    expect(text).not.toMatch(/\brecommended\b|\bbest\b|\bfor you\b|\d+\s*% match/i);
  }
});

test.describe("3D and motion", () => {
  test("the WebGL AI Core loads when the browser is idle", async ({ page, browserName }) => {
    test.skip(browserName !== "chromium");
    await page.goto("/copilot/market-pulse");
    await expect(page.getByTestId("ai-core-3d")).toBeVisible({ timeout: 15_000 });     // the WebGL scene itself, not its fallback
  });

  test("a 3D core that fails to load shows the still core - the page stays up", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.route("**/assets/AICore3D-*.js", (route) => route.abort());
    await page.goto("/copilot/market-pulse");
    await page.waitForTimeout(2500);
    await expect(page.getByTestId("ai-core-fallback").first()).toBeVisible();
    await expect(page.getByTestId("ai-core-3d")).toHaveCount(0);
    await expect(page.getByText(/could not be shown/)).toHaveCount(0);              // the app-level boundary never saw it
    await expect(page.getByTestId("tab-panel-market-pulse")).toBeVisible();
  });

  test("reduced motion: no 3D scene, the still core instead", async ({ browser }) => {
    const ctx = await browser.newContext({ reducedMotion: "reduce" });
    const page = await ctx.newPage();
    await mockApi(page);
    await page.goto("/copilot/market-pulse");
    await expect(page.getByTestId("ai-core-fallback").first()).toBeVisible();
    await page.waitForTimeout(1500);
    await expect(page.getByTestId("ai-core-3d")).toHaveCount(0);
    await ctx.close();
  });

  test("the Reduce motion setting turns the 3D off too", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark", colorBlind: false, reduceMotion: true })));
    await page.goto("/copilot/market-pulse");
    await page.waitForTimeout(1500);
    await expect(page.getByTestId("ai-core-3d")).toHaveCount(0);
    await expect(page.getByTestId("ai-core-fallback").first()).toBeVisible();
  });

  test("without WebGL the SVG core is shown", async ({ page }) => {
    await page.addInitScript(() => {
      const original = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...args: unknown[]) {
        if (type === "webgl" || type === "webgl2" || type === "experimental-webgl") return null;
        return (original as (...a: unknown[]) => unknown).call(this, type, ...args);
      } as typeof original;
    });
    await page.goto("/copilot/market-pulse");
    await page.waitForTimeout(1500);
    await expect(page.getByTestId("ai-core-3d")).toHaveCount(0);
    await expect(page.getByTestId("ai-core-fallback").first()).toBeVisible();
  });
});

test.describe("Watchtower decisions", () => {
  test("approve: the proposal is decided, leaves the list and the badge", async ({ page }) => {
    const calls = await mockApi(page);
    await page.goto("/copilot/watchtower");
    await expect(page.getByTestId("watch-proposal")).toHaveCount(2);
    await page.getByTestId("watch-proposal").first().getByRole("button", { name: "Approve" }).click();
    await expect(page.getByText("Approved and carried out.")).toBeVisible();
    await expect(page.getByTestId("watch-proposal")).toHaveCount(1);
    await expect(page.getByTestId("tab-watchtower")).toContainText("1");
    expect(calls.posts.filter((c) => /\/ai\/actions\/\d+\/approve$/.test(c.url))).toHaveLength(1);
  });

  test("reject: asks for an optional note and sends it", async ({ page }) => {
    const calls = await mockApi(page);
    await page.goto("/copilot/watchtower");
    await page.getByTestId("watch-proposal").first().getByRole("button", { name: "Reject" }).click();
    const dialog = page.getByRole("dialog", { name: "Reject this proposal" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("Why reject? (optional)").fill("Drift is from one bad week");
    await dialog.getByRole("button", { name: "Reject" }).click();
    await expect(page.getByText("Rejected.").first()).toBeVisible();
    await expect(page.getByTestId("watch-proposal")).toHaveCount(1);
    const sent = calls.posts.find((c) => /\/reject$/.test(c.url));
    expect(sent?.body).toEqual({ note: "Drift is from one bad week" });
  });

  test("LIVE: approving asks for the authenticator code first, then goes through", async ({ page }) => {
    await page.unrouteAll();
    const calls = await mockApi(page, { stepUp: true });
    await page.goto("/copilot/watchtower");
    await page.getByTestId("watch-proposal").first().getByRole("button", { name: "Approve" }).click();
    await expect(page.getByText("Two-factor check required")).toBeVisible();
    await expect(page.getByTestId("watch-proposal")).toHaveCount(2);                // nothing decided yet
    await page.getByPlaceholder("authenticator code").fill("123456");
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText("Approved and carried out.")).toBeVisible();
    await expect(page.getByTestId("watch-proposal")).toHaveCount(1);
    const flow = calls.posts.map((c) => c.url.replace(/\d+/, "N")).filter((u) => /actions\/N\/approve|mfa\/step-up/.test(u));
    expect(flow).toEqual(["/ai/actions/N/approve", "/auth/mfa/step-up", "/ai/actions/N/approve"]);
  });
});

async function chooseTemplate(page: import("@playwright/test").Page) {
  await page.goto("/copilot/idea-builder");
  await page.getByTestId("idea-start").click();
  await page.getByRole("button", { name: /No, ask me again/ }).click();         // the fixture has a saved profile
  await expect(page.getByTestId("idea-question")).toBeVisible();
  for (let i = 0; i < 15 && await page.getByTestId("idea-question").count(); i++) {
    await page.getByTestId("idea-question").locator("button").first().click();
  }
  await page.getByTestId("idea-read-market").click();
  await expect(page.getByTestId("idea-templates")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("strategy-card").first().getByRole("button", { name: "Choose this" }).click();
  await expect(page.getByTestId("idea-chosen")).toBeVisible();
  return page.getByTestId("idea-chosen");
}

test.describe("Idea Builder: risk gates", () => {
  test("Start in PAPER stays disabled until the loss is accepted, and sends the acceptance", async ({ page }) => {
    const calls = await mockApi(page);
    const chosen = await chooseTemplate(page);
    const start = chosen.getByRole("button", { name: "Start in PAPER" });
    await expect(start).toBeDisabled();
    await chosen.getByRole("checkbox").check();
    await expect(start).toBeEnabled();
    await start.click();
    await expect(page.getByText(/Deployment #77 is running in PAPER/)).toBeVisible();
    expect(calls.posts.find((c) => /\/ai\/interview\/deploy$/.test(c.url))?.body).toMatchObject({ accept_risk: true });
  });

  test("Apply risk settings asks first; Cancel changes nothing", async ({ page }) => {
    const calls = await mockApi(page);
    const chosen = await chooseTemplate(page);
    let asked = "";
    page.once("dialog", (d) => { asked = d.message(); void d.dismiss(); });
    await chosen.getByRole("button", { name: "Apply risk settings" }).click();
    expect(asked).toContain("Replace your current risk settings");
    expect(calls.posts.filter((c) => c.method === "PUT")).toHaveLength(0);
    page.once("dialog", (d) => void d.accept());
    await chosen.getByRole("button", { name: "Apply risk settings" }).click();
    await expect(page.getByText("Risk settings applied.")).toBeVisible();
    expect(calls.posts.filter((c) => c.method === "PUT" && /\/risk-settings$/.test(c.url))).toHaveLength(1);
  });

  test("the Marathi line is under the questions only - never under buttons", async ({ page }) => {
    await page.goto("/copilot/idea-builder");
    await expect(page.getByTestId("idea-start").locator('[lang="mr"]')).toHaveCount(0);
    await page.getByTestId("idea-start").click();
    const notNow = page.getByRole("button", { name: /No, ask me again/ });
    await expect(notNow).toBeVisible();
    await expect(notNow.locator('[lang="mr"]')).toHaveCount(0);
    await notNow.click();
    const question = page.getByTestId("idea-question");
    await expect(question.locator('[lang="mr"]').first()).toBeVisible();
    await expect(question.locator('button [lang="mr"]')).toHaveCount(0);           // answer chips: English only
    await expect(page.locator('main button [lang="mr"]')).toHaveCount(0);
  });
});

test("Ask Copilot asks for an English answer", async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto("/copilot/ask");
  await page.getByTestId("ask-suggestion").first().click();
  await expect(page.getByTestId("ask-meta")).toBeVisible({ timeout: 15_000 });
  expect(calls.posts.find((c) => /\/ai\/copilot$/.test(c.url))?.body).toMatchObject({ language: "en" });
});

test("first use: the acknowledgement modal comes before any AI content", async ({ page }) => {
  await page.unrouteAll();
  await mockApi(page, { ack: { ...ack, accepted: false } });
  await page.goto("/copilot/market-pulse");
  await expect(page.getByTestId("first-use-modal")).toBeVisible();
  await expect(page.getByText("It is not a SEBI-registered Investment Adviser or Research Analyst")).toBeVisible();
  await expect(page.getByTestId("tab-panel-market-pulse")).toHaveCount(0);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "I understand, continue" }).click();
  await expect(page.getByTestId("tab-panel-market-pulse")).toBeVisible();
});

test("mobile: the tab pills scroll sideways without scrolling the page", async ({ page, isMobile }) => {
  test.skip(!isMobile);
  await page.goto("/copilot/coach");
  await expect(page.getByTestId("tab-coach")).toBeInViewport();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
