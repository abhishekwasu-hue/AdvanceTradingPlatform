import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";

/**
 * BRK-1 C: every broker's connect panel opens with its step-by-step guide (fixtures/broker_catalog.json, generated from
 * backend/app/brokers/catalog.py) and reads cleanly at 390 px and 1280 px, light and dark: nothing wider than the panel,
 * every step numbered, each field "From step N", the Copy button with the exact value (made-up values from the mocked
 * GET /broker/specs).
 * Proof pictures (not baselines): set BRK1_SHOTS=<folder> to save `tiles-<width>-<theme>.png` and
 * `<broker>-<width>-<theme>.png` there; BRK1_BASE=<origin> points the tile shots at another build (the "before" one).
 */

const CATALOG = JSON.parse(readFileSync(new URL("./fixtures/broker_catalog.json", import.meta.url), "utf8")) as {
  broker: string; name: string; fields: { key: string }[]; guide: { steps: { copy: string | null }[] };
}[];
const SHOTS = process.env.BRK1_SHOTS ?? "";
const BASE = process.env.BRK1_BASE ?? "";
const SPECS = { brokers: CATALOG.map((e) => ({ name: e.broker, readonly: {
  redirect_url: ["zerodha", "fyers"].includes(e.broker) ? `https://atp.example/api/broker/${e.broker}/oauth/callback` : null,
  server_ip: "203.0.113.7",
} })) };
const LATER = { atp_sign_in: JSON.stringify({ userId: 2, id: "e2e-sign-in", isNew: false }), "atp_broker_prompt_later:2": "e2e-sign-in" };

async function open(page: Page, theme: "light" | "dark", catalog?: unknown) {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  await page.addInitScript(([t, s]) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
      for (const [k, v] of Object.entries(s)) localStorage.setItem(k, v);
    } catch { /* storage unavailable */ }
  }, [theme, LATER] as const);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { connections: [] });
  await page.route(/\/api(?:\/v1)?\/broker\/specs$/, (r) => r.fulfill({ json: SPECS }));
  if (catalog) await page.route(/\/api(?:\/v1)?\/broker\/catalog$/, (r) => r.fulfill({ json: catalog }));
  await page.goto(`${BASE}/broker`);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await expect(page.locator("[data-broker-page]")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

for (const [width, isPhone] of [[390, true], [1280, false]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`BRK-1 guides ${width} ${theme}`, () => {
      // Tall enough that the whole panel is on screen at once (the proof picture shows every step).
      test.use(isPhone ? { viewport: { width, height: 2400 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 }
        : { viewport: { width, height: 1800 }, deviceScaleFactor: 1 });

      test("the Broker tiles", async ({ page }) => {
        const before = BASE ? JSON.parse(readFileSync(process.env.BRK1_BEFORE_CATALOG ?? "", "utf8")) : undefined;
        await open(page, theme, before);
        await expect(page.locator("[data-broker-tile]")).toHaveCount(9);
        if (!BASE) await expect(page.locator("[data-broker-tiles], [data-broker-page]").first()).not.toContainText(/practice only/i);
        if (SHOTS) await page.locator("[data-broker-page]").screenshot({ path: `${SHOTS}/tiles-${BASE ? "before" : "after"}-${width}-${theme}.png`, animations: "disabled" });
      });

      for (const entry of CATALOG) {
        test(`${entry.name}: the guide is open and fits`, async ({ page }) => {
          test.skip(!!BASE, "the before build has no guide");
          await open(page, theme);
          await page.locator(`[data-broker-tile='${entry.broker}']`).click();
          const dialog = page.locator("[data-broker-dialog='connect']");
          const guide = dialog.locator(`[data-broker-guide='${entry.broker}'][data-guide-open]`);
          await expect(guide).toBeVisible();
          await expect(guide.locator("[data-guide-step]")).toHaveCount(entry.guide.steps.length);
          await expect(dialog.locator("[data-from-step]")).toHaveCount(entry.fields.length);
          await expect(dialog.locator("[data-guide-info]")).toContainText("static IP");
          if (entry.guide.steps.some((s) => s.copy === "static_ip")) await expect(dialog.locator("[data-guide-copy='static_ip'] code").first()).toHaveText("203.0.113.7");
          if (entry.guide.steps.some((s) => s.copy === "redirect_url")) {
            await expect(dialog.locator("[data-guide-copy='redirect_url'] code")).toHaveText(`https://atp.example/api/broker/${entry.broker}/oauth/callback`);
          }
          for (const link of await guide.locator("a[data-guide-link]").all()) {
            await expect(link).toHaveAttribute("target", "_blank");
            await expect(link).toHaveAttribute("href", /^https:\/\//);
          }
          // Nothing sticks out of the panel or is cut off sideways.
          const bad = await dialog.evaluate((root) => {
            const box = root.getBoundingClientRect();
            return [...root.querySelectorAll("*")].filter((e) => {
              const r = e.getBoundingClientRect();
              return r.width > 1 && (                         // (a 1 px screen-reader-only label is not shown)
                r.right > box.right + 1 || r.left < box.left - 1 || (e as HTMLElement).scrollWidth > (e as HTMLElement).clientWidth + 1 && getComputedStyle(e).overflowX !== "visible");
            }).map((e) => `${e.tagName}:${(e.textContent ?? "").trim().slice(0, 40)}`);
          });
          expect(bad).toEqual([]);
          expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
          if (SHOTS) await dialog.screenshot({ path: `${SHOTS}/${entry.broker}-${width}-${theme}.png`, animations: "disabled" });
        });
      }
    });
  }
}
