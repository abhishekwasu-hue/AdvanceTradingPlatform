import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import detailsSource from "./Details.tsx?raw";
import {
  CARD_LINE_WORDS, Details, EmptyState, KeyValueTable, PageFootnote, ProgressStat, Signed, cardTextProblems, progressPct, signedParts, wordCount,
} from "./numbersFirst";

/** UX-11 (owner, 27 Oct): the shared "numbers first, text on demand" pieces. */

const html = (el: React.ReactElement) => renderToStaticMarkup(<MemoryRouter>{el}</MemoryRouter>);

describe("U2 Signed: one way for every value with a direction", () => {
  it("positive: up colour, ▲ and +; negative: down colour, ▼ and −; Indian grouping", () => {
    const up = html(<Signed value={161990} />);
    expect(up).toContain("text-up");
    expect(up).toContain("▲");
    expect(up).toContain("+₹1,61,990");
    const down = html(<Signed value={-2500.4} />);
    expect(down).toContain("text-down");
    expect(down).toContain("▼");
    expect(down).toContain("−₹2,500");
  });

  it("zero as shown and missing: muted grey, no arrow, no sign", () => {
    for (const v of [0, 0.3, null, undefined, Number.NaN]) {
      const out = html(<Signed value={v} />);
      expect(out, String(v)).toContain("text-fg-muted");
      expect(out).not.toMatch(/▲|▼|\+/);
    }
    expect(signedParts({ value: 0.004, kind: "pct" }).dir).toBe("flat");
  });

  it("never colour alone: the arrow is hidden from screen readers, the signed text is read; tabular", () => {
    const out = html(<Signed value={1.25} kind="pct" />);
    expect(out).toMatch(/aria-hidden="true"[^>]*>▲/);
    expect(out).toContain("+1.25%");
    expect(out).toContain("font-tabular");
    expect(html(<Signed value={-3} kind="pct" arrow={false} />)).not.toContain("▼");
  });

  it("count and compact money keep the full figure as the hover", () => {
    expect(signedParts({ value: -1200, kind: "count" })).toMatchObject({ text: "−1,200", dir: "down" });
    expect(signedParts({ value: 972.5, kind: "count", decimals: 2 })).toMatchObject({ text: "+972.50", dir: "up" });
    expect(signedParts({ value: 187500, kind: "count" })).toMatchObject({ text: "+1,87,500", dir: "up" });
    const big = signedParts({ value: 24_000_000, compact: true });
    expect(big.text).toBe("+₹2.4 Cr");
    expect(html(<Signed value={24_000_000} compact />)).toContain(`title="${big.full}"`);
  });
});

describe("U1 Details: one closed disclosure for the explanations", () => {
  it("a native details / summary, closed by default, marked for the guard", () => {
    const out = html(<Details id="x.card"><p>How it is worked out.</p></Details>);
    expect(out).toMatch(/^<details data-details="x.card"/);
    expect(out).not.toMatch(/<details[^>]*\sopen/);
    expect(out).toContain("<summary");
    expect(out).toContain("Details");
    expect(out).toContain("How it is worked out.");
  });

  it("the open state is remembered per card; blocked storage is safe", () => {
    expect(detailsSource).toContain("atp_details_open:");
    expect(detailsSource).toMatch(/try \{ return localStorage\.getItem/);
    expect(detailsSource).toMatch(/catch \{ \/\* intentional: storage unavailable \*\/ \}/);   // M63 I-7: a kept silence says why
  });
});

describe("U3 KeyValueTable and ProgressStat", () => {
  it("a description list: label left, value right, tabular", () => {
    const out = html(<KeyValueTable caption="Your figures" rows={[{ label: "Win rate", value: "54%" }, { label: "Closed trades", value: 12, testId: "closed" }]} />);
    expect(out).toContain('<dl aria-label="Your figures"');
    expect(out.match(/<dt/g)).toHaveLength(2);
    expect(out).toContain('data-kv-row="closed"');
    expect(out).toContain("font-tabular");
  });

  it("'12 of 30' with a thin progress bar the screen reader can read", () => {
    const out = html(<ProgressStat label="Closed PAPER trades" done={12} of={30} />);
    expect(out).toContain("12 of 30");
    expect(out).toContain('role="progressbar"');
    expect(out).toContain('aria-valuenow="12"');
    expect(out).toContain('aria-valuemax="30"');
    expect(out).toContain("width:40%");
    expect(progressPct(45, 30)).toBe(100);
    expect(progressPct(-1, 30)).toBe(0);
    expect(progressPct(3, 0)).toBe(0);
  });
});

describe("U4 EmptyState and U5 PageFootnote", () => {
  it("one line and one action: a link or a button", () => {
    const link = html(<EmptyState text="No PAPER trades yet · figures appear at 20 closed trades" action={{ label: "Start a PAPER trade", to: "/signals" }} />);
    expect(link).toContain('href="/signals"');
    const button = html(<EmptyState text="Nothing yet." action={{ label: "Try again", onClick: () => undefined }} />);
    expect(button).toContain("<button");
    const foot = html(<PageFootnote>Prices from your broker.</PageFootnote>);
    expect(foot).toContain("data-page-footnote");
    expect(foot).toContain("For information and education: every trading decision is yours. Prices from your broker.");
  });
});

describe("U6 the text budget", () => {
  it("words are runs with a letter (numbers and ₹ figures are not read as words)", () => {
    expect(wordCount("Win rate 54% over 12 trades, ₹1,500")).toBe(4);
    expect(CARD_LINE_WORDS).toBe(15);
  });

  it("one visible line of at most 15 words per card; every problem in plain words", () => {
    expect(cardTextProblems([{ card: "Fits", lines: ["Twelve closed PAPER trades so far."] }, { card: "Numbers only", lines: [] }])).toEqual([]);
    const problems = cardTextProblems([
      { card: "Paper account", lines: ["One line.", "Another line."] },
      { card: "Your figures", lines: ["word ".repeat(16)] },
    ]);
    expect(problems).toHaveLength(2);
    expect(problems[0]).toContain("Paper account: 2 visible lines");
    expect(problems[1]).toContain("Your figures: a visible line of 16 words");
  });
});
