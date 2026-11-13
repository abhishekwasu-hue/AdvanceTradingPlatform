import { useEffect, useState } from "react";
import { api } from "../api/client";
import { eodApi } from "../api/eod";
import { useMarketStatus } from "../chart/useMarketStatus";
import { cx } from "../components/primitives/cx";
import { EMPTY, formatDateIST, formatNumber, formatTimeIST } from "../ui/format";
import { Details } from "../ui/Details";
import { useNoBroker } from "../ui/noBroker";
import { strikeText } from "./oiTab";
import {
  emptyColumn, eodColumn, formatLakhs, formatLakhsChange, historyApi, liveColumn, pickExpiries, storedColumn, storedIsCurrent, type ExpiryColumn, type ExpirySummary,
} from "./history";

/**
 * M96 N1: the near weeklies and the monthly side by side - total call / put OI, OI change, PCR (OI), max pain, ATM IV
 * and days to expiry per expiry. Each column comes from the user's own data, in this order: his stored reading of that
 * expiry when it is current (no broker read), else ONE live chain read of that expiry through his own broker per
 * refresh (the same read as the chain table, its cache and rate budget), else - with no broker - the last close from
 * NSE's F&O day file, labelled "Last close". Refreshed once a minute while the market is open. Information only.
 */
const CARD = "min-w-0 rounded-panel border border-subtle bg-surface-1 p-space-3 md:p-space-4";
const REFRESH_MS = 60_000;
const tone = (v: number | null) => (v === null || v === 0 ? "" : v > 0 ? "text-up" : "text-down");

const SOURCE: Record<string, string> = { stored: "Recorded", live: "Live", last_close: "Last close" };

async function readColumns(underlying: string, noBroker: boolean, marketOpen: boolean): Promise<ExpiryColumn[]> {
  const list = pickExpiries((await api.optionChainExpiries(underlying)).expiries);
  if (noBroker) {
    return Promise.all(list.map(async (e) => {
      try { return eodColumn(e, await eodApi.chain(underlying, e.date)); } catch { return emptyColumn(e); }
    }));
  }
  const stored: ExpirySummary[] = await historyApi.expiries(underlying).then((r) => r.expiries, () => []);
  const out: ExpiryColumn[] = [];
  for (const e of list) {                       // one at a time: one chain read per expiry per refresh
    const own = stored.find((s) => s.expiry === e.date);
    if (own && storedIsCurrent(own, marketOpen)) { out.push(storedColumn(e, own)); continue; }
    try { out.push(liveColumn(e, await api.optionChainLive(underlying, e.date))); }
    catch { out.push(own ? storedColumn(e, own) : emptyColumn(e)); }
  }
  return out;
}

