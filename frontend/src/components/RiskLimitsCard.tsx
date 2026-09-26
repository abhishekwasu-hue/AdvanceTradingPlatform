import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { RiskEvent, RiskLimit, RiskLimitType, RiskScope } from "../types";
import { Card } from "./ui";

const SCOPES: { value: RiskScope; label: string; needsId: string | null }[] = [
  { value: "TENANT", label: "Organisation", needsId: null },
  { value: "USER", label: "User", needsId: "user id" },
  { value: "ACCOUNT", label: "Broker account", needsId: "account id" },
  { value: "STRATEGY", label: "Strategy", needsId: "strategy id" },
  { value: "INSTRUMENT", label: "Instrument", needsId: "symbol" },
];

const TYPES: { value: RiskLimitType; label: string; unit: string }[] = [
  { value: "MAX_DAILY_LOSS", label: "Max daily loss", unit: "₹ realised today; breach stops the organisation" },
  { value: "MAX_STRATEGY_LOSS", label: "Max strategy loss", unit: "₹ realised today per strategy; breach stops that strategy" },
  { value: "MAX_LOSS_PER_TRADE", label: "Max loss per trade", unit: "₹ at the stop (spreads: max loss)" },
  { value: "MAX_ORDER_VALUE", label: "Max order value", unit: "₹ entry × quantity" },
  { value: "MAX_POSITION_QUANTITY", label: "Max quantity per order", unit: "units" },
  { value: "MAX_OPEN_POSITIONS", label: "Max open positions", unit: "count, including the new one" },
  { value: "MAX_TRADES_PER_DAY", label: "Max trades per day", unit: "count" },
  { value: "MAX_CAPITAL_ALLOCATION_PCT", label: "Max capital per order", unit: "% of capital" },
];

/**
 * Phase I1: the risk hierarchy. Limits at every scope are checked together for each order,
 * the strictest wins, and every check is written to the risk-events log shown below.
 */
export default function RiskLimitsCard({ canEdit }: { canEdit: boolean }) {
  const [limits, setLimits] = useState<RiskLimit[]>([]);
  const [events, setEvents] = useState<RiskEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<RiskScope>("TENANT");
  const [scopeId, setScopeId] = useState("");
  const [type, setType] = useState<RiskLimitType>("MAX_DAILY_LOSS");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  function refresh() {
    api.listRiskLimits().then(setLimits).catch((e) => setError(String(e)));
    api.listRiskEvents({ limit: 50 }).then(setEvents).catch(() => setEvents([]));
  }
  useEffect(refresh, []);

  async function add() {
    setBusy(true); setError(null);
    try {
      await api.upsertRiskLimit({ scope, scope_id: scopeId, limit_type: type, limit_value: Number(value) });
      setValue("");
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    try {
      await api.deleteRiskLimit(id);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  const needsId = SCOPES.find((s) => s.value === scope)?.needsId;

  return (
    <>
      <Card title={`Risk limits (${limits.length})`}>
        <p className="text-xs text-muted mb-3">
          Every order is checked against all limits that apply to it - organisation, user, broker account, strategy and
          instrument - and the strictest of each kind wins. A breached loss limit also engages the matching kill switch
          so the next signal is refused at the door. Warnings fire at 80% of a limit.
        </p>
        {canEdit && (
          <div className="grid sm:grid-cols-5 gap-2 items-end text-xs mb-3">
            <div>
              <label className="block text-[10px] text-muted mb-0.5">Scope</label>
              <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5" value={scope} onChange={(e) => setScope(e.target.value as RiskScope)}>
                {SCOPES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[10px] text-muted mb-0.5">{needsId ?? "Scope id"}</label>
              <input className="w-full rounded bg-panel2 border border-border px-2 py-1.5 disabled:opacity-40" disabled={!needsId} placeholder={needsId ?? "-"} value={scopeId} onChange={(e) => setScopeId(e.target.value)} />
            </div>
            <div>
              <label className="block text-[10px] text-muted mb-0.5">Limit</label>
              <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5" value={type} onChange={(e) => setType(e.target.value as RiskLimitType)}>
                {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[10px] text-muted mb-0.5">Value <span className="text-muted/70">({TYPES.find((t) => t.value === type)?.unit})</span></label>
              <input type="number" step="any" className="w-full rounded bg-panel2 border border-border px-2 py-1.5" value={value} onChange={(e) => setValue(e.target.value)} />
            </div>
            <button onClick={add} disabled={busy || !value} className="flex items-center justify-center gap-1 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 disabled:opacity-50">
              <Plus size={12} /> Set limit
            </button>
          </div>
        )}
        {error && <div className="text-xs text-danger mb-2">{error}</div>}
        {limits.length === 0 ? (
          <div className="text-xs text-muted">No limits yet - the risk settings above are the only guard.</div>
        ) : (
          <table className="w-full text-xs">
            <thead className="text-muted uppercase text-[10px] tracking-wide">
              <tr className="text-left"><th className="py-1 pr-3">Scope</th><th className="py-1 pr-3">Limit</th><th className="py-1 pr-3">Value</th><th className="py-1 pr-3">Note</th><th className="py-1 pr-3"></th></tr>
            </thead>
            <tbody>
              {limits.map((l) => (
                <tr key={l.id} className="border-t border-border">
                  <td className="py-1 pr-3 text-slate-200">{l.scope.toLowerCase()}{l.scope_id ? ` · ${l.scope_id}` : ""}</td>
                  <td className="py-1 pr-3">{TYPES.find((t) => t.value === l.limit_type)?.label ?? l.limit_type}</td>
                  <td className="py-1 pr-3 font-mono">{l.limit_value.toLocaleString()}</td>
                  <td className="py-1 pr-3 text-muted">{l.note ?? ""}{!l.enabled ? " (disabled)" : ""}{l.scope === "GLOBAL" ? " (platform)" : ""}</td>
                  <td className="py-1 pr-3 text-right">
                    {canEdit && l.scope !== "GLOBAL" && (
                      <button onClick={() => remove(l.id)} className="text-muted hover:text-danger" title="Remove"><Trash2 size={12} /></button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title={`Risk events (last ${events.length})`}>
        {events.length === 0 ? (
          <div className="text-xs text-muted">No checks recorded yet - events appear as orders are evaluated.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted uppercase text-[10px] tracking-wide">
                <tr className="text-left"><th className="py-1 pr-3">Time</th><th className="py-1 pr-3">Result</th><th className="py-1 pr-3">Strategy</th><th className="py-1 pr-3">Detail</th></tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id} className="border-t border-border">
                    <td className="py-1 pr-3 text-muted whitespace-nowrap">{e.created_at ? new Date(e.created_at).toLocaleString() : "-"}</td>
                    <td className={`py-1 pr-3 font-semibold ${e.status === "BLOCK" ? "text-danger" : e.status === "WARN" ? "text-warn" : "text-accent"}`}>{e.status}{e.action !== "ALLOW" && e.action !== "BLOCK_ORDER" && e.action !== "WARN" ? ` · ${e.action.toLowerCase().replace("_", " ")}` : ""}</td>
                    <td className="py-1 pr-3 text-slate-300">{e.strategy_id ?? "-"}{e.symbol ? ` · ${e.symbol}` : ""}</td>
                    <td className="py-1 pr-3 text-muted">{e.reason}{e.order_id ? ` (order #${e.order_id})` : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
