import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import LegalConsentField from "./LegalConsentField";
import { LegalNav, LegalView } from "./LegalPage";
import { LEGAL_SLUGS, isLegalSlug, laterKey, legalPath, listTitles, acceptanceFor, type LegalDocument } from "./legal";
import { Markdown, parseBlocks, parseInline, safeHref } from "./markdown";

const BODY = `> **Draft — to be reviewed by a lawyer before launch.** Text in [SQUARE BRACKETS] is a placeholder.

# Privacy notice

This notice explains what personal data the platform holds.

## How long we keep it (retention)

- Sign-in attempts: 365 days.
- Sessions: 30 days after they expire
  or are ended.

## Grievance officer

Write to [GRIEVANCE OFFICER EMAIL] or read the [Risk disclosure](/legal/risk).
`;

const DOC: LegalDocument = {
  slug: "privacy", title: "Privacy notice", version: "2026-10-11", sha256: "a".repeat(64), draft: true, path: "/legal/privacy",
  body: BODY, open_placeholders: ["GRIEVANCE OFFICER EMAIL"],
};

describe("legal helpers (Z10-f)", () => {
  it("knows the three documents and their public paths", () => {
    expect(LEGAL_SLUGS).toEqual(["terms", "privacy", "risk"]);
    expect(legalPath("risk")).toBe("/legal/risk");
    expect(isLegalSlug("terms")).toBe(true);
    expect(isLegalSlug("cookies")).toBe(false);
    expect(isLegalSlug(undefined)).toBe(false);
  });

  it("sends the version and text hash of each accepted document, optionally only the pending ones", () => {
    const docs = [{ slug: "terms" as const, version: "2026-10-11", sha256: "a".repeat(64) },
                  { slug: "risk" as const, version: "2026-10-12", sha256: "b".repeat(64) }];
    expect(acceptanceFor(docs)).toEqual({ terms: { version: "2026-10-11", sha256: "a".repeat(64) }, risk: { version: "2026-10-12", sha256: "b".repeat(64) } });
    expect(acceptanceFor(docs, ["risk"])).toEqual({ risk: { version: "2026-10-12", sha256: "b".repeat(64) } });
  });

  it("names the pending documents in a sentence", () => {
    expect(listTitles(["terms", "privacy", "risk"])).toBe("Terms of use, Privacy notice and Risk disclosure");
    expect(listTitles(["risk"])).toBe("Risk disclosure");
  });

  it("'Later' is tied to the user, the version and the text, so a new version or an edit asks again", () => {
    const ack = (version: string, sha256 = "a".repeat(64)) => ({ terms: { version, sha256 } });
    const a = laterKey(7, ["terms"], ack("2026-10-11"));
    expect(a).not.toBe(laterKey(7, ["terms"], ack("2026-12-01")));
    expect(a).not.toBe(laterKey(7, ["terms"], ack("2026-10-11", "c".repeat(64))));
    expect(a).not.toBe(laterKey(8, ["terms"], ack("2026-10-11")));
  });
});

describe("the legal Markdown subset", () => {
  it("parses headings, quotes, lists (with wrapped items) and paragraphs", () => {
    const blocks = parseBlocks(BODY);
    expect(blocks.map((b) => b.kind)).toEqual(["quote", "heading", "paragraph", "heading", "list", "heading", "paragraph"]);
    const list = blocks[4];
    expect(list.kind === "list" && list.items[1]).toBe("Sessions: 30 days after they expire or are ended.");
  });

  it("allows only in-app, https and mailto links", () => {
    expect(safeHref("/legal/risk")).toBe("/legal/risk");
    expect(safeHref("https://www.sebi.gov.in")).toBe("https://www.sebi.gov.in");
    expect(safeHref("mailto:grievance@example.com")).toBe("mailto:grievance@example.com");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("//evil.example")).toBeNull();
    expect(safeHref("http://plain.example")).toBeNull();
    expect(safeHref("/\\evil.example")).toBeNull();          // "/\host" is protocol-relative in browsers
    const parts = parseInline("[click](javascript:void0) and [ok](/legal/terms)");
    expect(parts.filter((p) => p.kind === "link")).toEqual([{ kind: "link", text: "ok", href: "/legal/terms" }]);
    expect(parts[0]).toEqual({ kind: "text", text: "click" });
  });

  it("never renders raw HTML from the text", () => {
    const html = renderToStaticMarkup(<Markdown source={'A <script>alert(1)</script> and <img src=x onerror=alert(1)>'} />);
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;script&gt;");
  });
});

