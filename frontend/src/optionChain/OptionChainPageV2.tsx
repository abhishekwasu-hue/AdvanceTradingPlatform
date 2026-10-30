import { Activity, CalendarDays, ChartColumn, Info, Sparkles, Table2, TriangleAlert } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { useColourV2 } from "../ui/colourV2";
import { useMarketStatus } from "../chart/useMarketStatus";
import { useChainTrading } from "../fno/ChainTradingV2";
import { readCarry } from "../fno/hubModel";
import { SetupPanelV2, useSetupStrikes } from "../fno/InsightsV2";
import { SIGNAL_PARAM, TRADE_PARAM, setupMarks, setupTrade, type SetupItem } from "../fno/insights";
import SymbolHubTabsV2 from "../fno/SymbolHubTabsV2";
import type { PageProps } from "../routes";
import { cx } from "../components/primitives/cx";
import { formatTimeIST } from "../ui/format";
import { useTerminalSpot } from "../terminal/spot";
import { PageIntro } from "../ui/PageIntro";
import { useFeatures } from "../ui/useFeatures";
import AnalysisSlotV2 from "./AnalysisSlotV2";
import ChainTableV2 from "./ChainTableV2";
import ChainCharts from "./ChartsViewV2";
import OiViewV2 from "./OiViewV2";
import ColumnsMenuV2 from "./ColumnsMenuV2";
import {
  INDEX_SYMBOLS, STOCK_SYMBOLS, TEXT, chainNotes, initialSymbol, readColumns, writeColumns, type ColumnPrefs,
} from "./model";
import RowDetailsV2 from "./RowDetailsV2";
import SummaryStripV2 from "./SummaryStripV2";
import { STRIKE_RANGES, rowsAround, usePrevClose } from "./spotChange";
import type { ChainRow } from "./types";
import { useOptionChain, type OptionChainData } from "./useOptionChain";
import { Combobox } from "../ui/Combobox";
import { Sheet } from "../components/primitives/Dialog";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import { AskAi } from "./AskAiV2";
import { Skeleton } from "../components/primitives";

/**
 * OC1 (owner: "After login everything should happen automatically"): the Option chain page in the v2 shell.
 * - The only inputs: the symbol (NIFTY first, then BANKNIFTY, FINNIFTY, SENSEX and the F&O stocks) and the expiry,
 *   which is chosen for the trader (the nearest; on an expiry day the next one). The spot, the future and the chain
 *   come from the trader's own broker session and refresh on their own while the page is shown.
 * - The summary strip, the plain-word states (market closed, OI change still building) and the chain with the ATM row
 *   in the middle; a row opens its full details.
 * - No sample data and no developer controls: without a broker session the shell's broker gate shows "Connect your
 *   broker" instead of this page (src/ui/dataPages.ts), and a chain that cannot be read says so in plain words.
 * - "Chain | Charts" (OC5): the Charts view shows the chain's OI, PCR, max pain, straddle, IV and futures charts in
 *   place of the chain (ChartsViewV2.tsx), for the same symbol and expiry, refreshed with the chain.
 * - Below the chain: the Analysis tabs, Ask AI and My analyses (AnalysisSlotV2).
 * - FNO-1: with the PAPER account on, a call or put cell opens its actions and the PAPER order ticket, held contracts
 *   show "P", and a bottom strip holds the key figures (src/fno/ChainTradingV2.tsx). The page itself places nothing.
 * - FNO-3: the symbol hub's tab row on top (Chart | Option chain | Futures | Overview, src/fno/SymbolHubTabsV2.tsx). The
 *   address keeps the symbol and the expiry (`?underlying=&expiry=`, and the chart's `tf`), so the tabs carry them.
 * - F&O 8b-a: opened from one of the user's own signals (`?signal=action:12`), a panel says where his own deployment
 *   settings land and the chain shades those cells (src/fno/InsightsV2.tsx). Marks only: nothing is picked or placed.
 * - F&O 8b-b: "Trade this setup" on each item of that panel opens the existing PAPER ticket / basket pre-filled with
 *   those strikes (src/fno/ChainTradingV2.tsx); `?trade=1` (the signal card's "Trade this setup") opens it by itself
 *   once, when exactly one of his deployments gives strikes on the expiry on screen. Nothing is placed by either: he
 *   reviews it and places it himself.
 * - MO4 slice 2: with `markets_view` on, "What the chain shows" on top (ChainDishV2): five plain lines from the analyses.
 * Shown only while `ui_v2` is on (src/shell/pagesV2.ts).
 */

