import { CalendarDays, Info, TriangleAlert } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { useMarketStatus } from "../chart/useMarketStatus";
import { useChainTrading } from "../fno/ChainTradingV2";
import { readCarry } from "../fno/hubModel";
import SymbolHubTabsV2 from "../fno/SymbolHubTabsV2";
import type { PageProps } from "../routes";
import { formatDateIST, formatTimeIST } from "../ui/format";
import { PageIntro } from "../ui/PageIntro";
import AnalysisSlotV2 from "./AnalysisSlotV2";
import ChainTableV2 from "./ChainTableV2";
import ColumnsMenuV2 from "./ColumnsMenuV2";
import {
  INDEX_SYMBOLS, STOCK_SYMBOLS, TEXT, chainNotes, initialSymbol, readColumns, writeColumns, type ColumnPrefs,
} from "./model";
import RowDetailsV2 from "./RowDetailsV2";
import SummaryStripV2 from "./SummaryStripV2";
import type { ChainRow } from "./types";
import { useOptionChain, type OptionChainData } from "./useOptionChain";

/**
 * OC1 (owner: "After login everything should happen automatically"): the Option chain page in the v2 shell.
 * - The only inputs: the symbol (NIFTY first, then BANKNIFTY, FINNIFTY, SENSEX and the F&O stocks) and the expiry,
 *   which is chosen for the trader (the nearest; on an expiry day the next one). The spot, the future and the chain
 *   come from the trader's own broker session and refresh on their own while the page is shown.
 * - The summary strip, the plain-word states (market closed, OI change still building) and the chain with the ATM row
 *   in the middle; a row opens its full details.
 * - No sample data and no developer controls: without a broker session the shell's broker gate shows "Connect your
 *   broker" instead of this page (src/ui/dataPages.ts), and a chain that cannot be read says so in plain words.
 * - Below the chain: the mount point of the Analysis tabs (OC2).
 * - FNO-1: with the PAPER account on, a call or put cell opens its actions and the PAPER order ticket, held contracts
 *   show "P", and a bottom strip holds the key figures (src/fno/ChainTradingV2.tsx). The page itself places nothing.
 * - FNO-3: the symbol hub's tab row on top (Chart | Option chain | Futures | Overview, src/fno/SymbolHubTabsV2.tsx). The
 *   address keeps the symbol and the expiry (`?underlying=&expiry=`, and the chart's `tf`), so the tabs carry them.
 * Shown only while `ui_v2` is on (src/shell/pagesV2.ts).
 */
const SELECT = "h-11 w-full rounded-control border border-border bg-surface-2 px-space-3 text-t14 font-semibold text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9";

function Note({ tone, children }: { tone: "neutral" | "warn"; children: string }) {
  const Icon = tone === "warn" ? TriangleAlert : Info;
  return (
    <p role="status" className={`flex items-start gap-space-2 rounded-control px-space-3 py-space-2 text-t13 ${tone === "warn" ? "bg-warn-tint text-warn" : "bg-surface-2 text-fg"}`}>
      <Icon size={16} aria-hidden className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

export default function OptionChainPageV2(_props: PageProps) {
  const [params, setParams] = useSearchParams();
  const [symbol, setSymbol] = useState(() => initialSymbol(typeof window === "undefined" ? "" : window.location.search));
  const [carried] = useState(() => readCarry(params));
  const market = useMarketStatus();
  const marketOpen = market.status?.sessions?.NSE?.is_open ?? null;
  const chain = useOptionChain(symbol, marketOpen, carried.expiry);
  const [prefs, setPrefs] = useState<ColumnPrefs>(() => readColumns());
  // FNO-3: the address follows the symbol and the expiry on screen, so the hub's tabs (and a reload) keep them.
  const expiry = chain.expiry;
  useEffect(() => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("underlying", symbol);
      if (expiry) next.set("expiry", expiry); else next.delete("expiry");
      return next.toString() === prev.toString() ? prev : next;
    }, { replace: true });
  }, [symbol, expiry, setParams]);
  return (
    <>
      <SymbolHubTabsV2 symbol={symbol} current="option-chain" carry={{ expiry, timeframe: carried.timeframe }} />
      <OptionChainView symbol={symbol} onSymbol={setSymbol} chain={chain} marketOpen={marketOpen} prefs={prefs}
        onPrefs={(next) => { setPrefs(next); writeColumns(next); }} />
    </>
  );
}

