/**
 * FN2-k (behind `ui_v2` and `screener_rankings`): "Rankings" - pick a ranking system, rank the symbols of the current list
 * 0-100 (100 = best of those ranked), and read the table: rank first, then the score, with each symbol's factors one tap
 * away. The method and the "not advice" line sit behind Details. Nothing here places or proposes an order.
 */
import { ChevronDown } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { Button, SkeletonRows, cx } from "../components/primitives";
import { Details } from "../ui/Details";
import { EMPTY, formatNumber } from "../ui/format";
import {
  RANKING_RUN_MAX, allSystems, isRankingRun, isRankingSystems, notRankedList, rankTone, rankingsApi, systemLabel,
  type RankingRun, type RankingSystem,
} from "./rankings";

export interface RankingsPanelProps {
  api?: Pick<typeof rankingsApi, "list" | "run">;
  /** The symbols of the page's current list; the first fifty are ranked. */
  symbols: readonly string[];
}

const LOAD_FAILED = "Ranking systems could not be loaded. Check your connection, then try again.";
const RUN_FAILED = "The ranking could not be run. Check your connection, then try again.";
const TONE: Record<"up" | "down" | "flat", string> = { up: "text-up", down: "text-down", flat: "text-fg" };

export default function RankingsPanel({ api = rankingsApi, symbols }: RankingsPanelProps) {
  const [systems, setSystems] = useState<RankingSystem[] | null>(null);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [picked, setPicked] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runProblem, setRunProblem] = useState<string | null>(null);
  const [run, setRun] = useState<RankingRun | null>(null);
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [disclaimer, setDisclaimer] = useState("");
  const list = symbols.slice(0, RANKING_RUN_MAX);

  useEffect(() => {
    let alive = true;
    setLoadProblem(null);
    api.list().then((r) => {
      if (!alive) return;
      if (isRankingSystems(r)) { const all = allSystems(r); setSystems(all); setPicked((p) => p ?? all[0]?.name ?? null); setDisclaimer(r.disclaimer); }
      else setLoadProblem(LOAD_FAILED);
    }).catch(() => { if (alive) setLoadProblem(LOAD_FAILED); });
    return () => { alive = false; };
  }, [api, attempt]);

  const go = async () => {
    const system = systems?.find((s) => s.name === picked);
    if (!system || !list.length) return;
    setRunning(true);
    setRunProblem(null);
    try {
      const r = await api.run(system, list);
      if (isRankingRun(r)) { setRun(r); setOpenRow(null); } else setRunProblem(RUN_FAILED);
    } catch {
      setRunProblem(RUN_FAILED);
    } finally {
      setRunning(false);
    }
  };

  if (loadProblem) {
    return (
      <div className="space-y-2" data-testid="rankings-problem">
        <p role="alert" className="text-t13 text-warn">{loadProblem}</p>
        <Button size="v2" onClick={() => setAttempt((n) => n + 1)}>Try again</Button>
      </div>
    );
  }
  if (!systems) return <SkeletonRows rows={3} label="Loading ranking systems" />;
  if (!systems.length) return <p className="text-t13 text-fg-muted" data-testid="rankings-empty">No ranking systems are available yet.</p>;
  const current = systems.find((s) => s.name === picked);
  const skipped = run ? notRankedList(run) : [];

  return (
    <div className="space-y-3 text-t13" data-testid="rankings">
      <div role="group" aria-label="Ranking system" className="flex flex-wrap gap-2">
        {systems.map((s) => (
          <button key={s.name} type="button" aria-pressed={s.name === picked} onClick={() => setPicked(s.name)}
                  data-testid={`ranking-system-${systemLabel(s).toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}
                  className={cx("min-h-11 rounded-control border px-space-3 text-t13 font-medium md:min-h-9",
                    s.name === picked ? "border-brand bg-brand/10 text-fg" : "border-border bg-surface-1 text-fg-muted hover:text-fg")}>
            {systemLabel(s)}
          </button>
        ))}
      </div>
      {current?.description && <p className="text-t12 text-fg-muted" data-testid="ranking-system-about">{current.description}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="v2" variant="primary" onClick={() => void go()} loading={running} disabled={!picked || !list.length} data-testid="rankings-run">
          Rank {formatNumber(list.length, 0)} symbols
        </Button>
        {!list.length && <span className="text-t12 text-fg-muted">Pick a list of symbols first.</span>}
      </div>
      {runProblem && <p role="alert" className="text-t13 text-warn" data-testid="rankings-run-problem">{runProblem}</p>}

      {run && (
        <div className="space-y-2" data-testid="rankings-result">
          <p className="text-t12 text-fg-muted">
            <span className="font-semibold text-fg">{systemLabel(run.system)}</span> · {formatNumber(run.ranked.length, 0)} of {formatNumber(run.scanned, 0)} ranked
          </p>
          {run.ranked.length > 0 && (
            <div className="overflow-x-auto rounded-panel border border-border">
              <table className="w-full text-left text-t13">
                <caption className="sr-only">Symbols ranked 0 to 100, best first</caption>
                <thead className="bg-surface-2 text-t12 text-fg-muted">
                  <tr>
                    <th scope="col" className="px-space-3 py-space-2 font-medium">#</th>
                    <th scope="col" className="px-space-3 py-space-2 font-medium">Symbol</th>
                    <th scope="col" className="px-space-3 py-space-2 text-right font-medium">Rank</th>
                    <th scope="col" className="px-space-3 py-space-2 text-right font-medium">Score</th>
                    <th scope="col" className="px-space-3 py-space-2 font-medium"><span className="sr-only">Factors</span></th>
                  </tr>
                </thead>
                <tbody>
                  {run.ranked.map((row, i) => {
                    const open = openRow === row.symbol;
                    return (
                      <Fragment key={row.symbol}>
                        <tr className="border-t border-border" data-testid={`ranking-row-${row.symbol}`}>
                          <td className="px-space-3 py-space-2 font-tabular text-fg-muted">{formatNumber(i + 1, 0)}</td>
                          <th scope="row" className="px-space-3 py-space-2 font-semibold text-fg">{row.symbol}</th>
                          <td className={cx("px-space-3 py-space-2 text-right font-tabular font-semibold", TONE[rankTone(row.rank)])}>
                            {row.rank == null ? EMPTY : formatNumber(row.rank, 0)}
                          </td>
                          <td className="px-space-3 py-space-2 text-right font-tabular text-fg-muted">{row.composite == null ? EMPTY : formatNumber(row.composite, 1)}</td>
                          <td className="px-space-1 py-space-1 text-right">
                            <button type="button" aria-expanded={open} aria-label={`Factors of ${row.symbol}`} onClick={() => setOpenRow(open ? null : row.symbol)}
                                    data-testid={`ranking-why-${row.symbol}`}
                                    className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-control text-fg-muted hover:text-fg md:min-h-9 md:min-w-9">
                              <ChevronDown size={16} aria-hidden className={cx("transition-transform", open && "rotate-180")} />
                            </button>
                          </td>
                        </tr>
                        {open && (
                          <tr className="bg-surface-1" data-testid={`ranking-factors-${row.symbol}`}>
                            <td colSpan={5} className="px-space-3 py-space-2">
                              <ul className="space-y-1">
                                {row.components.map((c) => (
                                  <li key={c.path} className="flex items-baseline justify-between gap-3" style={{ paddingLeft: `${Math.max(0, c.depth - 1) * 12}px` }}>
                                    <span className="min-w-0 text-fg">{c.name}<span className="text-t12 text-fg-muted"> · weight {formatNumber(c.weight, 0)}</span></span>
                                    <span className="shrink-0 font-tabular text-fg-muted">
                                      {c.value !== undefined && <>{c.value == null ? EMPTY : formatNumber(c.value, 2)} · </>}
                                      <span className={TONE[rankTone(c.score)]}>{c.score == null ? EMPTY : formatNumber(c.score, 0)}</span>
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {run.ranked.length === 0 && <p className="text-fg-muted" data-testid="rankings-none">None of these symbols had enough data to rank.</p>}
          {skipped.length > 0 && (
            <p className="text-t12 text-fg-muted" data-testid="rankings-skipped">
              Not ranked: {skipped.map((s) => `${s.symbol} (${s.why})`).join(", ")}
            </p>
          )}
          <Details id="screener.rankings.about" label="How the rank is worked out"><p>{run.method}</p><p>{run.disclaimer}</p></Details>
        </div>
      )}
      {!run && disclaimer && <Details id="screener.rankings.about" label="About rankings"><p>{disclaimer}</p></Details>}
    </div>
  );
}
