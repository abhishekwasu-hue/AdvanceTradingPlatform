import { useEffect, useState } from "react";
import { request } from "../../api/client";
import { useFeatures } from "../../ui/useFeatures";

/**
 * M72 PR-10 (CP-10, M72Q-2; flag `copilot_languages`): the language the Copilot ANSWERS in (English / Marathi / Hindi -
 * the interface stays English) and the browser's speech input in the Copilot box (off until he turns it on). Stored in his
 * own `user_prefs` namespace `ai` (backend app/me/prefs.py AiPrefs). Numbers and symbols in an answer stay as in the data.
 */
export type AnswerLanguage = "en" | "mr" | "hi";
export interface AiPrefs { language: AnswerLanguage; voice: boolean }
export const DEFAULT_AI_PREFS: AiPrefs = { language: "en", voice: false };
export const LANGUAGE_CHOICES: readonly { value: AnswerLanguage; label: string }[] = [
  { value: "en", label: "English" }, { value: "mr", label: "Marathi" }, { value: "hi", label: "Hindi" },
];
/** The speech recogniser's language for his answer language (Indian English, Marathi, Hindi). */
export const SPEECH_LANG: Record<AnswerLanguage, string> = { en: "en-IN", mr: "mr-IN", hi: "hi-IN" };

export const aiPrefsApi = {
  read: () => request<{ prefs: Record<string, unknown> }>("/me/prefs").then((r) => ({ ...DEFAULT_AI_PREFS, ...(r.prefs.ai as Partial<AiPrefs> | undefined) })),
  save: (p: AiPrefs) => request<{ value: AiPrefs }>("/me/prefs/ai", { method: "PUT", body: JSON.stringify(p) }).then((r) => ({ ...DEFAULT_AI_PREFS, ...r.value })),
};

/** His Copilot language choices while the flag is on (null while off or not read yet). */
export function useAiPrefs(): AiPrefs | null {
  const on = useFeatures().on("copilot_languages");
  const [prefs, setPrefs] = useState<AiPrefs | null>(null);
  useEffect(() => {
    if (!on) { setPrefs(null); return undefined; }
    let live = true;
    aiPrefsApi.read().then((p) => { if (live) setPrefs(p); }, () => { if (live) setPrefs(null); });
    return () => { live = false; };
  }, [on]);
  return prefs;
}

/** The browser's speech recogniser, when it has one (Chrome / Android); null elsewhere - then no microphone is shown. */
export interface Recogniser {
  lang: string; interimResults: boolean; continuous: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null; onerror: (() => void) | null;
  start: () => void; stop: () => void;
}
export function speechRecogniser(w: unknown = typeof window === "undefined" ? undefined : window): (new () => Recogniser) | null {
  const host = w as { SpeechRecognition?: new () => Recogniser; webkitSpeechRecognition?: new () => Recogniser } | undefined;
  return host?.SpeechRecognition ?? host?.webkitSpeechRecognition ?? null;
}

/** What he said, added to what he already typed (never sent by itself: he presses Ask). */
export function withSpoken(draft: string, spoken: string): string {
  const said = spoken.trim();
  if (!said) return draft;
  return draft.trim() ? `${draft.trimEnd()} ${said}` : said;
}
