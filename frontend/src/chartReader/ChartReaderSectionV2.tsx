import { useCallback, useEffect, useId, useRef, useState } from "react";
import { InPlaceLink } from "../inplace/InPlaceLink";
import { friendlyError } from "../api/errors";
import { isFeatureOff } from "../chartAnalyze/api";
import { guardText } from "../chartAnalyze/signalLanguage";
import { Button, Skeleton } from "../components/primitives";
import { Combobox } from "../ui/Combobox";
import { HelpPopover } from "../ui/HelpPopover";
import { useFeatures } from "../ui/useFeatures";
import { chartReaderApi } from "./api";
import { AbhiCard, BookCard, bookTitle, card, estimateText, type ReadState } from "./ReaderCardsV2";
import type { ReaderRequest, ReaderSource, ReaderStrategies } from "./types";

/**
 * M86 W3: the chart reader inside the Analyze-a-chart panel (the Copilot's Analyze tab and /chart-analyze), behind the
 * `chart_reader` flag (nothing shows while it is off). Opens in place. The strategies list is free (labels only); a
 * read runs ONLY when the user presses a read button - never on its own. "Abhi's method" is the default card; each
 * book-strategy read opens its own card below (they stack, each closes), never merged into Abhi's card and never a
 * filter or gate on anything.
 */

export const READER_FLAG = "chart_reader";
export const NOT_AVAILABLE = "The chart reader is not available right now.";
export const NEED_SYMBOL = "Type a symbol first.";
export const NEED_CHART = "Add a chart above first.";
/** The M59 product label of a stored-candles read, as the API returns it (app/chart_analyze/hybrid.py LABEL_CODE_AI). */
export const LAKE_LABEL = "Advanced analysis (code + AI)";

interface BookEntry { key: number; strategy: string; title: string; symbol?: string; request: ReaderRequest; state: ReadState }

