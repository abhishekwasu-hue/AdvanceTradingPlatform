import { expect, test, type Page } from "@playwright/test";
import { openAdmin, type Theme } from "./adminMocks";
import { installDiagnostics } from "./diagnostics";

/**
 * SRV2 P3 (owner AGENT-MODES 20 Nov, ADR-0029): the owner's "My trading agent" card in Admin -> Controls -> Safety.
 * "Ask me first" (the default) -> "Automatic" asks for one confirm, then the card shows Automatic, the Vision switch and the
 * last audited change. Another admin (can_switch false) sees no card.
 * - Baselines (NEW): `agent-mode-<ask|auto>-v2-{1440,390}-{light,dark}.png` (the card alone).
 * Every reply is made up. After an intended change: `npx playwright test --project=phone e2e/agent-mode-v2.e2e.ts --update-snapshots`.
 */
installDiagnostics();
const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };

async function withAgent(page: Page, theme: Theme, canSwitch = true) {
  const sent: unknown[] = [];
  let state = { installed: true, mode: "human_confirm", vision_on: true, can_switch: canSwitch, last: null as unknown };
  await openAdmin(page, "/admin?section=controls&group=safety", {
    theme,
    routes: async (p) => {
      await p.route(/\/api(?:\/v1)?\/admin\/controls\/srv2-agent-mode$/, async (r) => {
        if (r.request().method() === "PUT") {
          const body = r.request().postDataJSON() as { mode: string; vision_on: boolean; confirm: boolean };
          sent.push(body);
          state = { ...state, mode: body.mode, vision_on: body.vision_on,
            last: { by: 1, at: "2026-11-21T04:00:00+00:00", from: state.mode, to: body.mode, vision_on: body.vision_on } };
        }
        return r.fulfill({ json: state });
      });
    },
  });
  return sent;
}

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true }]) {
  test.describe(`Agent mode ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as Theme[]) {
      test(`Ask me first -> Automatic asks once, then shows the change (${theme})`, async ({ page }) => {
        const sent = await withAgent(page, theme);
        const card = page.locator("[data-console-card='agent-mode']");
        await expect(card.locator("[data-agent-mode='human_confirm']")).toBeVisible();
        await card.evaluate((el) => el.scrollIntoView({ block: "center" }));     // phone: clear of the bottom bar
        await expect(card).toHaveScreenshot(`agent-mode-ask-v2-${size.name}-${theme}.png`, SHOT);
        await card.getByRole("button", { name: /Automatic/ }).click();
        await page.getByRole("button", { name: "Switch to Automatic" }).click();
        await expect(card.locator("[data-agent-mode='auto']")).toBeVisible();
        await expect(card.locator("[data-agent-mode-last]")).toContainText("Ask me first → Automatic");
        expect(sent).toEqual([{ mode: "auto", vision_on: true, confirm: true }]);
        const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
        expect(r.scroll).toBeLessThanOrEqual(r.width);
        await card.evaluate((el) => el.scrollIntoView({ block: "center" }));
        await expect(card).toHaveScreenshot(`agent-mode-auto-v2-${size.name}-${theme}.png`, SHOT);
      });
    }

    test("another admin sees no agent card", async ({ page }) => {
      await withAgent(page, "light", false);
      await expect(page.locator("[data-controls-groups='safety']")).toBeVisible();
      await expect(page.locator("[data-console-card='agent-mode']")).toHaveCount(0);
    });
  });
}
