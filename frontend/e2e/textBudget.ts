import type { Page } from "@playwright/test";
import { cardTextProblems, type CardText } from "../src/ui/textBudget";

/**
 * UX-11 U6: what the text-budget guard reads on a real page. A card is a v2 card section (`section[aria-labelledby]`)
 * or anything marked `[data-card]`. Its visible lines are the paragraphs that belong to it (not a nested card's) and are
 * shown, outside Details, alerts / status lines, empty states and the page footnote.
 */
export async function readCards(page: Page): Promise<CardText[]> {
  return page.evaluate(() => {
    const CARD = "section[aria-labelledby], [data-card]";
    const SKIP = "[data-details], [role='alert'], [role='status'], [role='note'], [data-empty], [data-page-footnote], li, button, a, label, dt, dd, th, td";
    const cards = Array.from(document.querySelectorAll<HTMLElement>(`main :is(${CARD})`));
    return cards.map((card) => {
      const title = card.getAttribute("data-card") || card.querySelector("h2, h3")?.textContent?.trim() || "card";
      const lines = Array.from(card.querySelectorAll<HTMLElement>("p"))
        .filter((p) => p.closest(CARD) === card && !p.closest(SKIP) && p.offsetParent !== null)
        .map((p) => (p.innerText || "").replace(/\s+/g, " ").trim())
        .filter(Boolean);
      return { card: title, lines };
    });
  });
}

/** The guard: plain-words problems for the page on screen (empty = every card fits). */
export async function textBudgetProblems(page: Page): Promise<string[]> {
  return cardTextProblems(await readCards(page));
}

/** Visible words on the page's main area (closed Details are not rendered, so not counted): the before → after figure. */
export async function visibleWords(page: Page): Promise<number> {
  const text = await page.locator("main").innerText();
  return text.split(/\s+/).filter((w) => /\p{L}/u.test(w)).length;
}
