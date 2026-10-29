/**
 * U5: the result board - the table view (dense, sortable). Matched symbols first; a symbol the scan could not read
 * says why. D2: a "Stages" column of why-matched chips - one per top-level stage, filled when the symbol passed that
 * stage on its own, hollow when it did not - with the stage's words on hover and for screen readers. Heatmap, chart
 * grid, RRG and the F&O dashboard join as more views (D3). U8 EV1: a matched row with stored evidence has "Show on chart"
 * (the evidence chart with only the screen's own conditions and the verify strip).
 */
import { ArrowDown, ArrowUp, ChartCandlestick, Download } from "lucide-react";
import { useMemo, useState } from "react";
import { cx } from "../components/primitives";
import type { RunResult } from "./api";

export interface WhyChip { id: string; summary: string; passed: boolean }

export type SortKey = "symbol" | "result";
export interface SortState { key: SortKey; dir: "asc" | "desc" }

/** Matched first (then not matched, then could-not-read), each group by symbol; or by symbol alone. Pure, for tests. */
export function sortResults(rows: RunResult[], sort: SortState): RunResult[] {
  const rank = (r: RunResult) => (r.matched ? 0 : r.reason ? 2 : 1);
  const out = rows.slice().sort((a, b) => {
    const bySymbol = a.symbol.localeCompare(b.symbol);
    if (sort.key === "symbol") return bySymbol;
    return rank(a) - rank(b) || bySymbol;
  });
  return sort.dir === "asc" ? out : out.reverse();
}

/** UI-28 U5.3: the board as CSV (symbol, result, reason, then one column per stage when known), in the board's order. */
export function resultsCsv(rows: RunResult[], why?: (symbol: string) => WhyChip[]): string {
  const cell = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const stages = why && rows.length ? why(rows[0].symbol).map((c, i) => `stage_${i + 1}`) : [];
  const head = ["symbol", "result", "reason", ...stages].join(",");
  const body = rows.map((r) => [r.symbol, r.matched ? "matched" : r.reason ? "not read" : "not matched", r.reason ?? "",
    ...(why ? why(r.symbol).map((c) => (c.passed ? "passed" : "not passed")) : [])].map(cell).join(","));
  return [head, ...body].join("\n") + "\n";
}

function downloadCsv(text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "scan-results.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export interface ResultBoardProps {
  results: RunResult[] | null;
  running?: boolean;
  runKey?: number;
  stale?: boolean;           // the scan changed since this run: the rows are kept, dimmed, with a note to run again
  why?: (symbol: string) => WhyChip[];
  onShowEvidence?: (row: RunResult) => void;   // U8 EV1: only rows with an evidence id get the button
  /** UI-28 U5.3: the "Auto-refresh 1m" switch (the page re-runs the scan every minute while it is on and shown). */
  autoRefresh?: { on: boolean; onChange: (on: boolean) => void };
}

