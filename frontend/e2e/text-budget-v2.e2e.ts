import { expect, test } from "@playwright/test";
import { readCards, textBudgetProblems } from "./textBudget";

/**
 * UX-11 U6 (owner, 27 Oct): the text-budget guard. Each page converted to "numbers first, text on demand" joins
 * GUARDED (its own spec opens it with its mocks and calls `textBudgetProblems`); legal pages are never guarded
 * (Terms, Privacy, Risk disclosure). Here: the guard itself, on a hand-written page, so it can never pass by accident.
 */
const PAGE = `<!doctype html><html><body><main>
  <section aria-labelledby="a"><h2 id="a">Fits</h2><p>One short line under the numbers.</p>
    <details data-details="fits"><summary>Details</summary><p>${"word ".repeat(40)}</p></details></section>
  <section aria-labelledby="b"><h2 id="b">Too long</h2><p>${"word ".repeat(20)}</p></section>
  <section aria-labelledby="c"><h2 id="c">Two lines</h2><p>First line.</p><p>Second line.</p>
    <p role="status">Saved.</p><div data-empty><p>No trades yet.</p></div></section>
  <section aria-labelledby="d"><h2 id="d">Hidden</h2><p style="display:none">${"word ".repeat(30)}</p>
    <section aria-labelledby="e"><h2 id="e">Nested</h2><p>Its own line.</p></section></section>
  <section aria-labelledby="f"><h2 id="f">Bullets</h2><ul><li>TCS +₹1,240</li><li>The first bullet reads as a sentence.</li>
    <li>The second bullet reads as one too.</li></ul><details data-details="b"><summary>Details</summary><ul><li>Hidden bullet sentence here too.</li></ul></details></section>
</main></body></html>`;

test("the guard: one short visible line per card; Details, status, empty states and hidden text are not counted", async ({ page }) => {
  await page.setContent(PAGE);
  const cards = await readCards(page);
  expect(cards.map((c) => [c.card, c.lines.length])).toEqual([["Fits", 1], ["Too long", 1], ["Two lines", 2], ["Hidden", 0], ["Nested", 1], ["Bullets", 2]]);
  const problems = await textBudgetProblems(page);
  expect(problems).toHaveLength(3);
  expect(problems[0]).toContain("Too long: a visible line of 20 words");
  expect(problems[1]).toContain("Two lines: 2 visible lines");
  expect(problems[2]).toContain("Bullets: 2 visible lines");   // UX-11b C7: bullet prose counts; a data row does not
});
