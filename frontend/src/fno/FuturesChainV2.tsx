import { ChartCandlestick, Info, ListPlus, ShoppingBasket, TriangleAlert } from "lucide-react";
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { ApiError, errorSentence } from "../api/errors";
import { watchlistsV2Api, type WatchlistV2 } from "../chart/watchlistsV2";
import { cx } from "../components/primitives/cx";
import { formatCount, formatCountChange } from "../optionChain/model";
import { chartTabPath } from "../shell/shellRoutes";
import { EMPTY, formatChange, formatPct, formatPrice, formatTimeIST } from "../ui/format";
import { useFeatures } from "../ui/useFeatures";
import { usePageVisible } from "../utils/usePageVisible";
import type { Side } from "./api";
import { basket as defaultBasket, useBasket, type BasketStore } from "./basket";
import { watchlistAdd, type Lists, type Note } from "./ChainActionSheetV2";
import {
  basketHref, expiryText, futureContract, futureLeg, futureTarget, futuresNotes, hubApi, monthWord, type FutureRow, type FuturesChain, type HubApi,
} from "./hubModel";
import OrderTicketV2 from "./OrderTicketV2";
import { PRACTICE_TITLE, type TicketTarget } from "./ticketModel";

/**
 * FNO-3 (owner order section 4): the Futures tab of the symbol hub - every listed future of the symbol with its LTP,
 * change, OI, OI change and basis against the spot, then the rollover from the near month to the next with one plain
 * caption (backend app/option_chain/futures_chain.py). Read from the trader's own broker session every 15 s while the
 * tab is shown (every 5 min with the market closed); no sample data - a price the broker did not give shows "–".
 *
 * Each row: Chart (the future's own chart, with the timeframe), Buy / Sell (the ONE order ticket of FNO-1, PAPER) and
 * Add to basket (the FNO-2 basket store; the badge opens the basket panel on the option chain page) - both only with the PAPER account on, like chain trading - and Add to
 * watchlist (behind `watchlists`). Nothing here places an order by itself, and no word tells the trader what to trade.
 * `FuturesChainView` is the look without reads (rendered directly by the tests).
 */
const POLL_OPEN_MS = 15_000;
const POLL_CLOSED_MS = 5 * 60_000;

export type FuturesProblem = "no-session" | "plan" | "budget" | "failed";

export function futuresProblemOf(e: unknown): FuturesProblem {
  if (!(e instanceof ApiError)) return "failed";
  if (e.status === 409) return e.dataTier ? "plan" : "no-session";
  if (e.status === 429) return "budget";
  return "failed";
}

export const FUTURES_TEXT: Record<FuturesProblem | "empty", string> = {
  "no-session": "Log in to your broker to see the futures.",
  plan: "This symbol is not available live on your plan.",
  budget: "Your broker's rate budget is in use just now. The prices load on the next refresh.",
  failed: "We could not load the futures. We will try again in a moment.",
  empty: "This symbol trades without futures.",
};

export function useFuturesChain(symbol: string, api: Pick<HubApi, "futures"> = hubApi) {
  const visible = usePageVisible();
  const [data, setData] = useState<FuturesChain | null>(null);
  const [problem, setProblem] = useState<FuturesProblem | null>(null);
  const [loading, setLoading] = useState(true);
  const open = useRef<boolean | undefined>(undefined);
  open.current = data?.market_open;
  useEffect(() => { setData(null); setProblem(null); setLoading(true); }, [symbol]);
  useEffect(() => {
    if (!visible || !symbol) return undefined;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = () => {
      api.futures(symbol)
        .then((d) => { if (alive) { setData(d); setProblem(null); } })
        .catch((e: unknown) => { if (alive) setProblem(futuresProblemOf(e)); })
        .finally(() => {
          if (!alive) return;
          setLoading(false);
          timer = setTimeout(run, open.current === false ? POLL_CLOSED_MS : POLL_OPEN_MS);
        });
    };
    run();
    return () => { alive = false; if (timer !== undefined) clearTimeout(timer); };
  }, [symbol, visible, api]);
  return { data, problem, loading };
}

