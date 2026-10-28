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
  // Today, Charts, Signals, My trades and More; P1-8 (2): Watchlist too while `watchlists` is on.
  expect([5, 6]).toContain(report.navItems.length);
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

/** SCR-FIX E2: what counts as a card - a page block, a v2 card, a panel, a dialog, the phone's fixed bottom bars. */
export const CARD_SELECTOR = "main section, main aside, main [data-card], main .rounded-panel, [role='dialog'], [data-testid='screener-bottom-bar'], nav[aria-label='Quick navigation']";

/**
 * SCR-FIX E2: no element runs out of its card. For each visible card (CARD_SELECTOR, inside `scope`), every visible
 * descendant's right edge stays at or left of the card's right edge. Left out: the content of a closed <details>, what
 * sits inside a box that scrolls or clips sideways within the card (a wide table in its own scroller), screen-reader-only
 * text (1 px boxes) and fixed / absolute pop-overs. Returns plain lines ("Conditions: TEXTAREA 'high - low' runs 38 px
 * past"); empty when every card holds its content.
 */
export async function cardOverflows(page: Page, scope = "body"): Promise<string[]> {
  return page.evaluate(([sel, scopeSel]) => {
    const root = document.querySelector(scopeSel);
    if (!root) return [`no ${scopeSel} on the page`];
    const shown = (e: Element) => {
      const b = e.getBoundingClientRect();
      if (b.width <= 1 || b.height <= 1) return false;
      const st = getComputedStyle(e);
      return st.visibility !== "hidden" && st.display !== "none";
    };
    const folded = (e: Element) => { const d = e.closest("details:not([open])"); return !!d && !e.closest("summary"); };
    const cards = [...root.querySelectorAll(sel)].filter(shown);
    if (root.matches(sel) && shown(root)) cards.unshift(root);
    const out: string[] = [];
    for (const card of cards) {
      const edge = card.getBoundingClientRect().right;
      const name = card.getAttribute("aria-label") || card.getAttribute("data-testid") || card.querySelector("h2, h3")?.textContent?.trim() || card.tagName;
      const clipped = (e: Element) => {
        for (let a = e.parentElement; a && a !== card; a = a.parentElement) if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(a).overflowX)) return true;
        return false;
      };
      const floating = (e: Element) => {
        for (let a: Element | null = e; a && a !== card; a = a.parentElement) if (/(fixed|absolute)/.test(getComputedStyle(a).position)) return true;
        return false;
      };
      for (const e of card.querySelectorAll("*")) {
        if (!shown(e) || folded(e) || clipped(e) || floating(e)) continue;
        const past = e.getBoundingClientRect().right - edge;
        if (past > 1) out.push(`${name}: ${e.tagName} '${(e.textContent ?? "").trim().slice(0, 30)}' runs ${Math.round(past)} px past`);
      }
    }
    return out;
  }, [CARD_SELECTOR, scope] as const);
}

/** SCR-FIX E2: the page never scrolls sideways (document scrollWidth <= clientWidth) and nothing runs out of its card. */
export async function expectNoSidewaysOverflow(page: Page, scope = "body"): Promise<void> {
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  expect(scroll, "document scrollWidth <= clientWidth").toBeLessThanOrEqual(client);
  expect(await cardOverflows(page, scope), "elements past their card's right edge").toEqual([]);
}
