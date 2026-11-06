/**
 * FN2-g (behind `ui_v2` and `watchlists`): "Save matches as a watchlist" - the matched symbols of a result become a new
 * list of the trader's own, named in a small form (the scan's name and "matches" to start). A list of names only.
 */
import { ListPlus } from "lucide-react";
import { useId, useState } from "react";
import { Button, cx, fieldClass } from "../components/primitives";
import { useToast } from "../components/Toast";
import { watchlistError, watchlistsV2Api, type WatchItem, type WatchlistV2 } from "../chart/watchlistsV2";
import type { RunResult } from "./api";
import { defaultListName, matchedSymbols, nameProblem, watchItems } from "./pastRuns";

export interface SaveMatchesProps {
  results: readonly RunResult[] | null | undefined;
  scanName: string;
  /** The create call (the watchlists API; a test passes its own). */
  create?: (name: string, items: WatchItem[]) => Promise<WatchlistV2>;
  onSaved?: (list: WatchlistV2) => void;
  className?: string;
}

export function SaveMatchesButton({ results, scanName, create = watchlistsV2Api.create, onSaved, className }: SaveMatchesProps) {
  const toast = useToast();
  const symbols = matchedSymbols(results);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const inputId = useId();
  if (!symbols.length) return null;

  const start = () => { setName(defaultListName(scanName)); setProblem(null); setOpen(true); };
  const save = async () => {
    const bad = nameProblem(name);
    if (bad) { setProblem(bad); return; }
    const clean = name.trim();
    setBusy(true);
    setProblem(null);
    try {
      const list = await create(clean, watchItems(symbols));
      toast.success(`Saved "${list.name}" with ${symbols.length} ${symbols.length === 1 ? "symbol" : "symbols"}.`);
      setOpen(false);
      onSaved?.(list);
    } catch (e) {
      setProblem(watchlistError(e, "create", clean));
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <Button size="v2" className={cx("w-full sm:w-auto", className)} onClick={start} data-testid="save-matches-open">
        <ListPlus size={16} aria-hidden /> Save matches as a watchlist
      </Button>
    );
  }
  return (
    <form className={cx("space-y-2 rounded-panel border border-border bg-surface-1 p-3", className)} data-testid="save-matches-form"
          onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <label htmlFor={inputId} className="block text-t13 font-medium text-fg">
        New watchlist with {symbols.length} matched {symbols.length === 1 ? "symbol" : "symbols"}
      </label>
      <input id={inputId} value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoFocus
             aria-invalid={problem ? true : undefined} data-testid="save-matches-name" className={cx(fieldClass, "h-11 md:h-9")} />
      {problem && <p role="alert" className="text-t13 text-warn" data-testid="save-matches-problem">{problem}</p>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="v2" variant="primary" loading={busy} data-testid="save-matches-save">Save</Button>
        <Button size="v2" variant="ghost" onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
      </div>
    </form>
  );
}
