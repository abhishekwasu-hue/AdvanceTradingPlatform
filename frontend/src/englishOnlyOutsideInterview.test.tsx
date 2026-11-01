import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import AppearanceCard from "./components/AppearanceCard";

/**
 * ATP is English only; the Copilot strategy interview is the one trilingual screen (the owner's ruling, Oct 2026). The
 * repo-wide Devanagari check (scripts/check_english_only.py) cannot see a Latin-script language name, a "Read in
 * Marathi" link, a language toggle or a browser-language hand-off, so this test reads every UI source file outside the
 * interview for them. A file that legitimately belongs to the interview goes in INTERVIEW_FILES.
 */
const SOURCES = import.meta.glob<string>(["./**/*.ts", "./**/*.tsx", "!./**/*.test.ts", "!./**/*.test.tsx", "!./stories/**"],
  { query: "?raw", import: "default", eager: true });

// The interview's own files: its screen, its second-language line and switch, its namespace and its types.
const INTERVIEW_FILES = new Set([
  "./copilot/tabs/IdeaBuilderTab.tsx",
  "./copilot/components/BilingualQuestion.tsx",
  "./copilot/components/InterviewLanguageSwitch.tsx",
  "./copilot/languagePrefs.ts",
  "./copilot/i18n.ts",                     // wires the `interview` namespace; useSecondary is for the interview screen
  "./copilot/locales/en.ts",               // idea.secondLine / idea.secondLineOption: the interview switch's English labels
  "./i18n/interviewSecondary.ts",
  "./types.ts",                            // the interview payload types (InterviewQuestion.mr, ...)
]);

// M72 PR-10 (owner order M72 CP-10, M72Q-2 confirmed 1 Nov - it supersedes EO-4 for Copilot ANSWERS only): the one place
// that offers the Copilot's answer language (Settings > AI, flag copilot_languages). Its labels are English words; the
// interface itself stays English everywhere.
const ANSWER_LANGUAGE_FILES = new Set([
  "./copilot/language/model.ts",
  "./copilot/language/AnswerLanguageV2.tsx",
]);

const FORBIDDEN: [string, RegExp][] = [
  ["a Marathi / Hindi / Hinglish label", /\b(marathi|hindi|hinglish)\b/i],
  ["a lang attribute for Marathi / Hindi", /\blang=["'{]*\s*["'](mr|hi)["']/],
  ["the browser's language sent to the server", /\bnavigator\.languages?\b/],
  ["a second-language field read from a payload", /\.(text|body|title|notice|consent)\.(mr|hi)\b|\b(text|body|title)_(mr|hi)\b/],
  ["the interview's language switch or its second-language line", /\b(useCopilotLanguage|useSecondary|InterviewLanguageSwitch|BilingualQuestion|INTERVIEW_MR|INTERVIEW_HI)\b/],
  ["an AI answer-language setting", /\b(aiPreferences|aiSavePreferences|ai_language)\b|answer language|Copilot languages/i],
];

describe("English only outside the strategy interview", () => {
  it("reads a sensible number of UI source files", () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(100);
    for (const f of [...INTERVIEW_FILES, ...ANSWER_LANGUAGE_FILES]) expect(Object.keys(SOURCES)).toContain(f);   // a renamed file is noticed
  });

  it("no UI file outside the interview offers or shows another language", () => {
    const findings: string[] = [];
    for (const [path, text] of Object.entries(SOURCES)) {
      if (INTERVIEW_FILES.has(path) || ANSWER_LANGUAGE_FILES.has(path)) continue;
      text.split("\n").forEach((line, i) => {
        for (const [what, re] of FORBIDDEN) if (re.test(line)) findings.push(`${path}:${i + 1}: ${what}: ${line.trim().slice(0, 120)}`);
      });
    }
    expect(findings).toEqual([]);
  });

  it("Settings > Appearance has no AI-language toggle", () => {
    const html = renderToStaticMarkup(<AppearanceCard />);
    const words = html.replace(/<[^>]+>/g, " ");
    expect(words).toContain("Theme");
    expect(words).not.toMatch(/language|marathi|hindi/i);
    expect(html).not.toMatch(/\blang="/);
  });

  it("every AI call outside the interview is typed English-only", () => {
    const client = SOURCES["./api/client.ts"];
    expect(client).toMatch(/scannerAiPlan: \(text: string, language: "en" = "en"\)/);
    expect(client).toMatch(/scannerAiRead: \(scanRequest: ScannerRequest, result: ScannerResult, language: "en" = "en"\)/);
    expect(client).not.toMatch(/language\??: string\b/);
  });
});
