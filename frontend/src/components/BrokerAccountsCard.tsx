import { RefreshCw, Star } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BrokerAccount } from "../types";
import { Card } from "./ui";

/**
 * Phase I2: the trading accounts behind the stored broker credentials - balance, margin and
 * P&L pulled from the broker on demand, enable/disable (a disabled account refuses new LIVE
 * entries), and which account a deployment on that broker uses when it names none.
 */
export default function BrokerAccountsCard({ refreshKey = 0 }: { refreshKey?: number }) {
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  function refresh() {
    api.listAccounts().then(setAccounts).catch((e) => setError(String(e)));
  }
  useEffect(refresh, [refreshKey]);

  async function act(id: number, fn: () => Promise<BrokerAccount>) {
    setBusy(id); setError(null);
    try {
      await fn();
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  const money = (v: number | null) => (v == null ? "-" : v.toLocaleString("en-IN", { maximumFractionDigits: 0 }));

  return (
    <Card title={`Broker accounts (${accounts.length})`}>
      <p className="text-xs text-muted mb-3">
        One account per stored credential; store a second credential for the same broker with a different account label
        to trade two accounts. LIVE deployments route to the account they name, else the broker's default (★).
        Disabling an account stops new LIVE entries to it; exits still run.
      </p>
      {error && <div className="text-xs text-danger mb-2">{error}</div>}
      {accounts.length === 0 ? (
        <div className="text-sm text-muted">No accounts yet - store broker credentials first.</div>
      ) : (
        <div className="space-y-1.5">
          {accounts.map((a) => (
            <div key={a.id} className={`rounded border px-3 py-2 text-xs ${a.status === "ACTIVE" ? "border-border" : "border-warn/40 bg-warn/[0.05]"}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  {a.is_default && <Star size={12} className="text-warn" />}
                  <span className="font-semibold text-slate-200 capitalize">{a.display_name ?? `${a.broker_name} ${a.account_label}`}</span>
                  <span className="text-muted">#{a.id} · {a.broker_name}/{a.account_label}{a.broker_account_identifier ? ` · ${a.broker_account_identifier}` : ""}</span>
                  <span className={`rounded border px-1.5 py-0.5 font-semibold ${a.status === "ACTIVE" ? "border-accent/40 text-accent" : "border-warn/40 text-warn"}`}>{a.status}</span>
                  <span className="text-muted">token {a.token_status ?? "?"}</span>
                </div>
                <div className="flex items-center gap-3">
                  <button onClick={() => act(a.id, () => api.syncAccount(a.id))} disabled={busy === a.id} className="flex items-center gap-1 text-brand hover:underline disabled:opacity-50">
                    <RefreshCw size={11} className={busy === a.id ? "animate-spin" : ""} /> Sync
                  </button>
                  {!a.is_default && <button onClick={() => act(a.id, () => api.setDefaultAccount(a.id))} className="text-muted hover:underline">Make default</button>}
                  <button onClick={() => act(a.id, () => api.setAccountStatus(a.id, a.status !== "ACTIVE"))} className={`${a.status === "ACTIVE" ? "text-warn" : "text-accent"} hover:underline`}>
                    {a.status === "ACTIVE" ? "Disable" : "Enable"}
                  </button>
                </div>
              </div>
              <div className="mt-1 flex flex-wrap gap-4 text-muted">
                <span>balance <span className="text-slate-200">{money(a.available_balance)}</span></span>
                <span>margin used <span className="text-slate-200">{money(a.used_margin)}</span></span>
                <span>realised <span className={a.realized_pnl != null && a.realized_pnl < 0 ? "text-danger" : "text-slate-200"}>{money(a.realized_pnl)}</span></span>
                <span>unrealised <span className={a.unrealized_pnl != null && a.unrealized_pnl < 0 ? "text-danger" : "text-slate-200"}>{money(a.unrealized_pnl)}</span></span>
                <span>{a.last_sync_at ? `synced ${new Date(a.last_sync_at).toLocaleString()}` : "never synced"}{a.last_sync_error ? ` · ${a.last_sync_error}` : ""}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
