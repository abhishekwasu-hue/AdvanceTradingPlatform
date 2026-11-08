import { useEffect, useState } from "react";
import { eodApi, type EodChainReply, type EodChainRow } from "../api/eod";
import { LoadingRowsV2 } from "../dashboard/CardV2";
import { EMPTY, formatCount, formatDateIST, formatNumber, formatPrice } from "../ui/format";
import { lastCloseText } from "../ui/lastClose";

/**
 * M86 D2: the Option chain for a user without a broker - the last chain stored from NSE's F&O end-of-day file (calls and
 * puts close and OI per strike, the nearest expiry of that day), read only. Nothing stored: one plain line (the page's
 * line above carries the one "Connect your broker for live prices" button). No trading from these rows.
 */
const AROUND = 10;

/** The strikes shown: AROUND either side of the strike nearest the spot (all rows when the spot is not known). Pure. */
export function eodRowsAround(rows: readonly EodChainRow[], spot: number | null, around = AROUND): EodChainRow[] {
  if (spot === null || rows.length <= around * 2 + 1) return [...rows];
  let mid = 0;
  rows.forEach((r, i) => { if (Math.abs(r.strike - spot) < Math.abs(rows[mid].strike - spot)) mid = i; });
  return rows.slice(Math.max(0, mid - around), mid + around + 1);
}

type Read = { status: "loading" } | { status: "error" } | { status: "ok"; value: EodChainReply };

export default function EodChainV2({ symbol }: { symbol: string }) {
  const [read, setRead] = useState<Read>({ status: "loading" });
  useEffect(() => {
    const stop = new AbortController();
    setRead({ status: "loading" });
    eodApi.chain(symbol, null, stop.signal).then((value) => setRead({ status: "ok", value })).catch(() => { if (!stop.signal.aborted) setRead({ status: "error" }); });
    return () => stop.abort();
  }, [symbol]);

  if (read.status === "loading") return <LoadingRowsV2 rows={4} />;
  if (read.status === "error" || read.value.rows.length === 0) {
    return <p data-eod-chain="none" className="py-space-3 text-t13 text-fg-muted">No end-of-day option chain is stored for {symbol} yet.</p>;
  }
  const chain = read.value;
  const rows = eodRowsAround(chain.rows, chain.spot);
  const cell = "whitespace-nowrap px-space-2 py-space-2 text-right font-tabular text-t13";
  return (
    <div data-eod-chain={chain.expiry ?? ""} className="space-y-space-2">
      <p className="text-t13 text-fg-muted">
        {lastCloseText(chain.date)}{chain.expiry ? ` · expiry ${formatDateIST(chain.expiry, { year: false })}` : ""}
        {chain.spot !== null ? ` · ${symbol} ${formatPrice(chain.spot)}` : ""}
      </p>
      <div className="overflow-x-auto rounded-panel border border-subtle">
        <table className="w-full min-w-[20rem] border-collapse">
          <thead>
            <tr className="border-b border-subtle text-t12 text-fg-muted">
              <th className={`${cell} font-medium`}>Call OI</th>
              <th className={`${cell} font-medium`}>Call</th>
              <th className="px-space-2 py-space-2 text-center text-t12 font-medium">Strike</th>
              <th className={`${cell} font-medium`}>Put</th>
              <th className={`${cell} font-medium`}>Put OI</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.strike} className="border-b border-subtle last:border-b-0" data-eod-strike={r.strike}>
                <td className={`${cell} text-fg-muted`}>{r.call?.oi == null ? EMPTY : formatCount(r.call.oi)}</td>
                <td className={`${cell} text-fg`}>{r.call ? formatPrice(r.call.close) : EMPTY}</td>
                <td className="whitespace-nowrap px-space-2 py-space-2 text-center font-tabular text-t13 font-semibold text-fg">{formatNumber(r.strike, 0)}</td>
                <td className={`${cell} text-fg`}>{r.put ? formatPrice(r.put.close) : EMPTY}</td>
                <td className={`${cell} text-fg-muted`}>{r.put?.oi == null ? EMPTY : formatCount(r.put.oi)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
