import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { JournalLessons } from "../journal/types";
import { LessonsView, rupees } from "./LessonsCard";
import { LEARN_EN } from "./locales/learn.en";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");

// Shaped as tests/test_n2_journal_lessons.py's answer.
const DATA: JournalLessons = {
  cards: [{ tag: "fomo_entry", lesson: "pullback", trades: 3, losing_trades: 2, losing_net_pnl: -400, last_date: "2026-10-10" },
          { tag: "no_stop", lesson: "level", trades: 1, losing_trades: 1, losing_net_pnl: -300, last_date: "2026-10-10" }],
  weeks: 8, since: "2026-08-24", count_unconfirmed: true, basis: "Your own journal tags and those trades' net P&L after charges", as_of: "",
};

describe("LessonsView (N2-11)", () => {
  it("states the trader's own counts and links each lesson", () => {
    const html = renderToStaticMarkup(<LessonsView data={DATA} learnOn />);
    const words = text(html);
    expect(words).toContain("Learn from your journal");
    expect(words).toContain("Entry chasing beyond the plan's entry");
    expect(words).toContain("3 of your trades in the last 8 weeks carried this tag; 2 of them closed at a loss, -₹400 net together.");
    expect(words).toContain("Read the lesson: 3. Pullback");
    expect(html).toContain('href="/learn#learn-pullback"');
    expect(html).toContain('href="/learn#learn-level"');
    expect(words).toContain("Suggested tags count until you confirm or dismiss them");
    expect(html).not.toMatch(/<button/);
  });

  it("hides the lesson links while education_pages is off, keeping the facts", () => {
    const html = renderToStaticMarkup(<LessonsView data={DATA} learnOn={false} />);
    expect(html).not.toContain("/learn");
    expect(text(html)).not.toContain("Read the lesson");
    expect(text(html)).toContain("3 of your trades in the last 8 weeks carried this tag; 2 of them closed at a loss, -₹400 net together.");
  });

  it("says when there is nothing to show", () => {
    expect(text(renderToStaticMarkup(<LessonsView data={{ ...DATA, cards: [], count_unconfirmed: false }} learnOn />)))
      .toContain("No behaviour tag on a losing trade of yours in the last 8 weeks.");
  });

  it("formats rupees and keeps the lessons strings in the checked bundle", () => {
    expect(rupees(-400.4)).toBe("-₹400");
    expect(rupees(1234)).toBe("₹1,234");
    expect(Object.keys(LEARN_EN.lessons)).toEqual(["title", "intro", "card", "last", "read", "none", "unconfirmed"]);
  });
});
