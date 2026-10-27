import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "../components/primitives/Button";
import { AnalysisResultView } from "./AskResultV2";
import {
  MAX_SAVED, OC_ANALYSES_EVENT, QUESTION_MAX, askAi, defaultName, errorSentence, evaluateSpec, loadSaved, newId, saveSaved, shownResult, withAdded,
  type AnalysisSpec, type AskResponse, type SaveOutcome, type SavedItems, type SavedState,
} from "./askAi";

/**
 * OC3 (owner's order, PART C2): "Ask AI" on the Option chain - the Copilot attached to this page. The trader types the
 * analysis he wants in his own words; the server's AI writes an analysis spec, the server checks it and computes the
 * table (and an optional chart) from the real chain. Under every result: "How this was calculated" and the educational
 * disclaimer. A request that cannot be expressed gets one plain sentence and the closest analysis.
 *
 * "Save as My analysis" stores the spec in the user's own saved analyses (MyAnalysesV2 shows them as tabs that update
 * with the chain, with no AI). While a result is shown, a new `refreshKey` re-evaluates its spec on the new chain -
 * that uses no AI either.
 *
 * Standalone: `<AskAi symbol="NIFTY" expiry={expiry} refreshKey={tick} />`. `ask`, `evaluate`, `loadSaved` and
 * `saveSaved` replace the server calls (tests, stories).
 */
/** OC-2 (owner, 25 Oct): three suggested questions, each one tap. */
export const EXAMPLES = [
  "Show the CE-PE OI difference for ATM +- 5 strikes as a table",
  "Strikes where OI rose more than 20% today",
  "Which strikes have the highest call OI and put OI?",
];

export interface AskAiProps {
  symbol: string;
  expiry?: string | null;
  refreshKey?: number | string;
  ask?: (symbol: string, expiry: string | null, question: string) => Promise<AskResponse>;
  evaluate?: (symbol: string, expiry: string | null, spec: AnalysisSpec) => Promise<AskResponse>;
  load?: () => Promise<SavedState>;
  save?: (items: SavedItems, version: number) => Promise<SaveOutcome>;
}

export function AskAi({ symbol, expiry = null, refreshKey, ask = askAi, evaluate = evaluateSpec, load = loadSaved, save = saveSaved }: AskAiProps) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [response, setResponse] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveNote, setSaveNote] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const calls = useRef({ ask, evaluate, load, save });
  calls.current = { ask, evaluate, load, save };
  const asked = `${symbol.trim().toUpperCase()}|${expiry ?? ""}`;
  const shownFor = useRef(asked);
  const firstRefresh = useRef(true);

  // Another symbol or expiry: never keep the old one's numbers on screen.
  useEffect(() => {
    if (shownFor.current !== asked) { shownFor.current = asked; setResponse(null); setError(null); setSaveNote(null); }
  }, [asked]);

  // A new chain reading: the shown spec is evaluated again on it (no AI).
  useEffect(() => {
    if (firstRefresh.current) { firstRefresh.current = false; return; }
    const spec = response?.state === "ok" ? response.spec : null;
    if (!spec || !symbol.trim()) return;
    let live = true;
    calls.current.evaluate(symbol, expiry, spec)
      .then((r) => { if (live && r.state === "ok") setResponse((prev) => (prev ? { ...prev, ...r, state: "ok" } : r)); })
      .catch(() => { /* the last result stays; the next refresh tries again */ });
    return () => { live = false; };
  }, [refreshKey]);   // the shown spec is read through `response` at the time of the refresh

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    const q = question.trim();
    if (q.length < 3 || busy || !symbol.trim()) return;
    setBusy(true); setError(null); setSaveNote(null);
    try {
      setResponse(await calls.current.ask(symbol, expiry, q));
    } catch (err) {
      setResponse(null);
      setError(errorSentence(err));
    } finally {
      setBusy(false);
    }
  };

  const saveShown = async () => {
    const shown = shownResult(response);
    if (!shown || saving) return;
    setSaving(true); setSaveNote(null);
    try {
      setSaveNote(await saveAnalysis(shown.spec, calls.current.load, calls.current.save));
    } finally {
      setSaving(false);
    }
  };

  return (
    <AskAiView symbol={symbol} question={question} onQuestion={setQuestion} onSubmit={submit} busy={busy} response={response}
      error={error} onSave={saveShown} saving={saving} saveNote={saveNote}
      onExample={(text) => setQuestion(text)} />
  );
}

export default AskAi;