export type RowPanel = { key: string; kind: "basket" | "watchlist" } | null;

export interface RowHandlers {
  onTrade: (row: FutureRow, side: Side) => void;
  onPanel: (panel: RowPanel) => void;
  onBasket: (row: FutureRow, side: Side) => void;
  onWatchlist: (row: FutureRow, list: WatchlistV2 | null) => void;
}

const BTN = "inline-flex min-h-11 items-center justify-center gap-space-1 rounded-control px-space-3 text-t13 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";
const GHOST = cx(BTN, "border border-border text-fg hover:bg-surface-2");
const CHIP = "min-h-11 rounded-control border border-border px-space-3 text-t13 font-semibold text-fg hover:bg-surface-2 md:min-h-9";

const tone = (v: number | null | undefined) => (typeof v !== "number" || v === 0 ? "text-fg" : v > 0 ? "text-up" : "text-down");

function Problem({ tone: t, children }: { tone: "neutral" | "warn"; children: string }) {
  const Icon = t === "warn" ? TriangleAlert : Info;
  return (
    <p role="status" className={cx("flex items-start gap-space-2 rounded-control px-space-3 py-space-2 text-t13", t === "warn" ? "bg-warn-tint text-warn" : "bg-surface-2 text-fg")}>
      <Icon size={16} aria-hidden className="mt-0.5 shrink-0" /><span>{children}</span>
    </p>
  );
}

function RowActions({ row, trading, watchlistsOn, timeframe, panel, on }: {
  row: FutureRow; trading: boolean; watchlistsOn: boolean; timeframe: string | null; panel: RowPanel; on: RowHandlers;
}) {
  const key = row.tradingsymbol;
  const toggle = (kind: "basket" | "watchlist") => () => on.onPanel(panel?.key === key && panel.kind === kind ? null : { key, kind });
  return (
    <div className="flex flex-wrap items-center gap-space-1" data-future-actions={key}>
      <Link to={chartTabPath(row.tradingsymbol, row.exchange, timeframe)} className={GHOST} data-action="chart" aria-label={`Chart of ${row.label}`}>
        <ChartCandlestick size={14} aria-hidden />Chart
      </Link>
      {trading && (
        <>
          <button type="button" className={cx(BTN, "bg-up text-on-brand")} onClick={() => on.onTrade(row, "BUY")} data-action="buy" aria-label={`Buy ${row.label}`}>Buy</button>
          <button type="button" className={cx(BTN, "bg-down text-on-brand")} onClick={() => on.onTrade(row, "SELL")} data-action="sell" aria-label={`Sell ${row.label}`}>Sell</button>
          <button type="button" className={GHOST} onClick={toggle("basket")} aria-expanded={panel?.key === key && panel.kind === "basket"}
            data-action="basket" aria-label={`Add ${row.label} to the basket`}>
            <ShoppingBasket size={14} aria-hidden /><span className="sr-only md:not-sr-only">Basket</span>
          </button>
        </>
      )}
      {watchlistsOn && (
        <button type="button" className={GHOST} onClick={toggle("watchlist")} aria-expanded={panel?.key === key && panel.kind === "watchlist"}
          data-action="watchlist" aria-label={`Add ${row.label} to a watchlist`}>
          <ListPlus size={14} aria-hidden /><span className="sr-only md:not-sr-only">Watchlist</span>
        </button>
      )}
    </div>
  );
}

