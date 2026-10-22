/**
 * Handover 11j "idea -> scan" (flag `screener_copilot`, behind `ui_v2`): the trader writes a scan idea in plain words;
 * ATP reads it part by part into conditions built from the Screener's own functions only and shows, for each part, his
 * words, what the condition checks and what it assumed. Words ATP cannot read yet are listed as "Not supported yet" -
 * nothing is guessed. "Open as blocks" puts the draft into the funnel, where every block stays editable.
 *
 * Nothing runs and nothing is saved here. A scan lists symbols that meet the trader's own conditions; it is not advice.
 */
import { useState } from "react";
import { ApiError, friendlyError } from "../api/errors";
import { Button, cx } from "../components/primitives";
import { screenerApi, type IdeaResponse } from "./api";
import type { ServerStage } from "./importStages";

export interface IdeaApi {
  idea: (text: string, baseTf: string) => Promise<IdeaResponse>;
}

export interface IdeaPanelProps {
  baseTf: string;
  onOpen: (stages: ServerStage[], baseTf: string) => void;
  api?: IdeaApi;
  initialText?: string;
  initialResult?: IdeaResponse | null;
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

export default function IdeaPanel({ baseTf, onOpen, api = screenerApi, initialText = "", initialResult = null }: IdeaPanelProps) {
  const [text, setText] = useState(initialText);
  const [result, setResult] = useState<IdeaResponse | null>(initialResult);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const doRead = () => {
    if (text.trim().length < 3) return;
    setBusy(true);
    setError(null);
    api.idea(text.trim(), baseTf).then(setResult).catch((e: unknown) => setError(errorText(e))).finally(() => setBusy(false));
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

          {result.ok && (
            <Button variant="primary" onClick={() => onOpen(result.stages, result.base_tf)} data-testid="idea-open">Open as blocks</Button>
          )}
          <p className="text-t12 text-fg-muted">{result.disclaimer}</p>
        </div>
      )}
    </div>
  );
}
