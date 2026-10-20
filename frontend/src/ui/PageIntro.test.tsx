import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PageIntroView, dismissIntro, isIntroDismissed, type IntroStore } from "./PageIntro";

function memoryStore(): IntroStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, read: (k) => data.get(k) ?? null, write: (k, v) => { data.set(k, v); } };
}

describe("UI2-1 'What is this page?' card", () => {
  it("shows two short lines and a close button; 'Show me' only when the page has a Help topic", () => {
    const html = renderToStaticMarkup(<PageIntroView lines={["Line one.", "Line two."]} onDismiss={() => undefined} />);
    expect(html).toContain("What is this page?");
    expect(html).toContain("Line one.");
    expect(html).toContain("Line two.");
    expect(html).toContain('aria-label="Close this explanation"');
    expect(html).not.toContain("Show me");
    expect(html).not.toMatch(/text-warn|bg-warn|bg-brand(?![\w/-])/);              // information, not a warning or an action
    const withHelp = renderToStaticMarkup(<PageIntroView lines={["a"]} showMe={{ href: "/help/charts" }} onDismiss={() => undefined} />);
    expect(withHelp).toContain('href="/help/charts"');
    expect(withHelp).toContain("Show me");
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
