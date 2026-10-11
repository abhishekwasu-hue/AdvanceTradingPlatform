import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";

/**
 * PW2: the pages used every day, on the two phone sizes the beta targets (360 x 800 and 390 x 844), in dark and light.
 * Two kinds of checks:
 *  - layout rules that must hold whatever the data: no sideways scroll, the bottom navigation with five 44 px+
 *    targets, every button / field / tab a 44 px+ touch target, the last control on the page reachable above the nav;
 *  - a screenshot of the first screen compared with the committed one (visual regression). Text is drawn in DejaVu
 *    Sans (installed on the CI runner and here) and the clock is frozen, so a difference means the layout changed.
 *    After an intended change: `npx playwright test --project=phone --update-snapshots`, check the new images, commit.
 */
const PAGES: [string, string][] = [
  ["dashboard", "/"],
  ["screener", "/screener"],
  ["option-chain", "/option-chain"],
  ["positions", "/positions"],
  ["orders", "/orders"],
  ["alerts", "/notifications"],
  ["settings", "/settings"],
  ["copilot", "/copilot/market-pulse"],
  ["scanner", "/scanner"],
  ["signals", "/signals"],
];
const SIZES = [{ width: 360, height: 800 }, { width: 390, height: 844 }];
const THEMES = ["dark", "light"] as const;

async function open(page: Page, path: string, theme: "dark" | "light") {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");          // the install banner is PW1's; not part of these shots
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page);
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
}

for (const size of SIZES) {
  test.describe(`${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const [name, path] of PAGES) {
      test(`${name}: no sideways scroll, 44 px targets, nothing hidden behind the bottom bar`, async ({ page }) => {
        await open(page, path, "dark");
        const report = await page.evaluate(() => {
          const width = window.innerWidth;
          const inScroller = (e: Element) => {
            for (let a = e.parentElement; a; a = a.parentElement) if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(a).overflowX)) return true;
            return false;
          };
          const overflow = [...document.querySelectorAll("main *")]
            .filter((e) => e.getBoundingClientRect().right > width + 1 && !inScroller(e))
            .map((e) => e.tagName + ":" + (e.textContent ?? "").trim().slice(0, 30));
          const target = (e: Element) => {
            const box = e.getBoundingClientRect();
            const after = getComputedStyle(e, "::after");
            const grow = after.content !== "none" && after.position === "absolute" ? -2 * Math.min(0, parseFloat(after.top) || 0) : 0;
            return box.height + grow;
          };
          const small = [...document.querySelectorAll("button, a, input, select, textarea, [role=tab], [role=switch]")]
            .filter((e) => {
              const box = e.getBoundingClientRect();
              if (!box.width || !box.height || getComputedStyle(e).visibility === "hidden") return false;
              if (e.closest("table, p, li")) return false;                       // links inside text and table cells
              if (e.matches("input[type=checkbox], input[type=radio]")) return (e.closest("label")?.getBoundingClientRect().height ?? 0) < 43.5;
              if (e.tagName === "A" && !e.closest("header, nav")) return false;   // in-text links (the page's own prose)
              return target(e) < 43.5;
            })
            .map((e) => `${Math.round(target(e))}px ${e.tagName}:${(e.getAttribute("aria-label") ?? e.textContent ?? "").trim().slice(0, 30)}`);
          const nav = document.querySelector("nav[aria-label='Quick navigation']");
          const items = nav ? [...nav.querySelectorAll("a, button")].map((e) => e.getBoundingClientRect()) : [];
          return { scrollWidth: document.documentElement.scrollWidth, width, overflow, small, navItems: items.map((b) => [Math.round(b.width), Math.round(b.height)]) };
        });
        expect(report.scrollWidth, `page wider than the screen: ${report.overflow.join(", ")}`).toBeLessThanOrEqual(report.width);
        expect(report.small, "touch targets under 44 px").toEqual([]);
        expect(report.navItems).toHaveLength(5);
        for (const [w, h] of report.navItems) { expect(w).toBeGreaterThanOrEqual(44); expect(h).toBeGreaterThanOrEqual(44); }

        // The end of the page scrolls clear of the fixed bottom bar.
        await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
        const clear = await page.evaluate(() => {
          const nav = document.querySelector("nav[aria-label='Quick navigation']")!.getBoundingClientRect();
          const main = document.querySelector("main")!;
          const last = [...main.querySelectorAll("button, a, input, select, textarea")].filter((e) => e.getBoundingClientRect().height > 0).pop();
          return last ? last.getBoundingClientRect().bottom <= nav.top + 1 : true;
        });
        expect(clear, "the last control on the page sits under the bottom navigation").toBe(true);
      });

      for (const theme of THEMES) {
        test(`${name} looks the same (${theme})`, async ({ page }) => {
          await open(page, path, theme);
          await expect(page).toHaveScreenshot(`${name}-${size.width}-${theme}.png`, {
            animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000,
          });
        });
      }
    }

    test("More opens the full navigation and the drawer closes after a choice", async ({ page }) => {
      await open(page, "/", "dark");
      await page.getByRole("button", { name: "More pages" }).click();
      const drawer = page.locator("#app-navigation");
      await expect(drawer).toBeVisible();
      await drawer.getByRole("link", { name: "Orders" }).click();
      await expect(page).toHaveURL(/\/orders$/);
      await expect(drawer).toBeHidden();
      await expect(page.getByRole("navigation", { name: "Quick navigation" })).toBeVisible();
    });
  });
}

test.describe("desktop", () => {
  test.use({ viewport: { width: 1280, height: 800 } });
  test("no bottom navigation with a mouse and a wide screen", async ({ page }) => {
    await open(page, "/", "dark");
    await expect(page.getByRole("navigation", { name: "Quick navigation" })).toBeHidden();
  });
});