export type ChainView = "chain" | "charts" | "oi";

/** "Chain | Charts": which view sits under the summary. Two 44 px targets on a phone (no padding around them, so the
 * toggle fits the 44 px gap above the chain), 28 px from md. */
export function ViewToggle({ view, onView, className }: { view: ChainView; onView: (v: ChainView) => void; className?: string }) {
  const items: { key: ChainView; label: string; Icon: typeof Table2 }[] = [
    { key: "chain", label: "Chain", Icon: Table2 },
    { key: "charts", label: "Charts", Icon: ChartColumn },
    { key: "oi", label: "OI", Icon: Activity },   // UI-28 U6.1
  ];
  return (
    <div role="group" aria-label="View" data-view-toggle className={cx("inline-flex items-center gap-space-1 rounded-panel bg-surface-2 p-0 md:p-space-1", className)}>
      {items.map(({ key, label, Icon }) => (
        <button key={key} type="button" aria-pressed={view === key} onClick={() => onView(key)}
          className={cx("inline-flex h-11 items-center gap-space-1 rounded-control px-space-2 text-t13 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-7",
            view === key ? "bg-surface-1 font-semibold text-fg shadow-card" : "text-fg-muted hover:text-fg")}>
          <Icon size={14} aria-hidden />{label}
        </button>
      ))}
    </div>
  );
}

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
  // 8b-a: opened from one of the user's own signals (`?signal=action:12`): his settings' strikes are marked.
  const [signal, setSignal] = useState<string | null>(() => {
    const v = params.get("signal") ?? "";
    return SIGNAL_PARAM.test(v) ? v : null;
  });
  const [autoTrade] = useState(() => params.get(TRADE_PARAM) === "1");
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
      next.delete("basket");                       // read once on opening (the futures chain's basket badge)
      next.delete(TRADE_PARAM);                    // 8b-b: read once on opening (a reload never reopens the ticket)
      if (!signal) next.delete("signal");
      return next.toString() === prev.toString() ? prev : next;
    }, { replace: true });
  }, [symbol, expiry, setParams, signal]);
  return (
    <>
      <SymbolHubTabsV2 symbol={symbol} current="option-chain" carry={{ expiry, timeframe: carried.timeframe }} />
      <OptionChainView symbol={symbol} onSymbol={setSymbol} chain={chain} marketOpen={marketOpen} prefs={prefs}
        onPrefs={(next) => { setPrefs(next); writeColumns(next); }} signal={signal} onClearSignal={() => setSignal(null)} autoTrade={autoTrade} />
    </>
  );
}

/** The page's look for given data (no reads of its own), so tests render every state directly. */
// UX-5: the Symbol / Expiry pickers' rows live in ./model (TERM-2: the Terminal's context bar uses the same ones).
import { expiryComboOptions, symbolOptions } from "./model";
export { expiryComboOptions, symbolOptions };

