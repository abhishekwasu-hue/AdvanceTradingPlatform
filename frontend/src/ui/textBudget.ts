/**
 * UX-11 U6 (owner, 27 Oct): the text budget of a v2 card, as data so it is tested without a browser. A card shows a
 * title, its numbers / table / chart and at most ONE short visible line (≤ CARD_LINE_WORDS words); everything else is
 * in its Details. e2e/textBudget.ts reads each card's visible prose on a real page (paragraphs outside `[data-details]`,
 * alerts and status lines left out: they are state, not explanation) and hands it to `cardTextProblems`.
 */
export const CARD_LINE_WORDS = 15;

/** One card as the guard sees it: its name and its visible prose lines outside Details. */
export interface CardText { card: string; lines: readonly string[] }

export function wordCount(text: string): number {
  // Numbers, ₹ figures and symbols are not words a reader has to read; count only runs with a letter in them.
  return text.split(/\s+/).filter((w) => /\p{L}/u.test(w)).length;
}

/** Plain-words problems ("Paper account: 2 visible lines (one allowed)"); empty when every card fits. */
export function cardTextProblems(cards: readonly CardText[], limit = CARD_LINE_WORDS): string[] {
  const problems: string[] = [];
  for (const c of cards) {
    const lines = c.lines.map((l) => l.trim()).filter(Boolean);
    if (lines.length > 1) problems.push(`${c.card}: ${lines.length} visible lines (one allowed) - move the rest into Details`);
    for (const l of lines) {
      const n = wordCount(l);
      if (n > limit) problems.push(`${c.card}: a visible line of ${n} words (at most ${limit}): "${l.slice(0, 60)}…"`);
    }
  }
  return problems;
}
