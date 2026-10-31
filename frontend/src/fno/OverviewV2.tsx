import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { watchlistsV2Api, type QuoteRow } from "../chart/watchlistsV2";
import { formatChange, formatDateIST, formatNumber, formatPct, formatPrice } from "../ui/format";
import { dayRange, yearRange, type Bar, type HubInfo } from "./hubModel";

/**
 * FNO-3 (owner order section 1): the Overview tab of the symbol hub - a compact snapshot of the symbol: the last price
 * and the change (the watchlist's batch quote, `POST /market-data/quotes`), the day range and, when the daily bars
 * reach back a year, the 52-week range (`POST /market-data/candles`, day bars), then F&O yes / no, the lot size and the
 * next expiries (the hub facts, from the instrument master). Everything from the trader's own broker session and the
 * master; a figure that has no data is left out - never sample data.
 * `OverviewView` is the look without reads (rendered directly by the tests).
 */
export interface OverviewData {
  quote: QuoteRow | null;
  bars: Bar[] | null;
}

type Reads = {
  quotes: (items: { symbol: string; exchange: string }[]) => Promise<{ items: QuoteRow[] }>;
  candles: (symbol: string, exchange: string) => Promise<Bar[]>;
};

const defaultReads: Reads = {
  quotes: (items) => watchlistsV2Api.quotes(items),
  candles: async (symbol, exchange) => {
    const out = await api.marketDataCandles([symbol], "day", 380, exchange, undefined, undefined, { forming: true, volume_from_future: false });
    return (out.symbols?.[symbol]?.bars ?? []) as Bar[];
  },
};

export function useOverview(symbol: string, exchange: string, reads: Reads = defaultReads): OverviewData {
  const [data, setData] = useState<OverviewData>({ quote: null, bars: null });
  useEffect(() => {
    let alive = true;
    setData({ quote: null, bars: null });
    reads.quotes([{ symbol, exchange }])
      .then((r) => { if (alive) setData((d) => ({ ...d, quote: r.items.find((i) => !i.missing) ?? null })); })
      .catch(() => { /* intentional: no quote: the price is left out */ });
    reads.candles(symbol, exchange)
      .then((bars) => { if (alive) setData((d) => ({ ...d, bars })); })
      .catch(() => { if (alive) setData((d) => ({ ...d, bars: [] })); });
    return () => { alive = false; };
  }, [symbol, exchange, reads]);
  return data;
}

function Fact({ label, children, testId }: { label: string; children: ReactNode; testId: string }) {
  return (
    <div className="rounded-control border border-subtle bg-surface-1 px-space-3 py-space-2" data-fact={testId}>
      <dt className="text-t12 text-fg-muted">{label}</dt>
      <dd className="font-tabular text-t15 font-semibold text-fg">{children}</dd>
    </div>
  );
}

export function OverviewView({ symbol, info, data }: { symbol: string; info: HubInfo | null; data: OverviewData }) {
  const q = data.quote;
  const day = data.bars ? dayRange(data.bars) : null;
  const year = data.bars ? yearRange(data.bars) : null;
  const optionDates = info?.option_expiries.map((e) => e.date) ?? [];
  const futureDates = info?.future_expiries ?? [];
  const changeTone = q && typeof q.change === "number" ? (q.change > 0 ? "text-up" : q.change < 0 ? "text-down" : "text-fg") : "text-fg";
  return (
    <section aria-label={`Overview of ${symbol}`} className="space-y-space-3" data-block="hub-overview">
      <h2 className="text-t18 font-semibold text-fg">Overview of {symbol}</h2>
      <dl className="grid grid-cols-2 gap-space-2 md:grid-cols-4">
        {q && q.ltp !== null && <Fact label="Last price" testId="ltp">{formatPrice(q.ltp)}</Fact>}
        {q && q.change !== null && (
          <Fact label="Change" testId="change">
            <span className={changeTone}>{formatChange(q.change)}{q.change_pct !== null && <span className="ml-space-1 text-t13">({formatPct(q.change_pct)})</span>}</span>
          </Fact>
        )}
        {day && <Fact label={day.which === "today" ? "Day range" : "Last session's range"} testId="day-range">{formatPrice(day.low)} – {formatPrice(day.high)}</Fact>}
        {year && <Fact label="52-week range" testId="year-range">{formatPrice(year.low)} – {formatPrice(year.high)}</Fact>}
        {info && <Fact label="F&O" testId="fno">{info.has_fno ? "Yes" : "No"}</Fact>}
        {info?.has_fno && info.lot_size !== null && <Fact label="Lot size" testId="lot">{formatNumber(info.lot_size)}</Fact>}
      </dl>
      {info?.has_fno && (optionDates.length > 0 || futureDates.length > 0) && (
        <dl className="grid gap-space-2 md:grid-cols-2" data-fact="expiries">
          {optionDates.length > 0 && (
            <div className="rounded-control border border-subtle bg-surface-1 px-space-3 py-space-2">
              <dt className="text-t12 text-fg-muted">Next option expiries</dt>
              <dd className="text-t14 text-fg">{optionDates.map((d) => formatDateIST(d)).join(" · ")}</dd>
            </div>
          )}
          {futureDates.length > 0 && (
            <div className="rounded-control border border-subtle bg-surface-1 px-space-3 py-space-2">
              <dt className="text-t12 text-fg-muted">Futures expiries</dt>
              <dd className="text-t14 text-fg">{futureDates.map((d) => formatDateIST(d)).join(" · ")}</dd>
            </div>
          )}
        </dl>
      )}
      {!q && data.bars !== null && data.bars.length === 0 && (
        <p className="rounded-control bg-surface-2 px-space-3 py-space-2 text-t13 text-fg" role="status">
          No prices for {symbol} from your broker just now.
        </p>
      )}
    </section>
  );
}

export default function OverviewV2({ symbol, exchange, info }: { symbol: string; exchange: string; info: HubInfo | null }) {
  const data = useOverview(symbol, exchange);
  return <OverviewView symbol={symbol} info={info} data={data} />;
}