function RowPanelView({ row, panel, watchlists, on }: { row: FutureRow; panel: RowPanel; watchlists: WatchlistV2[] | null; on: RowHandlers }) {
  if (!panel || panel.key !== row.tradingsymbol) return null;
  return (
    <div className="flex flex-wrap items-center gap-space-2 py-space-2" data-future-panel={panel.kind}>
      {panel.kind === "basket" ? (
        <>
          <button type="button" className={CHIP} onClick={() => on.onBasket(row, "BUY")} data-action="basket-buy">Buy leg, 1 lot</button>
          <button type="button" className={CHIP} onClick={() => on.onBasket(row, "SELL")} data-action="basket-sell">Sell leg, 1 lot</button>
        </>
      ) : watchlists === null ? <span className="text-t13 text-fg-muted">Loading your lists…</span> : (
        <>
          {watchlists.map((w) => <button key={w.id} type="button" className={CHIP} onClick={() => on.onWatchlist(row, w)} data-watchlist={w.id}>{w.name}</button>)}
          {watchlists.length === 0 && <button type="button" className={CHIP} onClick={() => on.onWatchlist(row, null)} data-watchlist="new">New list "F&amp;O"</button>}
        </>
      )}
    </div>
  );
}

/** `stack`: the % on its own line (a phone card's narrow column). */
function Signed({ value, pct, tick, stack = false }: { value: number | null; pct: number | null; tick?: number; stack?: boolean }) {
  if (value === null) return <span className="text-fg-muted">{EMPTY}</span>;
  return (
    <span className={tone(value)}>
      {formatChange(value, tick)}{pct !== null && <span className={cx("text-t12", stack ? "block" : "ml-space-1")}>({formatPct(pct)})</span>}
    </span>
  );
}

export interface FuturesViewProps {
  data: FuturesChain | null;
  problem: FuturesProblem | null;
  loading: boolean;
  trading: boolean;
  watchlistsOn: boolean;
  timeframe: string | null;
  panel: RowPanel;
  watchlists: WatchlistV2[] | null;
  note: Note;
  basketCount: number;
  on: RowHandlers;
}

