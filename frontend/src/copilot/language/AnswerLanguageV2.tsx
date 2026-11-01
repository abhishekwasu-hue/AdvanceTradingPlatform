import { useEffect, useState } from "react";
import { Skeleton } from "../../components/primitives";
import { Details } from "../../ui/Details";
import { useFeatures } from "../../ui/useFeatures";
import { LANGUAGE_CHOICES, aiPrefsApi, speechRecogniser, type AiPrefs } from "./model";

/**
 * M72 PR-10 (Settings › AI, flag `copilot_languages`): "Copilot answers" - the language the Copilot answers in and the
 * microphone in the Copilot box. Saved at once; the interface itself stays English.
 */
const ROW = "flex min-h-11 items-center gap-space-3 text-t14 text-fg";
const BOX = "h-5 w-5 shrink-0 accent-brand";

export default function AnswerLanguageV2() {
  const on = useFeatures().on("copilot_languages");
  const [prefs, setPrefs] = useState<AiPrefs | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!on) return undefined;
    let live = true;
    aiPrefsApi.read().then((p) => { if (live) setPrefs(p); }, () => { if (live) setNote("Could not load your Copilot settings."); });
    return () => { live = false; };
  }, [on]);
  if (!on) return null;
  if (!prefs) return note ? <p className="text-t13 text-fg-muted">{note}</p> : <Skeleton className="h-16" label="Loading" />;

  async function save(next: AiPrefs) {
    setBusy(true); setNote(null);
    try { setPrefs(await aiPrefsApi.save(next)); setNote("Saved."); }
    catch { setNote("Could not save. Try again."); }
    finally { setBusy(false); }
  }

  const speech = speechRecogniser() !== null;
  return <AnswerLanguageForm prefs={prefs} busy={busy} speech={speech} note={note} onSave={(p) => void save(p)} />;
}

export function AnswerLanguageForm({ prefs, busy, speech, note, onSave }: { prefs: AiPrefs; busy: boolean; speech: boolean; note: string | null;
  onSave: (p: AiPrefs) => void }) {
  return (
    <div className="space-y-space-3" data-answer-language>
      <fieldset className="space-y-space-1" disabled={busy}>
        <legend className="text-t13 font-medium text-fg-muted">The Copilot answers in</legend>
        <div role="radiogroup" aria-label="Answer language" className="flex flex-wrap gap-space-2">
          {LANGUAGE_CHOICES.map((c) => (
            <label key={c.value} className={`${ROW} rounded-control border px-space-3 ${prefs.language === c.value ? "border-brand" : "border-border"}`}>
              <input type="radio" name="copilot-language" className={BOX} checked={prefs.language === c.value} onChange={() => onSave({ ...prefs, language: c.value })} />
              {c.label}
            </label>
          ))}
        </div>
      </fieldset>
      <label className={ROW}>
        <input type="checkbox" className={BOX} checked={prefs.voice} disabled={busy || !speech} onChange={(e) => onSave({ ...prefs, voice: e.target.checked })} />
        <span className="flex min-w-0 flex-col">
          <span>Speak my questions (microphone in the Copilot box)</span>
          {!speech && <span className="text-t13 text-fg-muted">This browser has no speech input.</span>}
        </span>
      </label>
      <Details id="copilot.language" className="mt-0">
        <p className="text-t13 text-fg-muted">Numbers, symbols and strikes stay exactly as in the data. Screens, menus and ATP&apos;s own rule answers stay in
          English. What you say is turned into text in the box; you read it and press Ask yourself.</p>
      </Details>
      {note && <p className="text-t13 text-fg-muted" role="status">{note}</p>}
    </div>
  );
}
