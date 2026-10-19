import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import InterviewLanguageSwitch from "./components/InterviewLanguageSwitch";
import { SECONDARY_LOCALES } from "./languagePrefs";

/**
 * The owner's ruling (Oct 2026): ATP is English only; the strategy interview is the one trilingual screen. Outside the
 * interview nothing offers Marathi / Hindi: no "Read in Marathi" on the Copilot's first-use notice, the AI
 * acknowledgement or the data-sharing consent, no language toggle in Settings; the interview's second line is switched
 * on the interview screen. (The repo-wide Devanagari check is scripts/check_english_only.py.)
 */
const RAW = import.meta.glob<string>(
  ["../components/AiProviderCard.tsx", "../components/AiAcknowledgementGate.tsx", "../components/AppearanceCard.tsx",
   "../components/GuideChat.tsx", "./components/FirstUseModal.tsx", "./tabs/IdeaBuilderTab.tsx", "../api/client.ts"],
  { query: "?raw", import: "default", eager: true });
const source = (name: string) => Object.entries(RAW).find(([path]) => path.endsWith(name))![1];

describe("English only outside the strategy interview", () => {
  it("drops the Marathi translations of the acknowledgement and the data consent", () => {
    for (const name of ["AiProviderCard.tsx", "AiAcknowledgementGate.tsx", "FirstUseModal.tsx"]) {
      expect(source(name)).not.toMatch(/Read in Marathi|text\.mr|readMr|lang="mr"/);
    }
  });

  it("has no language toggle in Settings and no AI answer language", () => {
    expect(source("AppearanceCard.tsx")).not.toMatch(/Marathi|useCopilotLanguage|SecondaryLocale/);
    expect(source("AiProviderCard.tsx")).not.toMatch(/aiPreferences|aiSavePreferences|answer language/);
    expect(source("GuideChat.tsx")).not.toMatch(/Marathi|aiPreferences/);
    expect(source("client.ts")).not.toMatch(/"en" \| "mr"|"mr" \| "en"/);
  });

  it("switches the interview's second line on the interview screen", () => {
    expect(source("IdeaBuilderTab.tsx").match(/<InterviewLanguageSwitch \/>/g)?.length).toBe(2);
    const html = renderToStaticMarkup(<InterviewLanguageSwitch />);
    expect(html).toContain("Second line under each question:");
    expect(html).toContain(">Marathi<");
    expect(html).toContain(">None<");
    expect(html).toContain('aria-pressed="true"');                        // the default (Marathi) is pressed
    expect([...SECONDARY_LOCALES]).toEqual(["mr"]);                       // + "hi" once the Hindi bundle is approved
  });
});