/** The a11y checks the SSR markup can prove: one h1, no skipped heading level, the draft banner as a note, every link
 * with text, external links that do not leak the opener. */
function headingLevels(html: string): number[] {
  return [...html.matchAll(/<h([1-6])\b/g)].map((m) => Number(m[1]));
}

describe("legal page render and accessibility", () => {
  const html = renderToStaticMarkup(<MemoryRouter><LegalView doc={DOC} /></MemoryRouter>);

  it("shows the version, the draft banner and the body", () => {
    expect(html).toContain("2026-10-11");
    expect(html).toContain("draft");
    expect(html).toContain('role="note"');
    expect(html).toContain("Draft — to be reviewed by a lawyer before launch.");
    expect(html).toContain("Sign-in attempts: 365 days.");
    expect(html).toContain('href="/legal/risk"');
  });

  it("has one h1 and no skipped heading levels", () => {
    const levels = headingLevels(html);
    expect(levels.filter((l) => l === 1)).toHaveLength(1);
    levels.forEach((l, i) => { if (i > 0) expect(l - levels[i - 1]).toBeLessThanOrEqual(1); });
    expect(html).toContain('aria-label="Privacy notice"');
  });

  it("the page nav marks the current document", () => {
    const nav = renderToStaticMarkup(<MemoryRouter><LegalNav current="privacy" /></MemoryRouter>);
    expect(nav).toContain('aria-label="Legal pages"');
    expect(nav.match(/aria-current="page"/g)).toHaveLength(1);
    expect(nav).toContain(">Terms of use<");
  });

  it("the sign-up box is required, labelled, and links to the three public pages in a new tab", () => {
    const box = renderToStaticMarkup(<LegalConsentField checked={false} onChange={() => undefined} />);
    expect(box).toMatch(/<input[^>]*id="legal-consent"[^>]*required=""/);
    expect(box).toContain('for="legal-consent"');
    for (const slug of LEGAL_SLUGS) expect(box).toContain(`href="/legal/${slug}"`);
    expect(box.match(/target="_blank"/g)).toHaveLength(3);
    expect(box.match(/rel="noopener"/g)).toHaveLength(3);
    expect(box).toContain("I am 18 or older");
    expect(box).toContain("paper only");
    expect(box).toContain("no investment advice");
  });
});

describe("public legal route next to the app shell", () => {
  // App.tsx: <Routes><Route path="/legal/:slug" .../><Route path="*" element={<AppShell />} /></Routes>, and the shell
  // keeps its own <Routes> with absolute paths. Both must still match.
  function Shell() {
    return (
      <Routes>
        <Route path="/settings" caseSensitive element={<p>settings page</p>} />
        <Route path="/" element={<p>dashboard</p>} />
        <Route path="*" element={<p>not found</p>} />
      </Routes>
    );
  }
  const at = (path: string) => renderToStaticMarkup(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/legal/:slug" caseSensitive element={<p>legal page</p>} />
        <Route path="*" element={<Shell />} />
      </Routes>
    </MemoryRouter>,
  );

  it("routes /legal/<slug> outside the shell and everything else into it", () => {
    expect(at("/legal/privacy")).toContain("legal page");
    expect(at("/settings")).toContain("settings page");
    expect(at("/")).toContain("dashboard");
    expect(at("/nope")).toContain("not found");
  });
});
