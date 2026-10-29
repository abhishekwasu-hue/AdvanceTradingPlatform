import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { CardV2, EmptyStateV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import type { CostMeter, CostPeriod } from "../types";
import { Chip } from "../ui/Chip";
import { Details } from "../ui/Details";
import { EMPTY, formatDateIST, formatINR, formatNumber } from "../ui/format";
import { Signed } from "../ui/Signed";

/**
 * AUD-T7 (owner audit 28 Oct; flags `ui_v2` + `cost_meter`): the cost-of-trading meter on My trades - what charges took
 * from the user's own closed trades this financial year and this month. Paper and live are two separate blocks, never
 * added together. Numbers first; the method sits in the one Details. Read-only.
 */

const MODE_LABEL: Record<string, string> = { PAPER: "Paper trades", LIVE: "Live trades" };

/** A fraction (0.37) as "37%"; "-" when there is no gross profit to take a share of. */
export function sharePct(v: number | null): string {
  return v == null ? EMPTY : `${formatNumber(v * 100, 1)}%`;
}

/** Where the period's charges come from, in plain words. */
export function sourceLabel(p: CostPeriod): string {
  if (p.trades === 0) return "";
  if (p.from_contract_notes === p.trades) return "From contract notes";
  if (p.from_contract_notes === 0) return "Estimated";
  return `${p.from_contract_notes} of ${p.trades} from contract notes`;
}

type Row = { label: string; key: string; cell: (p: CostPeriod) => ReactNode };
const ROWS: readonly Row[] = [
  { label: "Gross P&L", key: "gross", cell: (p) => <Signed value={p.gross} arrow={false} /> },
  { label: "Charges", key: "charges", cell: (p) => formatINR(p.charges) },
  { label: "Net P&L", key: "net", cell: (p) => <Signed value={p.net} arrow={false} /> },
  { label: "Charges of gross profit", key: "share", cell: (p) => sharePct(p.charges_share_of_gross) },
  { label: "Charges per trade", key: "per-trade", cell: (p) => (p.charges_per_trade == null ? EMPTY : formatINR(p.charges_per_trade)) },
  { label: "Closed trades", key: "trades", cell: (p) => String(p.trades) },
];

function ModeBlock({ mode, year, month }: CostMeter["modes"][number]) {
  const source = sourceLabel(year);
  return (
    <div data-cost-mode={mode} className="space-y-space-2">
      <div className="flex flex-wrap items-center gap-space-2">
        <h3 className="text-t14 font-semibold text-fg">{MODE_LABEL[mode] ?? mode}</h3>
        {source && <Chip tone="neutral" variant="meaning" name="cost-source">{source}</Chip>}
      </div>
      <table className="w-full text-t13">
        <thead>
          <tr className="text-fg-muted">
            <th scope="col" className="py-space-1 text-left font-normal"><span className="sr-only">Figure</span></th>
            <th scope="col" className="py-space-1 text-right font-normal">This year</th>
            <th scope="col" className="py-space-1 text-right font-normal">This month</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-subtle">
          {ROWS.map((r) => (
            <tr key={r.key} data-cost-row={r.key}>
              <th scope="row" className="py-space-2 text-left font-normal text-fg-muted">{r.label}</th>
              <td className="py-space-2 text-right font-tabular font-medium text-fg">{r.cell(year)}</td>
              <td className="py-space-2 text-right font-tabular font-medium text-fg">{month.trades === 0 && r.key !== "trades" ? EMPTY : r.cell(month)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The per-trade figures of one mode, this year, for the Details (each mode on its own line, never added together). */
function PerTrade({ mode, p }: { mode: string; p: CostPeriod }) {
  const name = MODE_LABEL[mode] ?? mode;
  return (
    <>
      {p.median_trade_share != null && <p data-cost-median={mode}>{`${name}: on a typical trade with a gross profit, charges took ${sharePct(p.median_trade_share)} of it.`}</p>}
      {p.charges_took_whole_profit > 0 && <p data-cost-whole={mode}>{`${name}: ${p.charges_took_whole_profit} of ${p.trades_with_gross_profit} trades with a gross profit lost all of it to charges.`}</p>}
    </>
  );
}

export function CostMeterView({ data }: { data: CostMeter }) {
  if (data.modes.length === 0) return <EmptyStateV2 text="Your charges show here once a trade closes this year." />;
  return (
    <div className="space-y-space-4">
      <p className="text-t13 text-fg-muted">Charges against your gross P&L, from your own closed trades.</p>
      {data.modes.map((m) => <ModeBlock key={m.mode} {...m} />)}
      <Details id="positions.cost-meter">
        <p>{`This year runs from ${formatDateIST(data.year_start)} to 31 March (IST); this month from ${formatDateIST(data.month_start)}.`}</p>
        <p>Gross P&L is before charges; net P&L is after them. Paper and live trades are counted apart.</p>
        <p>Charges come from your uploaded contract notes where a trade matched one; otherwise they are ATP's estimate.</p>
        <p>Charges of gross profit is shown only when the gross P&L is a profit.</p>
        {data.modes.map(({ mode, year }) => <PerTrade key={mode} mode={mode} p={year} />)}
      </Details>
    </div>
  );
}

const LOAD = () => api.costMeter();

export default function CostMeterV2({ load = LOAD }: { load?: () => Promise<CostMeter> }) {
  const [state, setState] = useState<{ status: "loading" } | { status: "error" } | { status: "ok"; data: CostMeter }>({ status: "loading" });
  useEffect(() => {
    let live = true;
    load().then((data) => { if (live) setState({ status: "ok", data }); }, () => { if (live) setState({ status: "error" }); });
    return () => { live = false; };
  }, [load]);
  return (
    <CardV2 title="Cost of trading" testId="cost-meter">
      {state.status === "loading" ? <LoadingRowsV2 /> : state.status === "error" ? <LoadFailedV2 what="your trading costs" /> : <CostMeterView data={state.data} />}
    </CardV2>
  );
}