export function ResultBoard({ results, running = false, runKey, stale = false, why: whyIn, onShowEvidence, autoRefresh }: ResultBoardProps) {
  const [sort, setSort] = useState<SortState>({ key: "result", dir: "asc" });
  const [showStages, setShowStages] = useState(true);   // U5.3 column picker: the one optional column
  const why = showStages ? whyIn : undefined;
  const rows = useMemo(() => (results ? sortResults(results, sort) : []), [results, sort]);
  const toggle = (key: SortKey) => setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  const matched = results?.filter((r) => r.matched).length ?? 0;

  return (
    <section aria-label="Results" className="flex min-h-0 flex-col rounded-panel border border-border bg-surface-1">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h2 className="text-t15 font-semibold text-fg">Results</h2>
        <span className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1 text-t12 text-fg-muted">
          {autoRefresh && (
            <label className="inline-flex min-h-11 items-center gap-1.5 md:min-h-0" data-testid="results-auto-refresh">
              <input type="checkbox" checked={autoRefresh.on} onChange={(e) => autoRefresh.onChange(e.target.checked)} className="accent-brand" />
              Auto-refresh 1m
            </label>
          )}
          {whyIn && (
            <label className="inline-flex min-h-11 items-center gap-1.5 md:min-h-0" data-testid="results-stages-column">
              <input type="checkbox" checked={showStages} onChange={(e) => setShowStages(e.target.checked)} className="accent-brand" />
              Stages column
            </label>
          )}
          {results != null && results.length > 0 && (
            <button type="button" onClick={() => downloadCsv(resultsCsv(rows, why))} data-testid="results-csv"
                    className="inline-flex min-h-11 items-center gap-1 rounded-control px-1.5 text-brand hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-0">
              <Download size={12} aria-hidden />Export (CSV)
            </button>
          )}
        </span>
      </header>
      {stale && results != null && (
        <p role="status" data-testid="results-stale" className="border-b border-border bg-surface-inset px-4 py-2 text-t12 text-fg-muted">
          The scan changed since this run. Run it again to update these results.
        </p>
      )}
      {results == null ? (
        <p className="px-4 py-8 text-center text-t13 text-fg-muted">{running ? "Running the scan…" : "Run the scan to see which symbols pass."}</p>
      ) : results.length === 0 ? (
        <p className="px-4 py-8 text-center text-t13 text-fg-muted">No symbols were read.</p>
      ) : (
        <div className={cx("min-h-0 overflow-auto transition-opacity", stale && "opacity-50")} key={runKey} aria-hidden={stale || undefined}>
          {/* SCR-FIX D5: fixed columns - the Symbol stays on one line and in view (sticky), the Result wraps; on a phone the
              stage dots stack under the result in the same row instead of a third column */}
          <table className="w-full table-fixed border-collapse text-t13">
            <caption className="sr-only">{matched} of {results.length} symbols matched</caption>
            <thead className="sticky top-0 z-10 bg-surface-1 text-t12 text-fg-muted">
              <tr className="h-row border-b border-border">
                <SortHeader label="Symbol" active={sort.key === "symbol"} dir={sort.dir} onClick={() => toggle("symbol")} className="sticky left-0 w-32 bg-surface-1" />
                <SortHeader label="Result" active={sort.key === "result"} dir={sort.dir} onClick={() => toggle("result")} />
                {why && <th scope="col" className="hidden w-24 px-4 text-left font-medium sm:table-cell">Stages</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.symbol} className="board-settle group/row h-row border-b border-border/60 last:border-b-0 hover:bg-surface-2"
                    style={{ ["--board-delay" as string]: `${Math.min(i, 12) * 15}ms` }}>
                  <td className="sticky left-0 whitespace-nowrap bg-surface-1 px-4 py-1.5 align-top font-mono font-tabular text-fg group-hover/row:bg-surface-2">
                    <span className="block truncate" title={r.symbol}>{r.symbol}</span>
                  </td>
                  <td className="break-words px-4 py-1.5 align-top">
                    {r.matched
                      ? <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
                          <span className="inline-flex items-center gap-1.5 text-signal"><span aria-hidden className="h-1.5 w-1.5 rounded-full bg-signal" />Matched</span>
                          {onShowEvidence && r.evidence_id != null && (
                            <button type="button" onClick={() => onShowEvidence(r)} aria-label={`Show ${r.symbol} on chart`} data-testid="show-on-chart"
                                    className="inline-flex items-center gap-1 rounded-control px-1.5 py-0.5 text-t12 text-brand hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                              <ChartCandlestick size={12} aria-hidden />Show on chart
                            </button>
                          )}
                        </span>
                      : r.reason
                        ? <span className="text-warn" title={r.reason}>Could not be read · <span className="text-fg-muted">{r.reason}</span></span>
                        : <span className="text-fg-muted">Did not match</span>}
                    {why && <span className="mt-1 block sm:hidden"><WhyChips chips={why(r.symbol)} /></span>}
                  </td>
                  {why && <td className="hidden px-4 py-1.5 align-top sm:table-cell"><WhyChips chips={why(r.symbol)} /></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SortHeader({ label, active, dir, onClick, className }: { label: string; active: boolean; dir: "asc" | "desc"; onClick: () => void; className?: string }) {
  return (
    <th scope="col" aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"} className={cx("px-4 text-left font-medium", className)}>
      <button type="button" onClick={onClick}
              className={cx("inline-flex items-center gap-1 rounded-control hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand", active && "text-fg")}>
        {label}
        {active && (dir === "asc" ? <ArrowUp size={12} aria-hidden /> : <ArrowDown size={12} aria-hidden />)}
      </button>
    </th>
  );
}

/** One dot per top-level stage: filled = passed on its own, hollow = did not. Words on hover and for screen readers. */
export function WhyChips({ chips }: { chips: WhyChip[] }) {
  if (!chips.length) return <span className="text-t12 text-fg-muted"><span aria-hidden>–</span><span className="sr-only">not evaluated</span></span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-1" data-testid="why-chips">
      {chips.map((c, i) => (
        <span key={c.id} title={`Stage ${i + 1}: ${c.summary} - ${c.passed ? "passed" : "did not pass"}`}
              className={cx("h-2.5 w-2.5 rounded-full border", c.passed ? "border-signal bg-signal" : "border-fg-muted/60 bg-transparent")}>
          <span className="sr-only">{`Stage ${i + 1}, ${c.summary}: ${c.passed ? "passed" : "did not pass"}.`}</span>
        </span>
      ))}
    </span>
  );
}
