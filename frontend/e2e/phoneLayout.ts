import { expect, type Page } from "@playwright/test";

/**
 * PW2 phone layout rules (moved here from mobile.e2e.ts unchanged, so the v2 shell's checks in shell-v2.e2e.ts use the
 * very same ones): no sideways scroll, the bottom navigation with five 44 px+ targets, every button / field / tab a
 * 44 px+ touch target, the last control on the page reachable above the bottom bar.
 */
export async function expectPhoneLayout(page: Page): Promise<void> {
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
    // Settings redesign B: the content of a closed <details> fold is not on screen (Chromium still reports its boxes).
    const folded = (e: Element) => { const d = e.closest("details:not([open])"); return !!d && !e.closest("summary"); };
    const small = [...document.querySelectorAll("button, a, input, select, textarea, [role=tab], [role=switch]")]
      .filter((e) => {
        if (folded(e)) return false;
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
    const folded = (e: Element) => { const d = e.closest("details:not([open])"); return !!d && !e.closest("summary"); };
    const last = [...main.querySelectorAll("button, a, input, select, textarea")].filter((e) => e.getBoundingClientRect().height > 0 && !folded(e)).pop();
    return last ? last.getBoundingClientRect().bottom <= nav.top + 1 : true;
  });
  expect(clear, "the last control on the page sits under the bottom navigation").toBe(true);
}
