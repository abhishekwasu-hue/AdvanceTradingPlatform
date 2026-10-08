import { RotateCcw, TriangleAlert } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

/** P1.1: one broken page no longer blanks the whole console. The shell (sidebar, top bar) stays usable, the page shows
 * a plain message with "Try again", and navigating elsewhere resets the boundary (the router keys it by path).
 * A chunk that fails to load after a new deploy (the old hashed file is gone) asks for a reload instead. */
export function isChunkLoadError(error: unknown): boolean {
  const text = error instanceof Error ? `${error.name} ${error.message}` : String(error);
  return /ChunkLoadError|Loading chunk|dynamically imported module|Importing a module script failed|error loading dynamically/i.test(text);
}

interface Props { children: ReactNode; title?: string }
interface State { error: unknown; resetKey: number }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: 0 };

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { error };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    // The details stay in the browser console for whoever debugs; the screen gets a sentence.
    console.error("Page error", error, info.componentStack);
  }

  private retry = () => this.setState((s) => ({ error: null, resetKey: s.resetKey + 1 }));

  render() {
    const { error, resetKey } = this.state;
    if (error === null) return <div key={resetKey} className="contents">{this.props.children}</div>;
    const stale = isChunkLoadError(error);
    return (
      <div role="alert" className="rounded-xl border border-down/40 bg-down/[0.06] p-4 text-sm text-fg">
        <div className="flex items-center gap-2 font-semibold text-down">
          <TriangleAlert size={16} aria-hidden="true" />
          {stale ? "A newer version of the console is available" : `${this.props.title ?? "This page"} could not be shown`}
        </div>
        <p className="mt-1 text-fg-muted">
          {stale
            ? "Part of the page could not be loaded because the console was updated. Reload to get the latest version."
            : "Something went wrong while showing this page. Your data and running strategies are not affected. Try again, or open another page."}
        </p>
        <button onClick={stale ? () => window.location.reload() : this.retry}
                className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-surface-2 px-3 py-1.5 text-xs font-semibold text-fg hover:bg-surface-1">
          <RotateCcw size={13} aria-hidden="true" /> {stale ? "Reload" : "Try again"}
        </button>
      </div>
    );
  }
}