export default function ChartReaderSectionV2({ itemId, symbol: knownSymbol }: { itemId?: number | null; symbol?: string | null }) {
  const features = useFeatures();
  const on = features.on(READER_FLAG);
  const [list, setList] = useState<ReaderStrategies | null>(null);
  const [listProblem, setListProblem] = useState<string | null>(null);
  const [off, setOff] = useState(false);
  const [symbol, setSymbol] = useState(knownSymbol ?? "");
  const [source, setSource] = useState<ReaderSource>(knownSymbol || !itemId ? "lake" : "upload");
  const [abhi, setAbhi] = useState<ReadState>({ phase: "idle" });
  const [abhiSymbol, setAbhiSymbol] = useState<string | undefined>();
  const [books, setBooks] = useState<BookEntry[]>([]);
  const [pick, setPick] = useState("");
  const nextKey = useRef(1);
  const live = useRef(true);
  const symbolId = useId();
  const pickId = useId();

  useEffect(() => () => { live.current = false; }, []);
  useEffect(() => { if (knownSymbol) setSymbol((s) => s || knownSymbol); }, [knownSymbol]);
  useEffect(() => { if (!itemId && source === "upload") setSource("lake"); }, [itemId, source]);

  useEffect(() => {
    if (!on) return;
    let alive = true;
    chartReaderApi.strategies().then(
      (s) => { if (alive) { setList(s); setListProblem(null); } },
      (e) => { if (!alive) return; if (isFeatureOff(e)) setOff(true); else setListProblem(friendlyError(e)); },
    );
    return () => { alive = false; };
  }, [on]);

  const typed = symbol.trim().toUpperCase();
  const blocked = source === "lake" ? (typed ? null : NEED_SYMBOL) : itemId ? null : NEED_CHART;

  const requestFor = useCallback((strategy: string): ReaderRequest => (
    source === "lake" ? { strategy, source, symbol: typed } : { strategy, source, item_id: itemId ?? undefined }
  ), [source, typed, itemId]);

  const run = useCallback(async (req: ReaderRequest): Promise<ReadState> => {
    try {
      const result = await chartReaderApi.read(req);
      return { phase: "done", result };
    } catch (e) {
      if (isFeatureOff(e)) setOff(true);
      return { phase: "failed", message: friendlyError(e) };
    }
  }, []);

  const readAbhi = useCallback(async () => {
    if (!list || blocked) return;
    const req = requestFor(list.default || "ABHI");
    setAbhiSymbol(req.symbol);
    setAbhi({ phase: "loading" });
    const state = await run(req);
    if (live.current) setAbhi(state);
  }, [list, blocked, requestFor, run]);

  const runBook = useCallback(async (key: number, req: ReaderRequest) => {
    setBooks((b) => b.map((x) => (x.key === key ? { ...x, state: { phase: "loading" } } : x)));
    const state = await run(req);
    if (live.current) setBooks((b) => b.map((x) => (x.key === key ? { ...x, state } : x)));
  }, [run]);

  const readBook = useCallback(() => {
    if (!list || blocked || !pick) return;
    const s = list.strategies.find((x) => x.id === pick);
    const key = nextKey.current++;
    const req = requestFor(pick);
    setBooks((b) => [...b, { key, strategy: pick, title: bookTitle(s, pick), symbol: req.symbol, request: req, state: { phase: "loading" } }]);
    void runBook(key, req);
  }, [list, blocked, pick, requestFor, runBook]);

  if (!on || off) return null;

  const header = (
    <div className="flex items-center gap-space-2">
      <h2 id="reader-title" className="text-t15 font-semibold text-fg">Chart reader</h2>
      <HelpPopover title="Chart reader">
        <p>Abhi&apos;s method reads the trend, the buyer and seller areas, the correction and the commitment candle, and names the area of interest.</p>
        <p>Each book strategy is a separate read in its own card, named after its book. It never changes Abhi&apos;s card.</p>
        <p>A read runs only when you press a read button. A chart read before, with the same structure, is shown from the cache at no cost.</p>
      </HelpPopover>
    </div>
  );

  if (!list) {
    return (
      <section className={card} aria-labelledby="reader-title" data-testid="chart-reader">
        {header}
        {listProblem ? (
          <p role="alert" className="mt-space-2 text-t14 text-fg" data-testid="reader-list-error">{listProblem}</p>
        ) : <Skeleton className="mt-space-3 h-16" label="Loading" />}
      </section>
    );
  }

  if (!list.available) {
    return (
      <section className={card} aria-labelledby="reader-title" data-testid="chart-reader">
        {header}
        <div role="status" className="mt-space-2 rounded-control bg-warn-tint p-space-3 text-t14 text-fg" data-testid="reader-unavailable">
          <p className="font-semibold">{NOT_AVAILABLE}</p>
          {list.message ? <p className="mt-space-1 text-t12 text-fg-muted">{guardText(list.message)}</p> : null}
          {list.installed && list.connected === false && (
            <InPlaceLink to="/settings/ai" data-testid="reader-connect-key"
                  className="mt-space-2 inline-flex min-h-11 items-center rounded-control px-space-1 text-t14 font-semibold text-brand hover:underline md:min-h-9">
              Connect my Claude key
            </InPlaceLink>
          )}
        </div>
      </section>
    );
  }

  const bookOptions = list.strategies.filter((s) => s.id !== list.default);
  // The API's own estimate for what this read sends: one uploaded picture, or the stored-candles charts (plus the
  // paired chart for a strategy with two-chart conditions).
  const estimateFor = (twoChart: boolean) => (source === "upload" ? list.estimate_usd_upload
    : twoChart ? list.estimate_usd_two_chart : undefined) ?? list.estimate_usd;
  const picked = list.strategies.find((s) => s.id === pick);
  const sourceOption = (value: ReaderSource, title: string, line: string, disabled: boolean) => (
    <label className={`flex min-h-11 items-start gap-space-3 rounded-control border p-space-3 ${disabled ? "border-subtle opacity-70" : `cursor-pointer ${source === value ? "border-brand bg-surface-2" : "border-subtle"}`}`}
      data-testid={`reader-source-${value}`}>
      <input type="radio" name="reader-source" value={value} checked={source === value} disabled={disabled} onChange={() => setSource(value)} className="mt-1" />
      <span className="min-w-0"><span className="block text-t14 font-semibold text-fg">{title}</span>
        <span className="block text-t12 text-fg-muted">{line}</span></span>
    </label>
  );

  return (
    <div className="space-y-space-4" data-testid="chart-reader">
      <section className={card} aria-labelledby="reader-title">
        {header}
        <fieldset className="mt-space-3">
          <legend className="mb-space-2 text-t12 font-medium text-fg-muted">Read from</legend>
          <div className="grid gap-space-2 sm:grid-cols-2">
            {sourceOption("lake", "ATP's stored candles", LAKE_LABEL, false)}
            {sourceOption("upload", "Your chart picture", itemId ? "AI only" : NEED_CHART, !itemId)}
          </div>
        </fieldset>
        {source === "lake" && (
          <div className="mt-space-3">
            <label htmlFor={symbolId} className="mb-space-1 block text-t12 font-medium text-fg-muted">Symbol</label>
            <input id={symbolId} value={symbol} onChange={(e) => setSymbol(e.target.value)} autoComplete="off" spellCheck={false}
              placeholder="e.g. NIFTY 50" data-testid="reader-symbol"
              className="h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand sm:max-w-xs md:h-9" />
          </div>
        )}
      </section>

      <AbhiCard state={abhi} estimateUsd={estimateFor(false)} symbol={abhiSymbol} onRead={() => void readAbhi()}
        disabled={Boolean(blocked) || abhi.phase === "loading"} disabledReason={blocked ?? undefined} />

      {bookOptions.length > 0 && (
        <section className={card} aria-labelledby="reader-books-title" data-testid="reader-books">
          <h3 id="reader-books-title" className="text-t15 font-semibold text-fg">Book strategies</h3>
          <div className="mt-space-2 flex flex-col gap-space-2 sm:flex-row sm:items-end">
            <div className="min-w-0 flex-1">
              <label htmlFor={pickId} className="mb-space-1 block text-t12 font-medium text-fg-muted">Strategy</label>
              <Combobox id={pickId} label="Strategy" value={pick} onChange={setPick} placeholder="Choose a strategy"
                options={bookOptions.map((s) => ({ value: s.id, label: s.name }))} dataAttrs={{ "data-testid": "reader-book-pick" }} />
            </div>
            <Button className="h-11 w-full sm:w-auto md:h-9" disabled={!pick || Boolean(blocked)} reason={blocked ?? (pick ? undefined : "Choose a strategy first.")}
              onClick={readBook} data-testid="reader-book-read">Read this strategy</Button>
          </div>
          <p className="mt-space-2 font-tabular text-t12 text-fg-muted">{estimateText(estimateFor(Boolean(picked?.two_chart)))}</p>
        </section>
      )}

      {books.map((b) => (
        <BookCard key={b.key} title={b.title} state={b.state} symbol={b.symbol}
          onClose={() => setBooks((x) => x.filter((y) => y.key !== b.key))} onRetry={() => void runBook(b.key, b.request)} />
      ))}
    </div>
  );
}