export function OptionChainView({ symbol, onSymbol, chain, marketOpen, prefs, onPrefs, signal = null, onClearSignal, autoTrade = false }: {
  symbol: string;
  onSymbol: (symbol: string) => void;
  chain: OptionChainData;
  /** The NSE session from the system status (null while not known); the chain's own `market_open` wins once it answers. */
  marketOpen: boolean | null;
  prefs: ColumnPrefs;
  onPrefs: (prefs: ColumnPrefs) => void;
  /** 8b-a: one of the user's own signals ("action:12"), to mark where his own settings land; null for none. */
  signal?: string | null;
  onClearSignal?: () => void;
  /** 8b-b: opened from the signal card's "Trade this setup": open the pre-filled ticket / basket once, by itself. */
  autoTrade?: boolean;
}) {
  const [picked, setPicked] = useState<number | null>(null);
  const [askOpen, setAskOpen] = useState(false);
  const phone = useMediaQuery(PHONE_QUERY);
  const [view, setView] = useState<ChainView>("chain");
  const colour = useColourV2();          // colour v2: the Calls / Puts headings in their side's colour
  const symbolId = useId();
  const expiryId = useId();

  const live = chain.live;
  // T1-3 on this page (owner 25 Oct): the spot card reads the same feed and time as the chart (src/terminal/spot.ts).
  const spot = useTerminalSpot({ symbol, hub: useFeatures().on("data_hub"), enabled: true, chain: live ?? null });
  const notes = chainNotes(live, marketOpen, (iso) => formatTimeIST(iso));
  const prevClose = usePrevClose(symbol);
  // UI-28 U7.1: the chain shows every strike, or ATM ± N (the choice is kept for the visit).
  const [range, setRange] = useState<number>(0);
  const shownLive = useMemo(() => (live && range ? { ...live, rows: rowsAround(live.rows, live.summary?.atm_strike, range) } : live), [live, range]);
  const extraSymbol = useMemo(() => (new Set<string>([...INDEX_SYMBOLS, ...STOCK_SYMBOLS]).has(symbol) ? null : symbol), [symbol]);
  const expiryOptions = chain.expiries?.expiries.length
    ? chain.expiries.expiries
    : chain.expiry ? [{ date: chain.expiry, kind: "weekly" as const }] : [];
  const pickedRow: ChainRow | null = live?.rows.find((r) => r.strike === picked) ?? null;
  const trading = useChainTrading({ symbol, live, prefs, onPrefs, expiries: expiryOptions, signal });
  const setupState = useSetupStrikes(signal, live?.summary.spot ?? null);
  const setupData = setupState.status === "ready" && setupState.data.underlying === symbol ? setupState.data : null;
  const marks = useMemo(() => setupMarks(setupData, live?.expiry ?? chain.expiry), [setupData, live?.expiry, chain.expiry]);
  // 8b-b: "Trade this setup" - the existing ticket / basket pre-filled from one item (only with the PAPER account on).
  const tradeSetup = trading.tradeSetup;
  const tradeOf = live && tradeSetup ? (item: SetupItem) => setupTrade(item, symbol, live.expiry, live.rows) : undefined;
  const autoDone = useRef(false);
  useEffect(() => {
    if (!autoTrade || autoDone.current || !setupData || !live || !tradeSetup) return;
    autoDone.current = true;                       // once: a later refresh of the chain never reopens it
    const ready = setupData.items.map((i) => setupTrade(i, symbol, live.expiry, live.rows)).filter((t) => t.kind !== "none");
    if (ready.length === 1) tradeSetup(ready[0]);
  }, [autoTrade, setupData, live, tradeSetup, symbol]);

  return (
    <div className="space-y-space-4" data-page="option-chain-v2">
      <PageIntro id="option-chain" lines={["Pick an index or stock: the strike nearest the price is in the middle. Tap a row to see all its figures."]} />
      <header className="flex flex-col gap-space-3 md:flex-row md:items-end md:justify-between">
        <h1 className="sr-only md:not-sr-only md:text-t24 md:font-semibold md:text-fg">Option chain</h1>
        <div data-tour="oc-pickers" className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-end gap-space-2 md:flex md:gap-space-3">
          <div className="min-w-0 md:w-48">
            <label htmlFor={symbolId} className="mb-space-1 block text-t12 font-medium text-fg-muted">Symbol</label>
            <Combobox id={symbolId} label="Symbol" value={symbol} onChange={(v) => { setPicked(null); onSymbol(v); }} searchable className="w-full"
              options={symbolOptions(extraSymbol)} />
          </div>
          <div className="min-w-0 md:w-56">
            <label htmlFor={expiryId} className="mb-space-1 flex items-center gap-space-1 text-t12 font-medium text-fg-muted">
              <CalendarDays size={12} aria-hidden /> Expiry
            </label>
            <Combobox id={expiryId} label="Expiry" value={chain.expiry ?? ""} disabled={expiryOptions.length === 0} className="w-full"
              placeholder={chain.loading ? "Loading…" : "Nearest"} onChange={(v) => { setPicked(null); chain.setExpiry(v); }}
              options={expiryComboOptions(expiryOptions)} />
          </div>
          <ColumnsMenuV2 prefs={prefs} onChange={onPrefs} />
          {/* OC-2: "Ask AI about this chain" is the header's primary action - a side panel (phone: a bottom sheet). */}
          <button type="button" onClick={() => setAskOpen(true)} data-oc-ask-open
            className="col-span-3 inline-flex min-h-11 items-center justify-center gap-space-2 rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand hover:bg-brand-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface md:col-span-1 md:min-h-9">
            <Sparkles size={16} aria-hidden />Ask AI
          </button>
        </div>
      </header>

      {notes.length > 0 && (
        <div className="space-y-space-2" data-chain-notes>
          {notes.map((n) => <Note key={n.key} tone="neutral">{n.text}</Note>)}
        </div>
      )}

      {/* OC-1 (owner, 25 Oct): header -> status -> summary cards -> the chain -> the compact reading -> the tables. */}
      <SummaryStripV2 summary={live?.summary ?? null} expiry={live?.expiry ?? chain.expiry} loading={chain.loading && !live} spot={spot} prevClose={prevClose} />

      <SetupPanelV2 state={setupState} symbol={symbol} expiry={live?.expiry ?? chain.expiry} onExpiry={(e) => { setPicked(null); chain.setExpiry(e); }}
        onClear={() => onClearSignal?.()} tradeOf={tradeOf} onTrade={tradeSetup} />

      {view === "charts" && (
        <section aria-label="Charts" data-block="charts" className="space-y-space-3">
          <div className="flex justify-center"><ViewToggle view={view} onView={setView} /></div>
          <ChainCharts symbol={symbol} expiry={live?.expiry ?? chain.expiry} refreshKey={live?.fetched_at ?? null} />
        </section>
      )}

      {view === "oi" && (
        <section aria-label="OI" data-block="oi" className="space-y-space-3">
          <div className="flex justify-center"><ViewToggle view={view} onView={setView} /></div>
          <OiViewV2 underlying={symbol} />
        </section>
      )}

      {view === "chain" && <section aria-label="Chain" data-block="chain" data-tour="oc-chain" className="relative">
        {/* The toggle sits in the gap above the chain, centred over the strike column, so the chain does not move. */}
        {!(live && live.rows.length > 0) && <div className="mb-space-3 flex justify-center"><ViewToggle view={view} onView={setView} /></div>}
        {chain.problem && !live && <Note tone="warn">{chain.problem === "no-session" ? TEXT.noSession : chain.problem === "plan" ? TEXT.plan : TEXT.loadFailed}</Note>}
        {chain.loading && !live && !chain.problem && (
          <div aria-busy="true" className="space-y-space-2 rounded-panel border border-subtle bg-surface-1 p-space-4">
            <span className="sr-only">Loading the option chain</span>
            {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-6" />)}
          </div>
        )}
        {live && live.rows.length === 0 && <Note tone="neutral">{TEXT.empty}</Note>}
        {live && live.rows.length > 0 && (
          <>
            <div aria-hidden className="mb-space-2 flex justify-between text-t12 font-semibold uppercase text-fg-muted">
              <span className={colour ? "text-down" : undefined}>Calls</span><span className={colour ? "text-up" : undefined}>Puts</span>
            </div>
            <ViewToggle view={view} onView={setView} className="absolute -top-space-5 left-1/2 z-10 -translate-x-1/2 md:-top-space-4" />
            <div role="group" aria-label="Strikes shown" data-strike-range className="mb-space-2 flex items-center gap-space-2">
              <span aria-hidden className="text-t12 text-fg-muted">Strikes</span>
              {STRIKE_RANGES.map((n) => (
                <button key={n} type="button" aria-pressed={range === n} onClick={() => setRange(n)} aria-label={n === 0 ? "All strikes" : `ATM ± ${n} strikes`}
                  className={cx("inline-flex min-h-11 items-center rounded-pill border px-space-3 text-t13 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8",
                    range === n ? "border-brand bg-brand-tint font-semibold text-fg" : "border-border text-fg-muted hover:text-fg")}>
                  {n === 0 ? "All" : `± ${n}`}
                </button>
              ))}
            </div>
            <ChainTableV2 live={shownLive ?? live} prefs={prefs} centreKey={`${symbol}|${live.expiry ?? ""}${trading.centreSuffix}`} onPick={(row) => setPicked(row.strike)}
              onPickLeg={trading.onPickLeg} held={trading.held} setup={marks.size ? marks : undefined} />
            {chain.problem && <p className="mt-space-2 text-t12 text-fg-muted">{TEXT.loadFailed}</p>}
          </>
        )}
        {trading.strip}
      </section>}
      {trading.layer}

      {/* UI-28 U1.2 (owner, 28 Oct): the "What the chain shows" prose is no longer mounted; its numbers stay in the summary strip. */}

      {/* OC4: the Analysis tabs, Ask AI and My analyses for the chain on screen. */}
      <AnalysisSlotV2 symbol={symbol} expiry={live?.expiry ?? chain.expiry} refreshKey={live?.fetched_at ?? null} />

      <Sheet side={phone ? "bottom" : "right"} open={askOpen} onOpenChange={setAskOpen} title="Ask AI about this chain">
        <AskAi symbol={symbol} expiry={live?.expiry ?? chain.expiry} refreshKey={live?.fetched_at ?? undefined} />
      </Sheet>

      <RowDetailsV2 row={pickedRow} atm={live?.summary.atm_strike ?? null} underlying={symbol} onClose={() => setPicked(null)} />
    </div>
  );
}
