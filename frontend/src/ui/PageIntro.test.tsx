import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { INTRO_TOGGLE, PageIntroView, dismissIntro, isIntroDismissed, type IntroStore } from "./PageIntro";

function memoryStore(): IntroStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, read: (k) => data.get(k) ?? null, write: (k, v) => { data.set(k, v); } };
}

describe("item 5: the page intro - one short line, collapsible, remembered per page", () => {
  it("open: the line, a Hide button, no 'What is this page?' box title; 'Show me' only with a Help topic", () => {
    const html = renderToStaticMarkup(<PageIntroView lines={["Line one."]} onDismiss={() => undefined} />);
    expect(html).not.toContain("What is this page?");
    expect(html).not.toMatch(/<h2/);                                            // the page title is the heading
    expect(html).toContain("Line one.");
    expect(html).toContain('data-page-intro="open"');
    expect(html).toContain('aria-label="Hide this tip"');
    expect(html).toContain('aria-expanded="true"');
    expect(html).not.toContain("Show me");
    expect(html).not.toMatch(/text-warn|bg-warn|bg-brand(?![\w/-])/);              // information, not a warning or an action
    const withHelp = renderToStaticMarkup(<PageIntroView lines={["a"]} showMe={{ href: "/help/charts" }} onDismiss={() => undefined} />);
    expect(withHelp).toContain('href="/help/charts"');
    expect(withHelp).toContain("Show me");
    const two = renderToStaticMarkup(<PageIntroView lines={["One.", "Two."]} onDismiss={() => undefined} />);
    expect(two.match(/<p[ >]/g)).toHaveLength(1);                               // older two-line callers: one paragraph
  });

  it("collapsed: only the small 'About this page' button, which opens it again", () => {
    const html = renderToStaticMarkup(<PageIntroView lines={["Line one."]} collapsed onDismiss={() => undefined} onExpand={() => undefined} />);
    expect(html).toContain('data-page-intro="collapsed"');
    expect(html).toContain(INTRO_TOGGLE);
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain("Line one.");
  });

  it("stays closed per user once closed; another user on the same browser still sees it", () => {
    const store = memoryStore();
    expect(isIntroDismissed(store, 7, "charts")).toBe(false);
    dismissIntro(store, 7, "charts");
    dismissIntro(store, 7, "charts");                                           // twice: stored once
    expect(isIntroDismissed(store, 7, "charts")).toBe(true);
    expect(isIntroDismissed(store, 7, "signals")).toBe(false);
    expect(isIntroDismissed(store, 8, "charts")).toBe(false);
    expect(JSON.parse(store.data.get("atp_page_intro_dismissed:7") ?? "[]")).toEqual(["charts"]);
  });

  it("never stores for a signed-out visitor, and survives unreadable storage", () => {
    const store = memoryStore();
    dismissIntro(store, null, "charts");
    expect(store.data.size).toBe(0);
    expect(isIntroDismissed(store, null, "charts")).toBe(false);
    store.data.set("atp_page_intro_dismissed:7", "{not json");
    expect(isIntroDismissed(store, 7, "charts")).toBe(false);
    dismissIntro(store, 7, "charts");
    expect(isIntroDismissed(store, 7, "charts")).toBe(true);
  });
});
