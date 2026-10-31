import { readFileSync } from "node:fs";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DraftExplanation } from "../copilot/tabs/lab/DraftsPanel";
import { EventSummary } from "../pages/NewsEventsPage";
import { LibraryView } from "../pages/StrategyLibraryV2";
import { ReplyText } from "../support/SupportDialog";
import type { StrategyInfo } from "../types";
import { RichText, parseRichBlocks } from "./RichText";
import { stripMarkdown, stripMarkdownLine } from "./stripMarkdown";

/** M63 I-7: markdown is rendered or stripped, never printed raw. Made-up texts only. */

const SAMPLE = "# Your plan\n**Trend** first, then the *area*.\n\n- Read the `trend`\n- Mark the [area](/learn)\n\n1. one\n2. two";
const RAW_MARKS = /\*\*|(^|>)\s*#\s|(^|>)\s*-\s|`|\]\(/;
const html = (node: ReactElement) => renderToStaticMarkup(node);
const text = (markup: string) => markup.replace(/<[^>]+>/g, "");

describe("M63 I-7: RichText", () => {
  it("renders headings, bold, lists, code and safe links, with no raw marks left", () => {
    const out = html(<RichText source={SAMPLE} />);
    expect(out).toContain("<strong class=\"font-semibold\">Trend</strong>");
    expect(out).toMatch(/<ul[^>]*><li>Read the <code[^>]*>trend<\/code><\/li><li>Mark the <a href="\/learn"[^>]*>area<\/a><\/li><\/ul>/);
    expect(out).toMatch(/<ol[^>]*><li>one<\/li><li>two<\/li><\/ol>/);
    expect(out).toContain("<p class=\"font-semibold\">Your plan</p>");
    expect(text(out)).not.toMatch(/[*`#]|\]\(/);
  });

  it("keeps a line break inside a paragraph and drops an unclosed mark", () => {
    expect(html(<RichText source={"Line one\nLine **two"} />)).toContain("Line one<br/>Line two");
  });

  it("never renders HTML: tags in the text stay text, and an unsafe link is plain words", () => {
    const out = html(<RichText source={"<img src=x onerror=alert(1)> [x](javascript:alert(1))"} />);
    expect(out).not.toContain("<img");
    expect(out).not.toContain("javascript:");
    expect(out).toContain("&lt;img");
  });

  it("parses '* ' and '+ ' bullets as one list and '---' as a break", () => {
    expect(parseRichBlocks("* a\n+ b\n---\nc")).toEqual([
      { kind: "list", ordered: false, items: ["a", "b"] }, { kind: "paragraph", lines: ["c"] }]);
  });

  it("an empty text renders nothing", () => {
    expect(html(<RichText source="" />)).toBe("");
  });
});

describe("M63 I-7: stripMarkdown", () => {
  it("turns markdown into plain words", () => {
    expect(stripMarkdown(SAMPLE)).toBe("Your plan\nTrend first, then the area.\n\nRead the trend\nMark the area\n\n1. one\n2. two");
    expect(stripMarkdownLine("**RBI** holds\n- the _repo_ rate ~~cut~~")).toBe("RBI holds the repo rate cut");
  });

  it("leaves plain text, a hyphen in a sentence and snake_case alone", () => {
    expect(stripMarkdown("Price - range 2 * 3, ema_fast")).toBe("Price - range 2 * 3, ema_fast");
    expect(stripMarkdown(null)).toBe("");
  });
});

describe("M63 I-7: each fixed surface shows no raw markdown", () => {
  const MD = "**Open** Settings, then:\n- Broker\n- AI key";

  it("the help desk answer", () => {
    const out = html(<ReplyText answer={MD} />);
    expect(out).toContain("<strong class=\"font-semibold\">Open</strong>");
    expect(out).toMatch(/<ul[^>]*><li>Broker<\/li><li>AI key<\/li><\/ul>/);
    expect(text(out)).not.toMatch(RAW_MARKS);
  });

  it("the AI draft explanation", () => {
    const out = html(<DraftExplanation text={MD} />);
    expect(out).toMatch(/<ul[^>]*><li>Broker<\/li>/);
    expect(text(out)).not.toMatch(RAW_MARKS);
  });

  it("a news event's description (one line: stripped)", () => {
    const out = html(<EventSummary text={"**RBI** keeps the [repo rate](https://example.org) - `steady`"} />);
    expect(text(out)).toBe("RBI keeps the repo rate - steady");
  });

  it("a strategy description: the card line stripped, the fold rendered", () => {
    const s: StrategyInfo = { id: "demo_md", name: "Made-up marks", category: "indicator_based", timeframes: ["5min"], default_params: {},
      description: "**Fast** average crosses a slow one. How:\n- the fast line\n- the slow line" };
    const out = html(<LibraryView strategies={[s]} error={null} inUse={new Set()} category="all" query="" chosen="demo_md"
      onCategory={() => undefined} onQuery={() => undefined} onChoose={() => undefined} onTry={() => undefined} />);
    expect(out).toContain(">Fast average crosses a slow one.</p>");
    expect(out).toMatch(/<ul[^>]*><li>the fast line<\/li><li>the slow line<\/li><\/ul>/);
    expect(text(out)).not.toContain("**");
  });
});

/** The guard: a known markdown-carrying field is never put on screen raw. Each entry is a file and the field's
 * expression there; the field must go through RichText or stripMarkdown (src/ui), never as a raw JSX child or label. Add a
 * row when a new surface shows an AI answer, a help passage, a news summary or a strategy description. */
const GUARDED: { file: string; field: string }[] = [
  { file: "src/support/SupportDialog.tsx", field: "t.reply.answer" },
  { file: "src/copilot/tabs/lab/DraftsPanel.tsx", field: "selected.explanation" },
  { file: "src/pages/NewsEventsPage.tsx", field: "e.description" },
  { file: "src/pages/NewsEventsPage.tsx", field: "e.classification.one_line_en" },
  { file: "src/pages/StrategyLibraryV2.tsx", field: "s.description" },
];
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

describe("M63 I-7 guard: markdown-carrying fields go through the renderer or the strip helper", () => {
  for (const { file, field } of GUARDED) {
    it(`${file}: ${field}`, () => {
      const src = readFileSync(file, "utf8");
      expect(src, `${file} no longer reads ${field}: update the guard`).toContain(field);
      const f = esc(field);
      expect(src).not.toMatch(new RegExp(`>\\s*\\{\\s*${f}\\s*\\}|\\{\\s*${f}\\s*\\}\\s*<`));          // {field} as a JSX child
      expect(src).not.toMatch(new RegExp(`(title|aria-label|placeholder|alt)=\\{\\s*${f}\\s*\\}`));    // or as a label
      expect(src).not.toMatch(new RegExp(`firstSentence\\(\\s*${f}\\s*\\)`));
      expect(src).toMatch(/from "(\.\.\/)+ui\/(RichText|stripMarkdown)"/);
    });
  }

  it("the renderer and the helper never write HTML", () => {
    for (const f of ["src/ui/RichText.tsx", "src/ui/stripMarkdown.ts", "src/legal/markdown.tsx"]) {
      expect(readFileSync(f, "utf8")).not.toContain("dangerouslySetInnerHTML");
    }
  });
});
