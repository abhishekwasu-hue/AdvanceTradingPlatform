import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import fixture from "../../e2e/fixtures/learn_v2.json";
import { allNavItems, isItemVisible, navItemFor } from "../shell/navModel";
import { closedFlag } from "../shell/pageFlags";
import { PAGE_COMPONENTS_V2 } from "../shell/pagesV2";
import { shellKeyFromPath, shellTitle } from "../shell/shellRoutes";
import { callWords } from "../signals/language";
import { GLOSSARY } from "../ui/glossary";
import type { LearnLibrary } from "./api";
import { LearnView } from "./LearnPageV2";
import { ALL, LEARN_ADDRESS, LEARN_FLAG, isAppPath, learnGlossary, lessonsIn, safeVideoUrl, searchLessons } from "./model";

/** Readiness C13: the Learn page from the server's own payload (e2e/fixtures/learn_v2.json, pinned by the backend test
 * test_c13_learning.py), the education guard on what it shows, the safe links and the shell wiring behind `learn`. */

const LIB = fixture as unknown as LearnLibrary;

function render(props: { initialTab?: "lessons" | "glossary"; initialOpen?: string[] } = {}, library: LearnLibrary = LIB): string {
  return renderToStaticMarkup(<MemoryRouter><LearnView library={library} {...props} /></MemoryRouter>);
}

function readable(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
}

describe("the helpers", () => {
  it("groups and search", () => {
    expect(lessonsIn(LIB.lessons, ALL)).toHaveLength(LIB.lessons.length);
    expect(lessonsIn(LIB.lessons, "risk").every((l) => l.group === "risk")).toBe(true);
    expect(searchLessons(LIB.lessons, "divergence").map((l) => l.id)).toEqual(["structure-divergence"]);
    expect(searchLessons(LIB.lessons, "  ")).toHaveLength(LIB.lessons.length);
  });

  it("only plain https video links and in-app paths", () => {
    expect(safeVideoUrl("https://videos.example.com/a")).toBe("https://videos.example.com/a");
    for (const bad of ["http://videos.example.com/a", "javascript:alert(1)", "https://user:pw@example.com/", "not a link", null, ""]) {
      expect(safeVideoUrl(bad)).toBeNull();
    }
    expect(isAppPath("/charts")).toBe(true);
    expect(isAppPath("/analytics/journal")).toBe(true);
    for (const bad of ["https://example.com", "//example.com", "charts", "/Charts"]) expect(isAppPath(bad)).toBe(false);
  });

  it("the glossary here carries no security names and no trade-call words", () => {
    const shown = learnGlossary();
    expect(shown.length).toBeGreaterThan(20);
    for (const e of shown) {
      expect(callWords(`${e.term} ${e.definition}`)).toEqual([]);
      expect(`${e.term} ${e.definition}`).not.toMatch(/nifty|sensex/i);
    }
    const keys = shown.map((e) => e.key);
    expect(keys).not.toContain("stop");
    expect(keys).not.toContain("target");
    expect(shown.map((e) => e.term)).toEqual([...shown.map((e) => e.term)].sort((a, b) => a.localeCompare(b)));
    expect(learnGlossary("open interest").map((e) => e.key)).toContain("oi");
  });

  it("every glossary key a lesson names exists", () => {
    for (const l of LIB.lessons) for (const k of l.glossary) expect(Object.keys(GLOSSARY)).toContain(k);
  });
});

describe("the page", () => {
  it("shows the frame, the start-here list and every lesson title, with no video while none is set", () => {
    const html = render();
    const text = readable(html);
    for (const line of LIB.header.frame) expect(text).toContain(line);
    expect(text).toContain("New here? Start with these");
    for (const l of LIB.lessons) expect(text).toContain(l.title);
    expect(html).not.toContain("data-video-link");
    expect(html).not.toContain("data-course-link");
  });

  it("an open lesson shows its points, its words and its next step inside the app", () => {
    const html = render({ initialOpen: ["structure-divergence"] });
    const lesson = LIB.lessons.find((l) => l.id === "structure-divergence")!;
    for (const p of lesson.points) expect(readable(html)).toContain(p);
    expect(readable(html)).toContain("Relative strength index");
    expect(html).toContain('href="/charts"');
  });

  it("the operator's links show once set, opening in a new tab", () => {
    const lib: LearnLibrary = {
      ...LIB, course_url: "https://course.example.com",
      lessons: LIB.lessons.map((l) => (l.id === "paper-why" ? { ...l, video_url: "https://videos.example.com/paper" } : l)),
    };
    const html = render({ initialOpen: ["paper-why"] }, lib);
    expect(html).toContain('href="https://videos.example.com/paper"');
    expect(html).toContain('href="https://course.example.com/"');
    expect(html).toMatch(/rel="noopener noreferrer"/);
    const bad = render({ initialOpen: ["paper-why"] }, { ...lib, lessons: lib.lessons.map((l) => ({ ...l, video_url: "javascript:alert(1)" })) });
    expect(bad).not.toContain("javascript:");
  });

  it("the glossary tab lists the words", () => {
    const text = readable(render({ initialTab: "glossary" }));
    expect(text).toContain("Open interest");
    expect(text).not.toContain("NIFTY");
  });

  it("nothing on the page reads as a trade call, a price or a performance claim", () => {
    const all = LIB.lessons.map((l) => l.id);
    const text = readable(render({ initialOpen: all })) + readable(render({ initialTab: "glossary" }));
    expect(callWords(text)).toEqual([]);
    expect(text).not.toMatch(/accuracy|guarantee|returns of|sure shot|₹|\bRs\b/i);
    // The lessons hold no number at all; the glossary's scale words ("a line from 0 to 100") are definitions, not prices.
    for (const l of LIB.lessons) expect([l.title, l.summary, ...l.points].join(" ")).not.toMatch(/\d/);
  });
});

describe("the shell", () => {
  it("lives at /learn, behind `learn`", () => {
    expect(shellKeyFromPath(LEARN_ADDRESS)).toBe("learn");
    expect(shellTitle("learn")).toBe("Learn");
    expect(PAGE_COMPONENTS_V2.learn).toBeDefined();
    expect(closedFlag("learn", { answered: true, on: () => false })).toBe(LEARN_FLAG);
    expect(closedFlag("learn", { answered: true, on: () => true })).toBeNull();
    const item = navItemFor("learn")!;
    expect(item.path).toBe(LEARN_ADDRESS);
    const ctx = (on: boolean) => ({ role: "USER", on: (f: string) => on && f === LEARN_FLAG });
    expect(isItemVisible(item, ctx(false))).toBe(false);
    expect(isItemVisible(item, ctx(true))).toBe(true);
    expect(allNavItems().filter((i) => i.key === "learn")).toHaveLength(1);
  });
});
