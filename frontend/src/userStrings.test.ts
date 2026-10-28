import { describe, expect, it } from "vitest";

/**
 * UI-28 U1.7 (owner, 28 Oct): no developer jargon in what a trader reads. The words below never appear in a user string
 * of a v2 screen (JSX text, or a quoted sentence): "Phase 0", "PAPER only", "ScreenQL", "engine", "dish", "tenant",
 * "flag", "information only". Code names, comments, test ids and the admin console (admins see flags) are not user
 * strings. The one SEBI footer line lives in ui/Disclaimer.tsx (U1.8) and is the only "Information only" on a page.
 */
// The v2 screens and the text modules they render (ticket / basket notes, the ladder words).
const SOURCES = import.meta.glob<string>(["./**/*V2.tsx", "./**/*V2.ts", "./fno/insights.ts", "./fno/riskParts.ts", "./fno/ticketModel.ts",
  "./progression/locales/en.ts", "!./**/*.test.*", "!./stories/**", "!./admin/**"],
  { query: "?raw", import: "default", eager: true });

const BANNED = /\b(phase 0|paper only|screenql|engine|dish|tenant|flag|information only)\b/i;

/** The comment-free source. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/[^\n]*/g, "$1");

/** What a trader can read: JSX text between tags, and quoted strings that read as words (a space, no code look). */
export function userStrings(src: string): string[] {
  const c = code(src);
  const out: string[] = [];
  for (const m of c.matchAll(/>([^<>{}]*[A-Za-z][^<>{}]*)</g)) out.push(m[1].trim());
  for (const m of c.matchAll(/"([^"\n]*\s[^"\n]*)"|`([^`\n]*\s[^`\n]*)`/g)) {
    const s = (m[1] ?? m[2]).trim();
    if (/^[a-z0-9_.:/[\]()=&|!?<>+\-*\s{}$,'"-]+$/.test(s) && !/[A-Z]/.test(s) && !/\s(a|the|is|of|to|your|and)\s/.test(` ${s} `)) continue;   // class lists, selectors
    out.push(s);
  }
  return out.filter((s) => !/[{}=;]|&&|^data-|\)\s*$/.test(s));   // code that the two patterns above caught by accident
}

describe("UI-28 U1.7: no jargon in user strings", () => {
  it("reads the v2 screens", () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(60);
  });

  it("finds a banned word in a sentence and ignores a class list", () => {
    expect(userStrings(`<p className="text-t12 flag-x">Phase 0 has PAPER only.</p>`)).toContain("Phase 0 has PAPER only.");
    expect(userStrings(`const k = "rounded-pill bg-warn-tint px-space-2";`)).toEqual([]);
  });

  it("no v2 screen shows Phase 0, PAPER only, ScreenQL, engine, dish, tenant, flag or information only", () => {
    const findings: string[] = [];
    for (const [path, text] of Object.entries(SOURCES)) {
      for (const s of userStrings(text)) if (BANNED.test(s)) findings.push(`${path}: ${s.slice(0, 120)}`);
    }
    expect(findings).toEqual([]);
  });
});
