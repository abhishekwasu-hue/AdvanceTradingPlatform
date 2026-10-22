import { RotateCw, TriangleAlert } from "lucide-react";
import { useId } from "react";
import { errorSentence } from "../api/errors";

/**
 * UX1 (audit problem 4, U7 "error: what happened + what to do, one sentence"): the one error state every page shows
 * when something it reads or does fails - instead of the server's raw text ("Not found") in red. The sentence comes
 * from errorSentence (src/api/errors.ts), so a bare "Not found", a status code or a flag name never reaches the
 * trader. "Try again" shows when the page passes `onRetry`.
 *
 * - `variant="page"` (the default): a card in place of the page's content (the page could not load at all);
 * - `variant="inline"`: one line inside a page (a part of it, or an action, failed).
 */
export interface ErrorStateProps {
  /** What failed: an ApiError, any thrown value, or a sentence the page already has. */
  message: unknown;
  /** Shown as "Try again". */
  onRetry?: () => void;
  variant?: "page" | "inline";
  /** The card's heading (page variant). */
  title?: string;
  className?: string;
}

export function ErrorState({ message, onRetry, variant = "page", title = "This did not load", className = "" }: ErrorStateProps) {
  const titleId = useId();
  const sentence = errorSentence(message);
  const retry = onRetry && (
    <button type="button" onClick={onRetry}
      className="inline-flex min-h-11 items-center gap-space-2 rounded-control border border-border bg-surface-2 px-space-4 text-t14 font-semibold text-fg hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
      <RotateCw size={16} aria-hidden /> Try again
    </button>
  );
  if (variant === "inline") {
    return (
      <div role="alert" data-state="error" className={`flex flex-wrap items-center gap-space-3 rounded-control bg-warn-tint px-space-3 py-space-2 text-t14 text-fg ${className}`}>
        <TriangleAlert size={16} aria-hidden className="shrink-0 text-warn" />
        <span className="min-w-0 flex-1">{sentence}</span>
        {retry}
      </div>
    );
  }
  return (
    <section role="alert" aria-labelledby={titleId} data-state="error"
      className={`mx-auto w-full max-w-[480px] rounded-panel border border-subtle bg-surface-1 p-space-5 text-center shadow-card ${className}`}>
      <div aria-hidden className="mx-auto mb-space-3 flex h-11 w-11 items-center justify-center rounded-pill bg-warn-tint text-warn">
        <TriangleAlert size={20} />
      </div>
      <h2 id={titleId} className="text-t18 font-semibold text-fg">{title}</h2>
      <p className="mt-space-2 text-t14 text-fg-muted">{sentence}</p>
      {retry && <div className="mt-space-4 flex justify-center">{retry}</div>}
    </section>
  );
}