/** The Futures tab's look for given data (no reads of its own). */
export function FuturesChainView({ data, problem, loading, trading, watchlistsOn, timeframe, panel, watchlists, note, basketCount, on }: FuturesViewProps) {
  const rows = data?.rows ?? [];
  const notes = futuresNotes(data);
  const spot = data?.spot;
  let body: ReactNode;
  if (!data && problem) body = <Problem tone="warn">{FUTURES_TEXT[problem]}</Problem>;
  else if (!data && loading) {
    body = (
      <div aria-busy="true" className="space-y-space-2 rounded-panel border border-subtle bg-surface-1 p-space-4">
        <span className="sr-only">Loading the futures</span>
        {Array.from({ length: 3 }, (_, i) => <div key={i} className="h-8 animate-pulse rounded-control bg-surface-2" />)}
      </div>
    );
  } else if (data && rows.length === 0) body = <Problem tone="neutral">{FUTURES_TEXT.empty}</Problem>;
  else if (data) {
    body = (
      <>
        {/* md and up: one table. */}
        <div className="hidden overflow-x-auto rounded-panel border border-subtle bg-surface-1 md:block">
          <table className="w-full text-left text-t14" data-futures-table>
            <thead className="border-b border-subtle text-t12 font-semibold uppercase text-fg-muted">
              <tr>
                <th scope="col" className="px-space-3 py-space-2">Contract</th>
                <th scope="col" className="px-space-3 py-space-2 text-right">LTP</th>
                <th scope="col" className="px-space-3 py-space-2 text-right">Change</th>
                <th scope="col" className="px-space-3 py-space-2 text-right">OI</th>
                <th scope="col" className="px-space-3 py-space-2 text-right">OI change</th>
                <th scope="col" className="px-space-3 py-space-2 text-right">Basis vs spot</th>
                <th scope="col" className="px-space-3 py-space-2"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <Fragment key={r.tradingsymbol}>
                  <tr className="border-t border-subtle first:border-t-0" data-future-row={r.tradingsymbol}>
                    <th scope="row" className="px-space-3 py-space-2 font-normal">
                      <span className="block font-semibold text-fg">{r.label}</span>
                      <span className="block text-t12 text-fg-muted">{monthWord(i)} · {expiryText(r)} · Lot {r.lot_size}</span>
                    </th>
                    <td className="whitespace-nowrap px-space-3 py-space-2 text-right font-tabular font-semibold text-fg" data-cell="ltp">{formatPrice(r.ltp, r.tick_size)}</td>
                    <td className="whitespace-nowrap px-space-3 py-space-2 text-right font-tabular" data-cell="change"><Signed value={r.change} pct={r.change_pct} tick={r.tick_size} /></td>
                    <td className="whitespace-nowrap px-space-3 py-space-2 text-right font-tabular text-fg" data-cell="oi">{formatCount(r.oi)}</td>
                    <td className={cx("whitespace-nowrap px-space-3 py-space-2 text-right font-tabular", tone(r.oi_change))} data-cell="oi-change">{formatCountChange(r.oi_change)}</td>
                    <td className="whitespace-nowrap px-space-3 py-space-2 text-right font-tabular" data-cell="basis"><Signed value={r.basis} pct={r.basis_pct} tick={r.tick_size} /></td>
                    <td className="px-space-3 py-space-2"><RowActions row={r} trading={trading} watchlistsOn={watchlistsOn} timeframe={timeframe} panel={panel} on={on} /></td>
                  </tr>
                  {panel?.key === r.tradingsymbol && (
                    <tr><td colSpan={7} className="px-space-3"><RowPanelView row={r} panel={panel} watchlists={watchlists} on={on} /></td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
        {/* A phone: one card per future. */}
        <ul className="space-y-space-2 md:hidden" data-futures-cards>
          {rows.map((r, i) => (
            <li key={r.tradingsymbol} className="rounded-panel border border-subtle bg-surface-1 p-space-3" data-future-card={r.tradingsymbol}>
              <div className="flex items-start justify-between gap-space-2">
                <div className="min-w-0">
                  <p className="truncate text-t14 font-semibold text-fg">{r.label}</p>
                  <p className="text-t12 text-fg-muted">{monthWord(i)} · {expiryText(r)}</p>
                </div>
                <div className="text-right">
                  <p className="font-tabular text-t15 font-semibold text-fg">{formatPrice(r.ltp, r.tick_size)}</p>
                  <p className="font-tabular text-t12"><Signed value={r.change} pct={r.change_pct} tick={r.tick_size} /></p>
                </div>
              </div>
              <dl className="mt-space-2 grid grid-cols-3 gap-space-2 text-t12">
                <div><dt className="text-fg-muted">OI</dt><dd className="font-tabular text-t13 text-fg">{formatCount(r.oi)}</dd></div>
                <div><dt className="text-fg-muted">OI change</dt><dd className={cx("font-tabular text-t13", tone(r.oi_change))}>{formatCountChange(r.oi_change)}</dd></div>
                <div><dt className="text-fg-muted">Basis</dt><dd className="font-tabular text-t13"><Signed value={r.basis} pct={r.basis_pct} tick={r.tick_size} stack /></dd></div>
              </dl>
              <div className="mt-space-2"><RowActions row={r} trading={trading} watchlistsOn={watchlistsOn} timeframe={timeframe} panel={panel} on={on} /></div>
              <RowPanelView row={r} panel={panel} watchlists={watchlists} on={on} />
            </li>
          ))}
        </ul>
      </>
    );
  }
  return (
    <section aria-label="Futures" className="space-y-space-3" data-block="futures-chain">
      <div className="flex flex-wrap items-end justify-between gap-space-2">
        <div>
          <h2 className="text-t18 font-semibold text-fg">Futures{data ? ` of ${data.underlying}` : ""}</h2>
          {spot && spot.ltp !== null && (
            <p className="font-tabular text-t13 text-fg-muted" data-futures-spot>
              Spot {spot.symbol} {formatPrice(spot.ltp)} <Signed value={spot.change} pct={spot.change_pct} />
            </p>
          )}
        </div>
        <div className="flex items-center gap-space-2">
          {trading && <span className="inline-flex rounded-pill bg-warn-tint px-space-2 text-t12 font-semibold text-warn" data-practice-frame>{PRACTICE_TITLE}</span>}
          {trading && data && (
            <Link to={basketHref(data.underlying, timeframe)} data-futures-basket
              className="inline-flex min-h-11 items-center gap-space-1 rounded-control px-space-2 text-t13 font-semibold text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9"
              aria-label={`Open the basket: ${basketCount} leg${basketCount === 1 ? "" : "s"}`}>
              <ShoppingBasket size={16} aria-hidden />
              <span className={cx("rounded-pill px-space-2 text-t12", basketCount ? "bg-brand text-on-brand" : "bg-surface-2 text-fg-muted")}>{basketCount}</span>
            </Link>
          )}
        </div>
      </div>
      {notes.map((n) => <Problem key={n} tone="neutral">{n}</Problem>)}
      {body}
      {data?.rollover?.caption && (
        <div className="rounded-panel border border-subtle bg-surface-1 px-space-3 py-space-2" data-futures-rollover>
          <p className="text-t12 font-semibold uppercase text-fg-muted">Rollover {data.rollover.near_label} → {data.rollover.next_label}</p>
          <p className="text-t14 text-fg">{data.rollover.caption}</p>
        </div>
      )}
      {data && problem && <p className="text-t12 text-fg-muted">{FUTURES_TEXT[problem]}</p>}
      {data && <p className="text-t12 text-fg-muted">Basis is the future's price minus the spot price. Prices from your broker, {formatTimeIST(data.fetched_at)}.</p>}
      {note && (
        <p role="status" data-futures-note={note.tone}
          className={cx("rounded-control px-space-3 py-space-2 text-t13", note.tone === "done" ? "bg-surface-2 text-fg" : "bg-warn-tint text-warn")}>
          {note.text}
        </p>
      )}
    </section>
  );
}

/** The basket's words for an added future. */
export function addFutureToBasket(store: BasketStore, row: FutureRow, side: Side): Note {
  const ok = store.add(futureLeg(row, side));
  return ok ? { tone: "done", text: `Added to the basket: ${side === "BUY" ? "Buy" : "Sell"} 1 lot of ${row.label}.` }
    : { tone: "warn", text: "The basket is full (20 legs). Remove a leg first." };
}

export default function FuturesChainV2({ symbol, timeframe, api, lists = watchlistsV2Api, basketStore = defaultBasket }: {
  symbol: string; timeframe: string | null; api?: Pick<HubApi, "futures">; lists?: Lists; basketStore?: BasketStore;
}) {
  const features = useFeatures();
  const trading = features.on("paper_account");
  const watchlistsOn = features.on("watchlists");
  const { data, problem, loading } = useFuturesChain(symbol, api);
  const [panel, setPanel] = useState<RowPanel>(null);
  const [watchlists, setWatchlists] = useState<WatchlistV2[] | null>(null);
  const [note, setNote] = useState<Note>(null);
  const [ticket, setTicket] = useState<{ target: TicketTarget; side: Side } | null>(null);
  const legs = useBasket(basketStore);
  const done = (n: Note) => { setNote(n); if (n?.tone === "done") setPanel(null); };
  const on: RowHandlers = {
    onTrade: (row, side) => { setNote(null); setTicket({ target: futureTarget(row), side }); },
    onPanel: (p) => {
      setPanel(p);
      setNote(null);
      if (p?.kind === "watchlist" && watchlists === null) lists.list().then(setWatchlists).catch((e: unknown) => setNote({ tone: "warn", text: errorSentence(e) }));
    },
    onBasket: (row, side) => done(addFutureToBasket(basketStore, row, side)),
    onWatchlist: (row, list) => { void watchlistAdd(lists, futureContract(row), list).then(done); },
  };
  return (
    <>
      <FuturesChainView data={data} problem={problem} loading={loading} trading={trading} watchlistsOn={watchlistsOn} timeframe={timeframe}
        panel={panel} watchlists={watchlists} note={note} basketCount={legs.length} on={on} />
      {trading && (
        <OrderTicketV2 open={ticket !== null} target={ticket?.target ?? null} side={ticket?.side ?? "BUY"} onClose={() => setTicket(null)} />
      )}
    </>
  );
}
