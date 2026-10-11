import { describe, expect, it } from "vitest";
import appSource from "../App.tsx?raw";
import feedbackSource from "../feedback/FeedbackDialog.tsx?raw";
import bannerSource from "../legal/LegalAcceptanceBanner.tsx?raw";
import dialogSource from "./SupportDialog.tsx?raw";
import { QUESTION_MAX_CHARS, SUGGESTED_QUESTIONS, safeLinks, sourceLabel, supportQuestion } from "./support";

describe("support bot helpers (Z10-f)", () => {
  it("sends only a trimmed question of 2 to 500 characters", () => {
    expect(supportQuestion("  how do I   reset my password? ")).toBe("how do I reset my password?");
    expect(supportQuestion("x")).toBeNull();
    expect(supportQuestion("a".repeat(QUESTION_MAX_CHARS + 1))).toBeNull();
  });

  it("follows only in-app links from an answer", () => {
    const links = safeLinks([
      { title: "Positions", url: "/positions", source: "help" },
      { title: "Risk", url: "/legal/risk", source: "legal" },
      { title: "Out", url: "https://evil.example", source: "help" },
      { title: "Proto", url: "//evil.example", source: "help" },
      { title: "Script", url: "javascript:alert(1)", source: "help" },
    ]);
    expect(links.map((l) => l.url)).toEqual(["/positions", "/legal/risk"]);
    expect(safeLinks(undefined)).toEqual([]);
  });

  it("says where an answer came from", () => {
    expect(sourceLabel({ outcome: "answer", source: "help" })).toBe("From the help pages");
    expect(sourceLabel({ outcome: "answer", source: "ai" })).toContain("your organisation's AI provider");
    expect(sourceLabel({ outcome: "refused", source: "help" })).toContain("Not something");
    expect(sourceLabel({ outcome: "handoff", source: "help" })).toBe("Not in the help pages");
  });

  it("suggests how-to questions only", () => {
    for (const q of SUGGESTED_QUESTIONS) expect(q).toMatch(/^(How do I|Does the platform)/);
  });
});

describe("support bot wiring", () => {
  const app = appSource;
  const dialog = dialogSource;

  it("the help button exists only while the support_bot flag is on, and the dialog is its own chunk", () => {
    expect(app).toMatch(/setSupportOn\(f\.support_bot === true\)/);
    expect(app).toMatch(/\{user && supportOn && \(/);
    expect(app).toContain('import("./support/SupportDialog")');
  });

  it("a hand-off opens the feedback form with the question filled in", () => {
    expect(dialog).toContain("onFeedback(t.reply?.feedback_draft ?? t.q)");
    expect(app).toMatch(/initialText=\{feedbackDraft\}/);
    expect(feedbackSource).toContain("setText(initialText.slice(0, FEEDBACK_MAX_CHARS))");
  });

  it("the dialog is labelled for screen readers", () => {
    expect(dialog).toContain('role="log"');
    expect(dialog).toContain('aria-live="polite"');
    expect(dialog).toContain('htmlFor="support-question"');
    expect(dialog).toContain('aria-label="Ask"');
  });

  it("the help desk calls only the support endpoint - no positions, orders or trades", () => {
    expect(dialog).toContain("api.supportAsk(");
    expect(dialog).not.toMatch(/api\.(positions|orders|trades|placeOrder|closePosition|exit)/i);
  });

  it("the legal pages are public routes outside the signed-in shell", () => {
    expect(app).toMatch(/<Route path="\/legal\/:slug"/);
    expect(app).toContain('import("./legal/LegalPage")');
    expect(app).toContain("<LegalAcceptanceBanner />");
  });

  it("the acceptance prompt is a banner, never a modal that could block exits", () => {
    const banner = bannerSource;
    expect(banner).not.toMatch(/Dialog|Sheet|aria-modal/);
    expect(banner).toContain("Later");
  });
});