export default function MultiExpiryV2({ underlying }: { underlying: string }) {
  const noBroker = useNoBroker();
  const market = useMarketStatus();
  const open = market.status?.sessions?.NSE?.is_open === true;
  const [cols, setCols] = useState<ExpiryColumn[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    readColumns(underlying, noBroker, open).then((c) => { if (live) { setCols(c); setFailed(false); } }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [underlying, noBroker, open, tick]);
  useEffect(() => { setCols(null); }, [underlying]);
  useEffect(() => {
    if (!open) return undefined;
    const id = window.setInterval(() => setTick((t) => t + 1), REFRESH_MS);
    return () => window.clearInterval(id);
  }, [open]);

  return (
    <section aria-label={`${underlying} expiries side by side`} className={CARD} data-multi-expiry={underlying}>
      <h3 className="mb-space-2 text-t15 font-semibold text-fg">Expiries side by side</h3>
      {failed && !cols && <p className="text-t13 text-fg-muted">The expiries could not be read right now.</p>}
      {!failed && !cols && <p className="text-t13 text-fg-muted">Reading the expiries…</p>}
      {cols && cols.length === 0 && <p className="text-t13 text-fg-muted">No expiries are listed for {underlying}.</p>}
      {cols && cols.length > 0 && <ExpiryTable cols={cols} />}
    </section>
  );
}

/** The table for columns already in hand (pure) - tests render it directly. */
export function ExpiryTable({ cols }: { cols: readonly ExpiryColumn[] }) {
  const rows: { key: string; label: string; cell: (c: ExpiryColumn) => { text: string; className?: string } }[] = [
    { key: "call_oi", label: "Call OI", cell: (c) => ({ text: formatLakhs(c.totalCall, { unit: false }) }) },
    { key: "put_oi", label: "Put OI", cell: (c) => ({ text: formatLakhs(c.totalPut, { unit: false }) }) },
    { key: "call_chg", label: "Call OI chg", cell: (c) => ({ text: formatLakhsChange(c.callChange, false), className: tone(c.callChange) }) },
    { key: "put_chg", label: "Put OI chg", cell: (c) => ({ text: formatLakhsChange(c.putChange, false), className: tone(c.putChange) }) },
    { key: "pcr", label: "PCR (OI)", cell: (c) => ({ text: c.pcr === null ? EMPTY : formatNumber(c.pcr, 2) }) },
    { key: "max_pain", label: "Max pain", cell: (c) => ({ text: c.maxPain === null ? EMPTY : strikeText(c.maxPain) }) },
    { key: "atm_iv", label: "ATM IV", cell: (c) => ({ text: c.atmIv === null ? EMPTY : `${formatNumber(c.atmIv, 1)}%` }) },
    { key: "dte", label: "Days left", cell: (c) => ({ text: c.dte === null ? EMPTY : String(c.dte) }) },
  ];
  const cell = "whitespace-nowrap px-space-2 py-space-1 text-right font-tabular";
  return (
    <div className="space-y-space-1">
      {/* Owner 13 Nov: the unit once above the table; the OI cells carry the bare figures. */}
      <p className="text-right text-t12 text-fg-muted" data-oi-unit>(OI in lakhs)</p>
      <div className="overflow-x-auto rounded-panel border border-subtle">
        <table className="w-full min-w-[20rem] border-collapse text-t13" data-multi-expiry-table>
          <thead>
            <tr className="border-b border-subtle text-t12 text-fg-muted">
              <th className="sticky left-0 bg-surface-1 px-space-2 py-space-1 text-left font-medium">Expiry</th>
              {cols.map((c) => (
                <th key={c.expiry} className={cx(cell, "font-medium text-fg")} data-multi-expiry-col={c.expiry}>
                  {formatDateIST(c.expiry, { year: false })}
                  <span className="block text-t12 font-normal text-fg-muted">{c.kind === "monthly" ? "Monthly" : "Weekly"}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-b border-subtle last:border-b-0" data-multi-expiry-row={r.key}>
                <th scope="row" className="sticky left-0 whitespace-nowrap bg-surface-1 px-space-2 py-space-1 text-left text-t12 font-medium text-fg-muted">{r.label}</th>
                {cols.map((c) => { const v = r.cell(c); return <td key={c.expiry} className={cx(cell, v.className || "text-fg")} data-tone={v.className || "none"}>{v.text}</td>; })}
              </tr>
            ))}
            <tr data-multi-expiry-row="source">
              <th scope="row" className="sticky left-0 whitespace-nowrap bg-surface-1 px-space-2 py-space-1 text-left text-t12 font-medium text-fg-muted">From</th>
              {cols.map((c) => (
                <td key={c.expiry} className={cx(cell, "text-t12 text-fg-muted")} data-source={c.source ?? "none"}>
                  {c.source ? SOURCE[c.source] : "Not available"}
                  {c.asOf && <span className="block">{c.source === "last_close" ? formatDateIST(c.asOf, { year: false }) : formatTimeIST(c.asOf)}</span>}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <Details id="optionChain.multiExpiry.about" label="How these are counted">
        <p className="text-t12 text-fg-muted">Whole chain per expiry. Live: your broker; recorded: your stored readings. OI change: since the day's first reading (recorded), or as the chain table shows it (live).</p>
      </Details>
    </div>
  );
}
