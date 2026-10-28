/**
 * S10 J (flag `screener_etf`): NSE ETFs from the shared end-of-day history - last close, average traded value, the
 * change over a window and the change of the index each tracks, and the difference between the two. Filters and
 * sorts; facts only, never a ranking of which ETF to hold.
 */
import { useEffect, useState } from "react";
import { Button } from "../components/primitives";
import { Combobox } from "../ui/Combobox";
import { screenerApi } from "./api";
import { ETF_SORTS, etfQuery, inrShort, pct, SORT_LABEL, type EtfResponse, type EtfSort } from "./etf";

export interface EtfPanelProps {
  initial?: EtfResponse | null;
  load?: (query: string) => Promise<EtfResponse>;
}

export function EtfPanel({ initial = null, load = screenerApi.etf }: EtfPanelProps) {
  const [sessions, setSessions] = useState(20);
  const [q, setQ] = useState("");
  const [minCr, setMinCr] = useState(1);
  const [maxTracking, setMaxTracking] = useState<string>("");
  const [sort, setSort] = useState<EtfSort>("traded_value");
  const [data, setData] = useState<EtfResponse | null>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetchRows = () => {
    setBusy(true);
    setError(null);
    load(etfQuery({ sessions, q, minCr, maxTracking: maxTracking.trim() ? Number(maxTracking) : null, sort }))
      .then(setData).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e))).finally(() => setBusy(false));
  };
  useEffect(() => { if (!initial) fetchRows(); }, []);                                   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div data-testid="etf-panel">
      <div className="flex flex-wrap items-end gap-2 text-t12 text-fg-muted">
        <label className="flex flex-col">Search<input value={q} onChange={(e) => setQ(e.target.value)} placeholder="name or index"
          className="min-h-[44px] rounded-control border border-border bg-surface-1 px-2 text-t13 text-fg" /></label>
        <label className="flex flex-col">Window (sessions)<input type="number" min={1} max={250} value={sessions} onChange={(e) => setSessions(Number(e.target.value) || 20)}
          className="min-h-[44px] w-24 rounded-control border border-border bg-surface-1 px-2 font-mono text-t13 text-fg" /></label>
        <label className="flex flex-col">Min traded value (Cr / day)<input type="number" min={0} step={0.5} value={minCr} onChange={(e) => setMinCr(Number(e.target.value) || 0)}
          className="min-h-[44px] w-28 rounded-control border border-border bg-surface-1 px-2 font-mono text-t13 text-fg" /></label>
        <label className="flex flex-col">Max |tracking diff| %<input value={maxTracking} onChange={(e) => setMaxTracking(e.target.value)} placeholder="any"
          className="min-h-[44px] w-24 rounded-control border border-border bg-surface-1 px-2 font-mono text-t13 text-fg" /></label>
        {/* SCR-FIX D8: the shared picker, not the browser's select */}
        <div className="flex flex-col"><span aria-hidden>Sort</span>
          <Combobox label="Sort" value={sort} onChange={(v) => setSort(v as EtfSort)}
                    options={ETF_SORTS.map((s) => ({ value: s, label: SORT_LABEL[s] }))} /></div>
        <Button size="sm" variant="primary" onClick={fetchRows} loading={busy}>Show ETFs</Button>
      </div>
      {error && <p role="alert" className="mt-2 text-t12 text-warn">{error}</p>}
      {data && (
        <div className="mt-3 overflow-x-auto" aria-live="polite">
          <p className="text-t12 text-fg-muted">{data.shown} of {data.total} ETFs · {data.source}</p>
          <table className="mt-1 w-full border-collapse text-t13" data-testid="etf-table">
            <caption className="sr-only">NSE ETFs: close, traded value, change over {data.sessions} sessions, the tracked index's change and the difference</caption>
            <thead><tr className="text-left text-t12 text-fg-muted">
              <th scope="col" className="py-1 pr-2 font-medium">ETF</th><th scope="col" className="py-1 pr-2 font-medium">Tracks</th>
              <th scope="col" className="py-1 pr-2 font-medium">Close</th><th scope="col" className="py-1 pr-2 font-medium">Traded / day</th>
              <th scope="col" className="py-1 pr-2 font-medium">{data.sessions}-session change</th><th scope="col" className="py-1 pr-2 font-medium">Index change</th>
              <th scope="col" className="py-1 font-medium">Tracking diff</th>
            </tr></thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.symbol} className="border-t border-border align-top" data-symbol={r.symbol}>
                  <th scope="row" className="py-1 pr-2 text-left"><span className="font-mono font-semibold text-fg">{r.symbol}</span>
                    <div className="text-t12 font-normal text-fg-muted">{r.name}</div></th>
                  <td className="py-1 pr-2 text-fg-muted">{r.underlying ?? "–"}</td>
                  <td className="py-1 pr-2 font-mono text-fg">{r.close?.toFixed(2) ?? "–"}<div className="text-t12 text-fg-muted">{r.session ?? ""}</div></td>
                  <td className="py-1 pr-2 font-mono text-fg">{inrShort(r.traded_value)}</td>
                  <td className="py-1 pr-2 font-mono text-fg">{pct(r.change_pct)}</td>
                  <td className="py-1 pr-2 font-mono text-fg">{pct(r.index_change_pct)}</td>
                  <td className="py-1 font-mono text-fg" title={r.notes.join("; ") || undefined}>{pct(r.tracking_diff_pct)}
                    {r.notes.length > 0 && <div className="text-t12 font-sans text-fg-muted">{r.notes[0]}</div>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-t12 text-fg-muted">{data.note}</p>
        </div>
      )}
    </div>
  );
}