/** The page's look for given data (no reads of its own), so tests render every state directly. */
export function OptionChainView({ symbol, onSymbol, chain, marketOpen, prefs, onPrefs }: {
  symbol: string;
  onSymbol: (symbol: string) => void;
  chain: OptionChainData;
  /** The NSE session from the system status (null while not known); the chain's own `market_open` wins once it answers. */
  marketOpen: boolean | null;
  prefs: ColumnPrefs;
  onPrefs: (prefs: ColumnPrefs) => void;
}) {
  const [picked, setPicked] = useState<number | null>(null);
  const symbolId = useId();
  const expiryId = useId();

  const live = chain.live;
  const notes = chainNotes(live, marketOpen, (iso) => formatTimeIST(iso));
  const extraSymbol = useMemo(() => (new Set<string>([...INDEX_SYMBOLS, ...STOCK_SYMBOLS]).has(symbol) ? null : symbol), [symbol]);
  const expiryOptions = chain.expiries?.expiries.length
    ? chain.expiries.expiries
    : chain.expiry ? [{ date: chain.expiry, kind: "weekly" as const }] : [];
  const pickedRow: ChainRow | null = live?.rows.find((r) => r.strike === picked) ?? null;
  const trading = useChainTrading({ symbol, live, prefs, onPrefs });

  return (
    <div className="space-y-space-4" data-page="option-chain-v2">
      <PageIntro id="option-chain" lines={[
        "The option chain of the index or stock you pick, live from your broker.",
        "The strike nearest the price is in the middle. Tap a row to see all its figures.",
      ]} />
      <header className="flex flex-col gap-space-3 md:flex-row md:items-end md:justify-between">
        <h1 className="sr-only md:not-sr-only md:text-t24 md:font-semibold md:text-fg">Option chain</h1>
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-end gap-space-2 md:flex md:gap-space-3">
          <div className="min-w-0 md:w-48">
            <label htmlFor={symbolId} className="mb-space-1 block text-t12 font-medium text-fg-muted">Symbol</label>
            <select id={symbolId} value={symbol} onChange={(e) => { setPicked(null); onSymbol(e.target.value); }} className={SELECT}>
              {extraSymbol && <option value={extraSymbol}>{extraSymbol}</option>}
              <optgroup label="Indices">
                {INDEX_SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
              </optgroup>
              <optgroup label="Stocks">
                {STOCK_SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
              </optgroup>
            </select>
          </div>
          <div className="min-w-0 md:w-56">
            <label htmlFor={expiryId} className="mb-space-1 flex items-center gap-space-1 text-t12 font-medium text-fg-muted">
              <CalendarDays size={12} aria-hidden /> Expiry
            </label>
            <select id={expiryId} value={chain.expiry ?? ""} disabled={expiryOptions.length === 0}
              onChange={(e) => { setPicked(null); chain.setExpiry(e.target.value); }} className={SELECT}>
              {expiryOptions.length === 0 && <option value="">{chain.loading ? "Loading…" : "Nearest"}</option>}
              {expiryOptions.map((e) => (
                <option key={e.date} value={e.date}>{formatDateIST(e.date)}{e.kind === "monthly" ? " · Monthly" : ""}</option>
              ))}
            </select>
          </div>
          <ColumnsMenuV2 prefs={prefs} onChange={onPrefs} />
        </div>
      </header>

      {notes.length > 0 && (
        <div className="space-y-space-2" data-chain-notes>
          {notes.map((n) => <Note key={n.key} tone="neutral">{n.text}</Note>)}
        </div>
      )}

      <SummaryStripV2 summary={live?.summary ?? null} expiry={live?.expiry ?? chain.expiry} loading={chain.loading && !live} />

      <section aria-label="Chain" data-block="chain">
        {chain.problem && !live && <Note tone="warn">{chain.problem === "no-session" ? TEXT.noSession : chain.problem === "plan" ? TEXT.plan : TEXT.loadFailed}</Note>}
        {chain.loading && !live && !chain.problem && (
          <div aria-busy="true" className="space-y-space-2 rounded-panel border border-subtle bg-surface-1 p-space-4">
            <span className="sr-only">Loading the option chain</span>
            {Array.from({ length: 6 }, (_, i) => <div key={i} className="h-6 animate-pulse rounded-control bg-surface-2" />)}
          </div>
        )}
        {live && live.rows.length === 0 && <Note tone="neutral">{TEXT.empty}</Note>}
        {live && live.rows.length > 0 && (
          <>
            <div aria-hidden className="mb-space-2 flex justify-between text-t12 font-semibold uppercase text-fg-muted">
              <span>Calls</span><span>Puts</span>
            </div>
            <ChainTableV2 live={live} prefs={prefs} centreKey={`${symbol}|${live.expiry ?? ""}${trading.centreSuffix}`} onPick={(row) => setPicked(row.strike)}
              onPickLeg={trading.onPickLeg} held={trading.held} />
            {chain.problem && <p className="mt-space-2 text-t12 text-fg-muted">{TEXT.loadFailed}</p>}
          </>
        )}
        {trading.strip}
      </section>
      {trading.layer}

      {/* OC2: the Analysis tabs go here (AnalysisSlotV2 is the placeholder a follow-up wires to AnalysisTabsV2). */}
      <AnalysisSlotV2 symbol={symbol} expiry={live?.expiry ?? chain.expiry} refreshKey={live?.fetched_at ?? null} />

      <RowDetailsV2 row={pickedRow} atm={live?.summary.atm_strike ?? null} underlying={symbol} onClose={() => setPicked(null)} />
    </div>
  );
}
