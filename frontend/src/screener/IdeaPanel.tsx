/**
 * Handover 11j "idea -> scan" (flag `screener_copilot`, behind `ui_v2`): the trader writes a scan idea in plain words;
 * ATP reads it part by part into conditions built from the Screener's own functions only and shows, for each part, his
 * words, what the condition checks and what it assumed. Words ATP cannot read yet are listed as "Not supported yet" -
 * nothing is guessed. "Open as blocks" puts the draft into the funnel, where every block stays editable.
 *
 * 11j step 6 "edits by chat": once read, the trader changes the draft in plain words ("drop the volume condition",
 * "also RSI above 50", "weekly instead of daily"). The server replays the idea with its edits and answers each with one
 * plain line (what was removed, added or changed, or why nothing changed). Every edit that changes the draft is a new
 * version: the versions stay listed, each with what changed from the one before, and any of them can be opened.
 *
 * Nothing runs and nothing is saved here. A scan lists symbols that meet the trader's own conditions; it is not advice.
 */
import { useState } from "react";
import { ApiError, friendlyError } from "../api/errors";
import { Button, cx } from "../components/primitives";
import { screenerApi, type IdeaResponse } from "./api";
import type { ServerStage } from "./importStages";

export interface IdeaApi {
  idea: (text: string, baseTf: string, edits?: string[]) => Promise<IdeaResponse>;
}

/** One version of the draft: the idea as read, plus the edits that made it (in order), and the server's answer. */
export interface IdeaVersion { text: string; edits: string[]; result: IdeaResponse }

export interface IdeaPanelProps {
  baseTf: string;
  onOpen: (stages: ServerStage[], baseTf: string, parts: IdeaResponse["parts"]) => void;
  api?: IdeaApi;
  initialText?: string;
  initialResult?: IdeaResponse | null;
  initialVersions?: IdeaVersion[];
}

export const EDIT_EXAMPLES = ["drop the volume condition", "also RSI above 50", "weekly instead of daily"];

/** "Version 1 · your idea", "Version 2 · drop the volume condition". */
export function versionLabel(v: IdeaVersion, i: number): string {
  return `Version ${i + 1} · ${v.edits.length ? v.edits[v.edits.length - 1] : "your idea"}`;
}

/** What changed from the version before (the server's feedback on this version's last edit). */
export function changeLine(v: IdeaVersion): string | null {
  const last = v.result.edits?.[v.result.edits.length - 1];
  return v.edits.length && last ? last.message : null;
}

export const EXAMPLE_IDEA = "F&O stocks in a Daily uptrend, at a demand zone on falling volume, RSI near 40";
const NOT_AVAILABLE = "Not available on your account yet";

function errorText(e: unknown): string {
  if (e instanceof ApiError && (e.status === 404 || e.status === 503)) return "Scans from an idea are not turned on for this organisation yet.";
  return friendlyError(e);
}

/** The one plain line under the parts: how many became blocks. */
export function ideaSummary(r: IdeaResponse): string {
  const usable = r.parts.filter((p) => p.ok).length;
  if (!usable) return "No part of this idea could become a condition yet. Try the words in the example, or build the scan block by block.";
  return `${usable} ${usable === 1 ? "part" : "parts"} of your idea became ${r.stages.length === 1 ? "a block" : "blocks"}. Check each one, then open them in the builder.`;
}

