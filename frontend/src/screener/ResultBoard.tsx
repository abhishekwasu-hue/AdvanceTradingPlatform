/**
 * U5: the result board - D1 has the table view (dense, sortable). Matched symbols first; a symbol the scan could not
 * read says why. Heatmap, chart grid, RRG and the F&O dashboard join as more views; why-matched chips per stage come
 * with the live survivor counts.
 */
import { ArrowDown, ArrowUp } from "lucide-react";
import { useMemo, useState } from "react";
import { cx } from "../components/primitives";
import type { RunResult } from "./api";

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

export interface ResultBoardProps {
  results: RunResult[] | null;
  running?: boolean;
  runKey?: number;
}

export function ResultBoard({ results, running = false, runKey }: ResultBoardProps) {
  const [sort, setSort] = useState<SortState>({ key: "result", dir: "asc" });
  const rows = useMemo(() => (results ? sortResults(results, sort) : []), [results, sort]);
  const toggle = (key: SortKey) => setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  const matched = results?.filter((r) => r.matched).length ?? 0;

  return (
    <section aria-label="Results" className="flex min-h-0 flex-col rounded-panel border border-border bg-surface-1">
      <header className="flex items-baseline justify-between gap-2 border-b border-border px-4 py-3">
        <h2 className="text-t15 font-semibold text-fg">Results</h2>
        <span className="text-t12 text-fg-muted">Table</span>
      </header>
      {results == null ? (
        <p className="px-4 py-8 text-center text-t13 text-fg-muted">{running ? "Running the scan…" : "Run the scan to see which symbols pass."}</p>
      ) : results.length === 0 ? (
        <p className="px-4 py-8 text-center text-t13 text-fg-muted">No symbols were read.</p>
      ) : (
        <div className="min-h-0 overflow-auto" key={runKey}>
          <table className="w-full border-collapse text-t13">
            <caption className="sr-only">{matched} of {results.length} symbols matched</caption>
            <thead className="sticky top-0 bg-surface-1 text-t12 text-fg-muted">
              <tr className="h-row border-b border-border">
                <SortHeader label="Symbol" active={sort.key === "symbol"} dir={sort.dir} onClick={() => toggle("symbol")} />
                <SortHeader label="Result" active={sort.key === "result"} dir={sort.dir} onClick={() => toggle("result")} />
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.symbol} className="board-settle h-row border-b border-border/60 last:border-b-0 hover:bg-surface-2"
                    style={{ ["--board-delay" as string]: `${Math.min(i, 12) * 15}ms` }}>
                  <td className="px-4 font-mono font-tabular text-fg">{r.symbol}</td>
                  <td className="px-4">
                    {r.matched
                      ? <span className="inline-flex items-center gap-1.5 text-signal"><span aria-hidden className="h-1.5 w-1.5 rounded-full bg-signal" />Matched</span>
                      : r.reason
                        ? <span className="text-warn" title={r.reason}>Could not be read · <span className="text-fg-muted">{r.reason}</span></span>
                        : <span className="text-fg-muted">Did not match</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SortHeader({ label, active, dir, onClick }: { label: string; active: boolean; dir: "asc" | "desc"; onClick: () => void }) {
  return (
    <th scope="col" aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"} className="px-4 text-left font-medium">
      <button type="button" onClick={onClick}
              className={cx("inline-flex items-center gap-1 rounded-control hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand", active && "text-fg")}>
        {label}
        {active && (dir === "asc" ? <ArrowUp size={12} aria-hidden /> : <ArrowDown size={12} aria-hidden />)}
      </button>
    </th>
  );
}
