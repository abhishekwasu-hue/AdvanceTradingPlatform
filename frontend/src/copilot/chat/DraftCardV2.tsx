import { Bell, Play, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { InPlaceLink } from "../../inplace/InPlaceLink";
import { Details } from "../../ui/Details";
import { screenerApi, type RunResponse } from "../../screener/api";
import { friendlyError } from "../../api/errors";
import { copilotPath } from "../tabs";
import { draftParams, draftSymbols, paramLabel, type AnswerDraft } from "./model";

/**
 * M72 PR-6 (CP-5): one editable draft the planner made from the user's plain words. A screen draft is edited, checked
 * and run HERE, only when the user taps, on the symbols he types (none are pre-filled) and with the values he fills
 * (a value he never stated is empty); nothing is saved. A proposed alert says it waits for his approval in the
 * Watchtower (it exists only after a person approves it; an alert notifies, it never places an order).
 */
const FIELD = "w-full rounded-control border border-border bg-surface-2 px-space-3 py-space-2 text-t14 text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const BUTTON = "inline-flex min-h-11 items-center justify-center gap-space-2 rounded-control border border-brand px-space-4 text-t14 font-semibold text-brand hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-60 md:min-h-9";
const LABEL = "block text-t12 font-medium text-fg-muted";

function ScreenDraft({ draft, index }: { draft: Extract<AnswerDraft, { kind: "screen" }>; index: number }) {
  const id = `draft-${index}`;
  const [text, setText] = useState(draft.text);
  const [values, setValues] = useState<Record<string, string>>(
    () => Object.fromEntries(draft.params.map((p) => [p.name, typeof p.value === "number" ? String(p.value) : ""])));
  const [symbols, setSymbols] = useState("");
  const [problems, setProblems] = useState<string[]>(draft.problems);
  const [checked, setChecked] = useState<boolean | null>(null);
  const [result, setResult] = useState<RunResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const names = draft.params.map((p) => p.name);
  const params = draftParams(values, names);
  const list = draftSymbols(symbols);
  const missing = params === null ? "Fill every value you decide" : list.length === 0 ? "Add the symbols to scan" : null;

  async function check() {
    setBusy(true); setError(null);
    try {
      const out = await screenerApi.validate(text, draft.base_tf, params ?? undefined);
      setProblems(out.problems.map((p) => p.message)); setChecked(out.ok);
    } catch (e) { setError(friendlyError(e) || "The screen could not be checked."); } finally { setBusy(false); }
  }
  async function run() {
    if (params === null || list.length === 0) return;
    setBusy(true); setError(null);
    try { setResult(await screenerApi.run(text, draft.base_tf, list, "NSE", params)); } catch (e) { setError(friendlyError(e) || "The screen could not run."); } finally { setBusy(false); }
  }

  return (
    <section className="space-y-space-3 rounded-panel border border-border p-space-3" data-draft="screen" aria-labelledby={`${id}-title`}>
      <div className="flex flex-wrap items-center gap-space-2">
        <h3 id={`${id}-title`} className="text-t15 font-semibold text-fg">Screen draft</h3>
        <span className="inline-flex min-h-7 items-center rounded-pill bg-surface-2 px-space-2 text-t12 font-semibold text-fg">{draft.base_tf}</span>
        <span className="text-t12 text-fg-muted">Not saved. Nothing runs until you tap Run.</span>
      </div>
      <div>
        <label htmlFor={`${id}-text`} className={LABEL}>Screen</label>
        <textarea id={`${id}-text`} value={text} onChange={(e) => { setText(e.target.value); setChecked(null); }} rows={3} spellCheck={false}
          className={`${FIELD} font-tabular`} data-draft-text />
      </div>
      {draft.params.length > 0 && (
        <fieldset className="grid gap-space-2 sm:grid-cols-2" data-draft-params>
          <legend className="mb-space-1 text-t13 font-semibold text-fg">Values you decide</legend>
          {draft.params.map((p) => (
            <div key={p.name}>
              <label htmlFor={`${id}-p-${p.name}`} className={LABEL}>{paramLabel(p.name)}</label>
              <input id={`${id}-p-${p.name}`} inputMode="decimal" value={values[p.name] ?? ""} placeholder="Not set" className={`${FIELD} font-tabular`}
                onChange={(e) => setValues((v) => ({ ...v, [p.name]: e.target.value }))} data-draft-param={p.name} />
            </div>
          ))}
        </fieldset>
      )}
      <div>
        <label htmlFor={`${id}-symbols`} className={LABEL}>Symbols to scan (up to 50)</label>
        <input id={`${id}-symbols`} value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="For example RELIANCE, TCS" className={FIELD} data-draft-symbols />
      </div>
      {problems.length > 0 && (
        <ul className="space-y-space-1 text-t13 text-warn" data-draft-problems>{problems.map((p) => <li key={p}>{p}</li>)}</ul>
      )}
      {checked === true && problems.length === 0 && <p className="text-t13 text-up" data-draft-checked>The screen checks out.</p>}
      <div className="flex flex-col gap-space-2 sm:flex-row sm:items-center">
        <button type="button" className={BUTTON} onClick={() => void check()} disabled={busy} data-draft-check><ShieldCheck size={16} aria-hidden />Check</button>
        <button type="button" className={BUTTON} onClick={() => void run()} disabled={busy || missing !== null} data-draft-run><Play size={16} aria-hidden />Run</button>
        {missing && <span className="text-t12 text-fg-muted" data-draft-missing>{missing}</span>}
      </div>
      {error && <p role="alert" className="text-t13 text-down">{error}</p>}
      {result && (
        <div className="text-t14 text-fg" data-draft-result>
          <p className="font-semibold"><span className="font-tabular">{result.matched.length}</span> of <span className="font-tabular">{result.scanned}</span> matched</p>
          {result.matched.length > 0 && <p className="mt-space-1 break-words text-t13">{result.matched.join(", ")}</p>}
          <p className="mt-space-1 text-t12 text-fg-muted">{result.disclaimer}</p>
        </div>
      )}
    </section>
  );
}

function AlertDraft() {
  return (
    <section className="flex items-start gap-space-3 rounded-panel border border-border p-space-3" data-draft="alert">
      <Bell size={18} aria-hidden className="mt-0.5 shrink-0 text-brand" />
      <div className="min-w-0">
        <h3 className="text-t15 font-semibold text-fg">Alert proposed</h3>
        <p className="mt-space-1 text-t13 text-fg-muted">Waits for your approval.</p>
        <Details id="copilot.draft.alert" className="mt-space-1"><p>It is created only after you approve it. An alert notifies you; it never places an order.</p></Details>
        <InPlaceLink to={copilotPath("watchtower")} className="mt-space-2 inline-flex min-h-11 items-center text-t14 font-semibold text-brand hover:underline md:min-h-0" data-draft-review>
          Review in Watchtower
        </InPlaceLink>
      </div>
    </section>
  );
}

/** M72 PR-6: the answer's drafts, each editable or reviewable in place. */
export function DraftCardsV2({ drafts }: { drafts: AnswerDraft[] }) {
  return (
    <div className="space-y-space-3" data-drafts>
      {drafts.map((d, i) => d.kind === "screen" ? <ScreenDraft key={`screen-${i}`} draft={d} index={i} /> : <AlertDraft key={`alert-${i}`} />)}
    </div>
  );
}

export default DraftCardsV2;