export default function IdeaPanel({ baseTf, onOpen, api = screenerApi, initialText = "", initialResult = null, initialVersions }: IdeaPanelProps) {
  const [text, setText] = useState(initialText);
  const [versions, setVersions] = useState<IdeaVersion[]>(
    initialVersions ?? (initialResult ? [{ text: initialText, edits: [], result: initialResult }] : []));
  const [at, setAt] = useState((initialVersions?.length ?? 1) - 1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [edit, setEdit] = useState("");
  const [editBusy, setEditBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ message: string; changed: boolean } | null>(null);
  const shown = versions[at] ?? null;
  const result = shown?.result ?? null;

  const doRead = () => {
    if (text.trim().length < 3) return;
    const asked = text.trim();
    setBusy(true);
    setError(null);
    setFeedback(null);
    api.idea(asked, baseTf).then((r) => { setVersions([{ text: asked, edits: [], result: r }]); setAt(0); })
      .catch((e: unknown) => setError(errorText(e))).finally(() => setBusy(false));
  };

  // An edit builds on the version shown; a later version is replaced by the new one (like an undo history).
  const doEdit = () => {
    const words = edit.trim();
    if (!shown || !words) return;
    const edits = [...shown.edits, words];
    setEditBusy(true);
    setError(null);
    api.idea(shown.text, baseTf, edits).then((r) => {
      const last = r.edits?.[r.edits.length - 1];
      const changed = last ? last.changed : r.text !== shown.result.text;
      setFeedback({ message: last?.message ?? (changed ? "Your scan changed." : "Nothing changed."), changed });
      if (!changed) return;
      const next = [...versions.slice(0, at + 1), { text: shown.text, edits, result: r }];
      setVersions(next);
      setAt(next.length - 1);
      setEdit("");
    }).catch((e: unknown) => setError(errorText(e))).finally(() => setEditBusy(false));
  };

  return (
    <div data-testid="idea-panel" className="space-y-4 text-t13">
      <label className="block text-t12 text-fg-muted" htmlFor="idea-text">Your idea, in plain words</label>
      <textarea id="idea-text" value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={1000}
                placeholder={EXAMPLE_IDEA} data-testid="idea-text"
                className="w-full resize-y rounded-control border border-border bg-surface-inset px-2 py-2 text-t13 text-fg placeholder:text-fg-muted/60 focus:outline-none focus:ring-2 focus:ring-brand" />
      <div className="flex flex-wrap items-center gap-2">
        <Button variant={result ? "secondary" : "primary"} onClick={doRead} loading={busy} disabled={text.trim().length < 3} data-testid="idea-read">
          {result ? "Read it again" : "Make the scan"}
        </Button>
        {!text && <Button size="sm" onClick={() => setText(EXAMPLE_IDEA)} data-testid="idea-example">Use the example</Button>}
      </div>

      {error && <p role="alert" className="text-t12 text-warn">{error}</p>}

      {result && (
        <div className="space-y-3" aria-live="polite">
          <p className={cx("text-t13", result.ok ? "text-fg" : "text-warn")} data-testid="idea-status">{ideaSummary(result)}</p>

          {result.parts.length > 0 && (
            <ul className="space-y-2" data-testid="idea-parts">
              {result.parts.map((p, i) => (
                <li key={`${p.rule}-${i}`} className="rounded-panel border border-border bg-surface-1 p-3">
                  <p className="text-t12 text-fg-muted">You wrote: <span className="text-fg">“{p.phrase}”</span></p>
                  {p.ok ? (
                    <>
                      <p className="mt-1 text-fg">Checks: {p.reads}.</p>
                      {p.assumptions.length > 0 && <p className="mt-1 text-t12 text-fg-muted">Assumed: {p.assumptions.join("; ")}. You can change it in the block.</p>}
                    </>
                  ) : (
                    <p className="mt-1 text-t12 text-warn">{NOT_AVAILABLE}: left out of the scan.</p>
                  )}
                  <details className="mt-1 text-t12 text-fg-muted">
                    <summary className="cursor-pointer">Advanced: the screen condition</summary>
                    <code className="mt-1 block whitespace-pre-wrap break-all font-mono text-fg">{p.condition}</code>
                    {!p.ok && p.problems[0] && <span className="mt-1 block">{p.problems[0].message}</span>}
                  </details>
                </li>
              ))}
            </ul>
          )}

          {result.not_supported.length > 0 && (
            <div className="rounded-panel border border-border bg-surface-1 p-3" data-testid="idea-unsupported">
              <p className="font-semibold text-fg">Not supported yet</p>
              <ul className="mt-1 space-y-1">
                {result.not_supported.map((u, i) => (
                  <li key={i} className="text-fg">“{u.phrase}”{u.why !== "not supported yet" && <span className="text-t12 text-fg-muted"> - {u.why}</span>}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="space-y-2 rounded-panel border border-border bg-surface-1 p-3" data-testid="idea-edit">
            <label className="block font-semibold text-fg" htmlFor="idea-edit-text">Change it in plain words</label>
            <div className="flex flex-wrap gap-2">
              <input id="idea-edit-text" value={edit} onChange={(e) => setEdit(e.target.value)} maxLength={300} data-testid="idea-edit-text"
                     onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); doEdit(); } }}
                     placeholder={EDIT_EXAMPLES[0]}
                     className="h-9 min-w-0 flex-1 basis-48 rounded-control border border-border bg-surface-inset px-2 text-t13 text-fg placeholder:text-fg-muted/60 focus:outline-none focus:ring-2 focus:ring-brand" />
              <Button onClick={doEdit} loading={editBusy} disabled={!edit.trim()} data-testid="idea-edit-apply">Apply</Button>
            </div>
            <p className="text-t12 text-fg-muted">
              Try:{" "}
              {EDIT_EXAMPLES.map((x, i) => (
                <span key={x}>
                  {i > 0 && " · "}
                  <button type="button" className="underline decoration-dotted hover:text-fg" onClick={() => setEdit(x)}>{x}</button>
                </span>
              ))}
            </p>
            {feedback && (
              <p role="status" className={cx("text-t13", feedback.changed ? "text-fg" : "text-warn")} data-testid="idea-edit-feedback">{feedback.message}</p>
            )}
          </div>

          {versions.length > 1 && (
            <div className="space-y-2" data-testid="idea-versions">
              <p className="text-t12 font-semibold text-fg">Versions</p>
              <div className="flex flex-wrap gap-2">
                {versions.map((v, i) => (
                  <button key={i} type="button" aria-pressed={i === at} onClick={() => { setAt(i); setFeedback(null); }}
                          className={cx("max-w-full truncate rounded-control border px-2 py-1 text-t12",
                            i === at ? "border-brand bg-surface-2 text-fg" : "border-border text-fg-muted hover:text-fg")}>
                    {versionLabel(v, i)}
                  </button>
                ))}
              </div>
              {shown && changeLine(shown) && (
                <p className="text-t12 text-fg-muted" data-testid="idea-version-change">What changed from version {at}: <span className="text-fg">{changeLine(shown)}</span></p>
              )}
            </div>
          )}

          {result.ok && (
            <Button variant="primary" onClick={() => onOpen(result.stages, result.base_tf, result.parts)} data-testid="idea-open">
              {versions.length > 1 ? `Open version ${at + 1} as blocks` : "Open as blocks"}
            </Button>
          )}
          <p className="text-t12 text-fg-muted">{result.disclaimer}</p>
        </div>
      )}
    </div>
  );
}
