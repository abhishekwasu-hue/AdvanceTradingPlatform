import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import sectionSource from "../../settings/SettingsSectionV2.tsx?raw";
import chatSource from "../chat/CopilotChatV2.tsx?raw";
import { AnswerLanguageForm } from "./AnswerLanguageV2";
import { DEFAULT_AI_PREFS, LANGUAGE_CHOICES, SPEECH_LANG, speechRecogniser, withSpoken } from "./model";
import { VoiceButtonV2 } from "./VoiceButtonV2";
import voiceSource from "./VoiceButtonV2.tsx?raw";

/** M72 PR-10 (CP-10): the answer language (UI stays English) and the opt-in speech input. */

describe("M72 PR-10 Copilot languages and voice", () => {
  it("English by default, voice off; the choices are English words", () => {
    expect(DEFAULT_AI_PREFS).toEqual({ language: "en", voice: false });
    expect(LANGUAGE_CHOICES.map((c) => c.label)).toEqual(["English", "Marathi", "Hindi"]);
    for (const c of LANGUAGE_CHOICES) expect(c.label).toMatch(/^[A-Za-z]+$/);     // no Devanagari in the interface
    expect(SPEECH_LANG).toEqual({ en: "en-IN", mr: "mr-IN", hi: "hi-IN" });
    const html = renderToStaticMarkup(<AnswerLanguageForm prefs={DEFAULT_AI_PREFS} busy={false} speech={false} note={null} onSave={() => undefined} />);
    expect(html).toMatch(/checked=""[^>]*\/?>English|value="en"/);
    expect(html).toContain("This browser has no speech input.");
  });

  it("speech only where the browser has it; what he says is added, never sent", () => {
    expect(speechRecogniser({})).toBeNull();
    function Fake() { /* a recogniser */ }
    expect(speechRecogniser({ webkitSpeechRecognition: Fake })).toBe(Fake);
    expect(withSpoken("", " NIFTY chain ")).toBe("NIFTY chain");
    expect(withSpoken("Explain", "open interest")).toBe("Explain open interest");
    expect(withSpoken("Explain", "  ")).toBe("Explain");
    expect(renderToStaticMarkup(<VoiceButtonV2 prefs={null} draft="" onDraft={() => undefined} />)).toBe("");
    expect(renderToStaticMarkup(<VoiceButtonV2 prefs={{ language: "mr", voice: false }} draft="" onDraft={() => undefined} />)).toBe("");
    expect(voiceSource).not.toMatch(/onAsk|submit\(/);
  });

  it("wired behind the flag in Settings › AI and the Copilot box", () => {
    expect(sectionSource).toContain('useFeatures().on("copilot_languages")');
    expect(sectionSource).toContain("<AnswerLanguageV2 />");
    expect(chatSource).toContain("<VoiceButtonV2 prefs={aiPrefs}");
  });
});