/** Adds `spec` to the user's saved analyses (one retry on another device's newer list). Returns the sentence to show. */
export async function saveAnalysis(spec: AnalysisSpec, load: () => Promise<SavedState>,
                                   save: (items: SavedItems, version: number) => Promise<SaveOutcome>): Promise<string> {
  let state: SavedState;
  try {
    state = await load();
  } catch {
    return "Your analyses could not be read. Check your connection and try again.";
  }
  for (let attempt = 0; attempt < 2; attempt++) {
    if (Object.keys(state.items).length >= MAX_SAVED) {
      return `You have ${MAX_SAVED} saved analyses, the most there can be. Delete one under My analyses first.`;
    }
    const name = defaultName(spec);
    const outcome = await save(withAdded(state.items, newId(state.items), { name, spec, saved_at: new Date().toISOString() }), state.version);
    if (outcome.ok) {
      if (typeof window !== "undefined") window.dispatchEvent(new Event(OC_ANALYSES_EVENT));
      return `Saved as "${name}" under My analyses. It updates with the chain and uses no AI.`;
    }
    if (!outcome.conflict) return outcome.message;
    state = outcome.conflict;
  }
  return "Your analyses were changed on another device. Try again.";
}

/** The box and its answer for a state already in hand (pure: no reads, no timers). */
export function AskAiView({ symbol, question, onQuestion, onSubmit, busy, response, error, onSave, saving, saveNote, onExample }: {
  symbol: string; question: string; onQuestion: (q: string) => void; onSubmit: (e?: FormEvent) => void; busy: boolean;
  response: AskResponse | null; error: string | null; onSave: () => void; saving: boolean; saveNote: string | null;
  onExample: (text: string) => void;
}) {
  const shown = shownResult(response);
  const noSymbol = !symbol.trim();
  return (
    <section className="space-y-space-3 rounded-panel border border-subtle bg-surface-1 p-space-3 md:p-space-4" aria-label="Ask AI about this option chain"
      data-oc-ask={response?.state ?? (error ? "error" : "idle")}>
      <div>
        <h2 className="text-t15 font-semibold text-fg">Ask AI</h2>
        <p className="text-t13 text-fg-muted">Describe the analysis you want in your own words. The AI writes it as an analysis; the numbers come from the chain.</p>
      </div>
      <form className="flex flex-col gap-space-2 md:flex-row" onSubmit={onSubmit}>
        <label className="sr-only" htmlFor="oc-ask-question">Your analysis request</label>
        <input id="oc-ask-question" type="text" value={question} maxLength={QUESTION_MAX} disabled={busy || noSymbol}
          onChange={(e) => onQuestion(e.target.value)} placeholder={EXAMPLES[0]}
          className="min-h-11 w-full min-w-0 flex-1 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg placeholder:text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand" />
        <Button type="submit" variant="primary" className="min-h-11" loading={busy} disabled={noSymbol || question.trim().length < 3}>Ask</Button>
      </form>
      {!response && !error && (
        <div className="flex flex-wrap gap-space-1" aria-label="Examples">
          {EXAMPLES.map((text) => (
            <button key={text} type="button" onClick={() => onExample(text)} disabled={busy}
              className="min-h-11 rounded-control bg-surface-2 px-space-2 text-left text-t12 text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
              {text}
            </button>
          ))}
        </div>
      )}
      {noSymbol && <p className="text-t13 text-fg-muted">Pick a symbol first.</p>}
      {busy && <p className="text-t13 text-fg-muted" role="status">Working out the analysis…</p>}
      {error && <p className="text-t14 text-fg" role="alert">{error}</p>}
      {response && !shown && <PlainAnswer response={response} />}
      {response && shown && (
        <>
          <AnalysisResultView evaluated={shown} disclaimer={response.disclaimer} asOf={response.data?.data_as_of} />
          {response.data?.history_note && <p className="text-t12 text-fg-muted">{response.data.history_note}</p>}
          <div className="flex flex-wrap items-center gap-space-2">
            <Button className="min-h-11" onClick={onSave} loading={saving}>Save as My analysis</Button>
            {saveNote && <p className="text-t13 text-fg-muted" role="status">{saveNote}</p>}
          </div>
        </>
      )}
    </section>
  );
}

/** A state with no table of its own: the server's plain sentence, and for an unsupported request the closest analysis. */
export function PlainAnswer({ response }: { response: AskResponse }) {
  return (
    <div className="space-y-space-3" data-oc-ask-plain={response.state}>
      {response.message && <p className="text-t14 text-fg">{response.message}</p>}
      {/* OC-3: with no AI connected, the one way to fix it, one tap away. */}
      {response.state === "ai_off" && (
        <a href="/settings/ai" data-oc-ask-add-key className="inline-flex min-h-11 items-center font-semibold text-brand hover:underline md:min-h-0">Add your AI key</a>
      )}
      {response.closest && (
        <div className="space-y-space-2 rounded-panel border border-subtle p-space-2">
          <p className="text-t13 text-fg-muted">{response.closest.note}</p>
          <AnalysisResultView evaluated={response.closest} disclaimer={response.disclaimer} asOf={response.data?.data_as_of} />
        </div>
      )}
      {!response.closest && (response.state === "unsupported" || response.state === "cannot") && (
        <p className="text-t12 text-fg-muted">{response.disclaimer}</p>
      )}
    </div>
  );
}
