import { Clock, Database, History, Layers } from "lucide-react";
import { useEffect, useState } from "react";
import { Panel, Pill } from "./AdminSectionsV2";
import { ago, consoleApi, type ChainHistoryRow, type ChainHistoryStatus } from "./consoleApi";
import { SkeletonRows } from "../components/primitives";

/**
 * TM 9.4: the admin card of the owner's chain history (Admin › System). What is being recorded, today's chains per
 * underlying, the last snapshot and today's gaps. Read-only; counts and times only, never a price.
 */

const TH = "py-space-1 pr-space-3 text-left font-medium text-fg-muted";
const TD = "py-space-1 pr-space-3 align-top";

export function chainHistoryState(s: ChainHistoryStatus): { tone: "ok" | "warn" | "off"; label: string } {
  if (!s.owner_configured) return { tone: "off", label: "Not set up" };
  if (!s.flag_on) return { tone: "off", label: "Off" };
  if (!s.owner_session) return { tone: "warn", label: "Waiting for the owner's login" };
  return { tone: "ok", label: "Recording" };
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

const hhmm = (iso: string) => iso.slice(11, 16);

export function gapText(row: ChainHistoryRow): string {
  if (!row.trading_day) return "No session today";
  if (row.slots_expected === 0) return "Session not started";
  if (row.missing_slots === 0) return "None";
  const shown = row.gaps.slice(-3).map((g) => `${hhmm(g.from)}–${hhmm(g.to)}`).join(", ");
  return `${row.missing_slots} missing · ${shown}`;
}

export function ChainHistoryView({ data }: { data: ChainHistoryStatus }) {
  const state = chainHistoryState(data);
  const today = data.underlyings.reduce((a, r) => a + r.chains_today, 0);
  const bytesToday = data.underlyings.reduce((a, r) => a + r.stored_bytes_today, 0);
  const last = data.underlyings.map((r) => r.last_captured_at).filter((t): t is string => !!t).sort().pop() ?? null;
  return (
    <div data-chain-history={state.label}>
      <div className="flex flex-wrap items-center gap-space-2">
        <Pill tone={state.tone}>{state.label}</Pill>
        {data.owner_session && <span className="text-t12 text-fg-muted">through the owner's own <span className="capitalize">{data.owner_session.broker}</span> session</span>}
      </div>
      {data.problem && <p className="mt-space-2 text-t14 text-fg" data-chain-history-problem>{data.problem}</p>}
      <dl className="mt-space-3 grid grid-cols-2 gap-space-3 lg:grid-cols-4">
        <Stat icon={Clock} label="Interval" value={`Every ${data.slot_minutes} min`} sub={`${data.expiries_per_underlying} nearest expiries · market hours only`} />
        <Stat icon={Layers} label="Today" value={`${today.toLocaleString("en-IN")} chains`} sub={formatBytes(bytesToday)} />
        <Stat icon={History} label="Last snapshot" value={ago(last)} sub={last ? `${new Date(last).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" })} IST` : "-"} />
        <Stat icon={Database} label="Stored" value={`${(data.totals?.chains ?? 0).toLocaleString("en-IN")} chains`}
          sub={data.totals ? `${data.totals.days} days · ${formatBytes(data.totals.stored_bytes)}` : "-"} />
      </dl>
      <div className="-mx-space-1 mt-space-4 overflow-x-auto px-space-1">
        <table className="w-full text-t12">
          <thead><tr><th className={TH}>Index / stock</th><th className={TH}>Chains today</th><th className={TH}>Slots</th><th className={`${TH} hidden sm:table-cell`}>Last</th><th className={TH}>Gaps today</th></tr></thead>
          <tbody>
            {data.underlyings.map((r) => (
              <tr key={r.underlying} data-chain-row={r.underlying} className="border-t border-subtle">
                <td className={`${TD} font-medium text-fg`}>{r.underlying}</td>
                <td className={`${TD} font-tabular text-fg`}>{(r.chains_today ?? 0).toLocaleString("en-IN")}</td>
                <td className={`${TD} whitespace-nowrap font-tabular text-fg-muted`}>{r.slots_expected ? `${r.slots_today} / ${r.slots_expected}` : r.slots_today ?? 0}</td>
                <td className={`${TD} hidden whitespace-nowrap text-fg-muted sm:table-cell`}>{ago(r.last_captured_at)}</td>
                <td className={`${TD} min-w-32 ${r.missing_slots ? "text-warn" : "text-fg-muted"}`}>{gapText(r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ icon: Icon, label, value, sub }: { icon: typeof Clock; label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-control border border-subtle p-space-3">
      <dt className="flex items-center gap-space-2 text-t12 text-fg-muted"><Icon size={14} aria-hidden />{label}</dt>
      <dd className="mt-space-1 text-t14 font-semibold text-fg">{value}</dd>
      {sub && <dd className="text-t12 text-fg-muted">{sub}</dd>}
    </div>
  );
}

export default function ChainHistoryCard() {
  const [data, setData] = useState<ChainHistoryStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    consoleApi.chainHistory().then((d) => { if (live) setData(d); }, (e) => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, []);
  return (
    <Panel title="Chain history" id="chain-history"
      note="Option chains (every strike: price, OI, IV, Greeks) recorded through the owner's own broker session into the owner's private store.">
      {data ? <ChainHistoryView data={data} /> : error ? <p className="text-t12 text-down">Could not load: {error}</p> : <SkeletonRows rows={2} label="Loading" />}
    </Panel>
  );
}
