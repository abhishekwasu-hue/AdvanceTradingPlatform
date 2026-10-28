import type { Page } from "@playwright/test";
import { cardTextProblems, type CardText } from "../src/ui/textBudget";

/**
 * UX-11 U6: what the text-budget guard reads on a real page. A card is a v2 card section (`section[aria-labelledby]`),
 * a page block (`section[data-block]`), a Terminal panel (`[data-panel]`) or anything marked `[data-card]`. Its visible lines are the paragraphs that belong to it (not a nested card's) and are
 * shown, outside Details, alerts / status lines, empty states and the page footnote - and (UX-11b C7) its bullet
 * prose: list items that read as sentences - and (AUD-V6) leaf div / span text of 15 or more words.
 */
export async function readCards(page: Page): Promise<CardText[]> {
  return page.evaluate(() => {
    const CARD = "section[aria-labelledby], section[data-block], [data-card], [data-panel]";
    const SKIP = "[data-details], [role='alert'], [role='status'], [role='note'], [data-empty], [data-page-footnote], li, button, a, label, dt, dd, th, td";
    const cards = Array.from(document.querySelectorAll<HTMLElement>(`main :is(${CARD})`));
    return cards.map((card) => {
      const title = card.getAttribute("data-card") || card.getAttribute("aria-label") || card.querySelector("h2, h3")?.textContent?.trim() || "card";
      const lines = Array.from(card.querySelectorAll<HTMLElement>("p"))
        .filter((p) => p.closest(CARD) === card && !p.closest(SKIP) && p.offsetParent !== null)
        .map((p) => (p.innerText || "").replace(/\s+/g, " ").trim())
        .filter(Boolean);
      // UX-11b C7: bullet prose counts too - a list item that reads as a sentence (ends in . ! or ?, four or more words),
      // not a row of a list (a symbol and its numbers) and not a link or a button. Same exclusions as a paragraph.
      const LI_SKIP = SKIP.replace(/, li\b/, "");
      const prose = Array.from(card.querySelectorAll<HTMLElement>("li"))
        .filter((li) => li.closest(CARD) === card && !li.parentElement?.closest(LI_SKIP) && !li.closest("nav") && li.offsetParent !== null)
        .filter((li) => !li.querySelector("li, div, p, table, button, input, [data-signed]"))   // a row with blocks or numbers is data
        .map((li) => (li.innerText || "").replace(/\s+/g, " ").trim())
        .filter((t) => /[.!?]$/.test(t) && t.split(" ").filter((w) => /\p{L}/u.test(w)).length >= 4);
      lines.push(...prose);
      // AUD-V6 (owner audit 28 Oct): prose written straight into a div / span counts too - a leaf (no element inside)
      // of 15 or more words, not inside a paragraph or a list item (already read) and with the same exclusions.
      const LEAF_MIN_WORDS = 15;
      const leaves = Array.from(card.querySelectorAll<HTMLElement>("div, span"))
        .filter((el) => el.children.length === 0 && el.closest(CARD) === card && !el.closest(`${SKIP}, p`) && el.offsetParent !== null)
        .map((el) => (el.innerText || "").replace(/\s+/g, " ").trim())
        .filter((t) => t.split(" ").filter((w) => /\p{L}/u.test(w)).length >= LEAF_MIN_WORDS);
      lines.push(...leaves);
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
