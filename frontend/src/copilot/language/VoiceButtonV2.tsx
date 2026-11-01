import { Mic, MicOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { SPEECH_LANG, speechRecogniser, withSpoken, type AiPrefs, type Recogniser } from "./model";

/**
 * M72 PR-10 (CP-10): the microphone in the Copilot box - only while his `voice` choice is on and the browser has speech
 * input. What he says is added to the box; it is never sent by itself (he presses Ask).
 */
export function VoiceButtonV2({ prefs, draft, onDraft, disabled }: { prefs: AiPrefs | null; draft: string; onDraft: (v: string) => void; disabled?: boolean }) {
  const [listening, setListening] = useState(false);
  const rec = useRef<Recogniser | null>(null);
  const latest = useRef(draft);
  latest.current = draft;
  useEffect(() => () => rec.current?.stop(), []);
  const Ctor = speechRecogniser();
  if (!prefs?.voice || !Ctor) return null;

  function toggle() {
    if (listening) { rec.current?.stop(); return; }
    const r = new Ctor!();
    r.lang = SPEECH_LANG[prefs!.language];
    r.interimResults = false;
    r.continuous = false;
    r.onresult = (e) => {
      const said = Array.from(e.results).map((res) => res[0]?.transcript ?? "").join(" ");
      onDraft(withSpoken(latest.current, said));
    };
    r.onend = () => setListening(false);
    r.onerror = () => setListening(false);
    rec.current = r;
    setListening(true);
    r.start();
  }

  return (
    <button type="button" onClick={toggle} disabled={disabled} aria-pressed={listening} aria-label={listening ? "Stop listening" : "Speak your question"} data-voice
      className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-control border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${listening ? "border-brand bg-surface-2 text-brand" : "border-border bg-surface-2 text-fg-muted hover:text-fg"}`}>
      {listening ? <MicOff size={18} aria-hidden /> : <Mic size={18} aria-hidden />}
    </button>
  );
}
