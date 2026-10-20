import { ExternalLink } from "lucide-react";
import { Suspense, lazy, useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router";
import { PageIntro } from "../ui/PageIntro";
import { chartsPath } from "./shellRoutes";

const ChartWindow = lazy(() => import("../pages/ChartWindow"));

/** The last symbol viewed on Charts (per browser): Charts opens on it, NIFTY 50 on first use (§6 default state). */
export const LAST_SYMBOL_KEY = "atp_charts_last_symbol";
export const DEFAULT_SYMBOL = "NIFTY 50";

function readLast(): string | null {
  try { return localStorage.getItem(LAST_SYMBOL_KEY); } catch { return null; }
}

/** The symbol Charts shows: the one in the address, else the last one viewed, else NIFTY 50. */
export function chartsSymbol(fromPath: string | null | undefined, last: string | null): string {
  return (fromPath ?? last ?? DEFAULT_SYMBOL).trim().toUpperCase() || DEFAULT_SYMBOL;
}

/**
 * UI2-1 (UI v2 proposal §2 row 2, §3 M8): the Charts page at `/charts` and `/charts/:symbol`, inside the app. For now it
 * wraps today's chart (the `?chart=` window: candles from the trader's broker, live price, scroll-back history); UI2-4
 * rebuilds it to the §6 spec. It sits behind the broker gate like every data page (the shell puts it there), so it
 * never draws sample candles. "Open in new tab" keeps the standalone window (§2 "Standalone and special routes").
 */
export default function ChartsPageV2() {
  // The router's own reading of `/charts/:symbol` (already decoded), never a second decode of the raw address.
  const { symbol: fromPath } = useParams<{ symbol?: string }>();
  const navigate = useNavigate();
  const symbol = chartsSymbol(fromPath?.trim() ? fromPath : null, readLast());
  const [draft, setDraft] = useState(symbol);
  const fieldId = useId();
  useEffect(() => { setDraft(symbol); }, [symbol]);
  useEffect(() => { try { localStorage.setItem(LAST_SYMBOL_KEY, symbol); } catch { /* storage unavailable */ } }, [symbol]);
  const params = useMemo(() => new URLSearchParams({ chart: symbol }), [symbol]);
  // TODO(UI2-4): the standalone chart windows (?chart=, ?workspace=, ?contract=, ?basis= in App.tsx) render outside
  // the shell and so outside the broker gate; UI2-4 moves the `?chart=` window onto the gated v2 chart component.
  const newTab = `/?${new URLSearchParams({ chart: symbol, tf: "5min" }).toString()}`;

  const open = (e: FormEvent) => {
    e.preventDefault();
    const next = draft.trim().toUpperCase();
    if (next && next !== symbol) navigate(chartsPath(next));
  };

  return (
    <div className="space-y-space-3">
      <PageIntro id="charts" lines={[
        "See what a symbol is doing: candles, volume and the live price from your broker.",
        "Type a symbol and choose Open to switch. Scroll the chart left for older history.",
      ]} />
      <div className="flex flex-wrap items-end gap-space-2">
        <form onSubmit={open} className="flex min-w-0 flex-1 items-end gap-space-2 sm:flex-none">
          <div className="min-w-0 flex-1 sm:w-64 sm:flex-none">
            <label htmlFor={fieldId} className="mb-space-1 block text-t12 font-medium text-fg-muted">Symbol</label>
            <input id={fieldId} value={draft} onChange={(e) => setDraft(e.target.value)} autoComplete="off" autoCapitalize="characters"
              spellCheck={false}
              className="h-11 w-full rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9" />
          </div>
          <button type="submit"
            className="h-11 shrink-0 rounded-control border border-border bg-surface-2 px-space-4 text-t14 font-semibold text-fg hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9">
            Open
          </button>
        </form>
        <a href={newTab} target="_blank" rel="noopener noreferrer"
          className="inline-flex h-11 items-center gap-space-1 rounded-control px-space-2 text-t14 font-medium text-brand hover:underline md:h-9">
          Open in new tab <ExternalLink size={14} aria-hidden />
        </a>
      </div>
      <Suspense fallback={<div className="h-64 animate-pulse rounded-panel bg-surface-2" aria-busy="true" />}>
        {/* Keyed by symbol: a new symbol starts from its own default timeframe and history. */}
        <ChartWindow key={symbol} params={params} />
      </Suspense>
    </div>
  );
}
