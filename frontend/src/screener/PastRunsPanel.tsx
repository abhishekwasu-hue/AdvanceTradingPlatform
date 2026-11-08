/**
 * FN2-i (behind `ui_v2`): "Past runs" - the trader's own earlier screener runs, newest first, each with when it ran and
 * what matched; "Open" shows that run's stored results (the board as it was), and, with `watchlists` on, saves its
 * matches as a watchlist. Never another member's runs (the server answers only the caller's own).
 */
import { ArrowLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError } from "../api/errors";
import { Button, SkeletonRows } from "../components/primitives";
import { Details } from "../ui/Details";
import type { WatchlistV2 } from "../chart/watchlistsV2";
import { ResultBoard } from "./ResultBoard";
import { SaveMatchesButton } from "./SaveMatchesButton";
import { isPastRuns, pastRunsApi, runSummary, runTitle, runWhen, type PastRun, type StoredRun } from "./pastRuns";

export interface PastRunsPanelProps {
  api?: Pick<typeof pastRunsApi, "list" | "get">;
  tfWords?: (tf: string) => string;
  /** `watchlists` is on: an opened run offers "Save matches as a watchlist". */
  canSave?: boolean;
  onSaved?: (list: WatchlistV2) => void;
}

const LOAD_FAILED = "Your past runs could not be loaded. Check your connection, then try again.";
const OPEN_FAILED = "This run could not be opened. Check your connection, then try again.";
const GONE = "This run is no longer available.";

export default function PastRunsPanel({ api = pastRunsApi, tfWords, canSave = false, onSaved }: PastRunsPanelProps) {
  const [runs, setRuns] = useState<PastRun[] | null>(null);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [opened, setOpened] = useState<{ row: PastRun; run: StoredRun } | null>(null);
  const [opening, setOpening] = useState<number | null>(null);
  const [openProblem, setOpenProblem] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoadProblem(null);
    api.list().then((r) => { if (alive) { if (isPastRuns(r)) setRuns(r.runs); else setLoadProblem(LOAD_FAILED); } })
      .catch(() => { if (alive) setLoadProblem(LOAD_FAILED); });
    return () => { alive = false; };
  }, [api, attempt]);

  const open = async (row: PastRun) => {
    setOpening(row.run_id);
    setOpenProblem(null);
    try {
      const run = await api.get(row.run_id);
      setOpened({ row, run });
    } catch (e) {
      setOpenProblem(e instanceof ApiError && e.status === 404 ? GONE : OPEN_FAILED);
    } finally {
      setOpening(null);
    }
  };

  if (opened) {
    const { row, run } = opened;
    return (
      <div className="space-y-3 text-t13" data-testid="past-run-opened">
        <Button size="v2" variant="ghost" onClick={() => setOpened(null)} data-testid="past-run-back">
          <ArrowLeft size={16} aria-hidden /> All past runs
        </Button>
        <div>
          <p className="font-semibold text-fg">{runTitle(row, tfWords)}</p>
          <p className="text-t12 text-fg-muted">{runWhen(row) && `Ran ${runWhen(row)} · `}{run.matched} of {run.scanned} matched</p>
        </div>
        {canSave && <SaveMatchesButton results={run.results} scanName={row.screen_name ?? "Scan"} onSaved={onSaved} />}
        <ResultBoard results={run.results} />
        <Details id="screener.pastRuns.about" label="About these results" className="mt-0"><p>{run.disclaimer}</p></Details>
      </div>
    );
  }
  if (loadProblem) {
    return (
      <div className="space-y-2" data-testid="past-runs-problem">
        <p role="alert" className="text-t13 text-warn">{loadProblem}</p>
        <Button size="v2" onClick={() => setAttempt((n) => n + 1)}>Try again</Button>
      </div>
    );
  }
  if (!runs) return <SkeletonRows rows={3} label="Loading your past runs" />;
  if (!runs.length) return <p className="text-t13 text-fg-muted" data-testid="past-runs-empty">Your runs show here after you run a scan.</p>;
  return (
    <div className="space-y-3 text-t13" data-testid="past-runs">
      {openProblem && <p role="alert" className="text-t13 text-warn" data-testid="past-run-problem">{openProblem}</p>}
      <ol className="space-y-2">
        {runs.map((r) => (
          <li key={r.run_id} className="flex flex-wrap items-center justify-between gap-2 rounded-panel border border-border bg-surface-1 p-3"
              data-testid={`past-run-${r.run_id}`}>
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-fg">{runTitle(r, tfWords)}</p>
              <p className="text-t12 text-fg-muted">{runWhen(r) && `Ran ${runWhen(r)} · `}<span className="font-tabular">{runSummary(r)}</span></p>
            </div>
            <Button size="v2" onClick={() => void open(r)} loading={opening === r.run_id} disabled={opening != null}
                    aria-label={`Open the run from ${runWhen(r) || "earlier"}`} data-testid={`past-run-open-${r.run_id}`}>Open</Button>
          </li>
        ))}
      </ol>
      <Details id="screener.pastRuns" className="mt-0"><p>Your own runs, newest first. Opening one shows its results as they were then; nothing runs again.</p></Details>
    </div>
  );
}
