import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { StaticIpOverview } from "../types";
import { Button } from "./primitives";
import { staticIpRows } from "./staticIps";
import { Card } from "./ui";

const BROKERS = ["upstox", "zerodha", "fyers", "dhan", "angelone", "shoonya"];

/** Part D4 (rule IN-SEBI.static_ip.registered): the IPs registered with each broker for API orders. */
export default function StaticIpCard() {
  const [data, setData] = useState<StaticIpOverview | null>(null);
  const [broker, setBroker] = useState(BROKERS[0]);
  const [role, setRole] = useState<"PRIMARY" | "BACKUP">("PRIMARY");
  const [ip, setIp] = useState("");
  const [registeredAt, setRegisteredAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = () => api.staticIps().then(setData).catch((e) => setMessage(String(e)));
  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy(true); setMessage(null);
    try {
      await api.setStaticIp({ broker_name: broker, role, ip: ip.trim(), registered_at: registeredAt ? new Date(registeredAt).toISOString() : null });
      setIp(""); setMessage(`Saved ${role.toLowerCase()} IP for ${broker}.`);
      await load();
    } catch (e) {
      setMessage(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  const rows = data ? staticIpRows(data) : [];
  return (
    <Card title="Static IP (SEBI)">
      <div className="space-y-3 text-sm">
        <p className="text-xs text-fg-muted">
          SEBI&apos;s retail-algo framework accepts API orders only from an IP the broker has on file for you. Register this
          server&apos;s public IP in each broker&apos;s API console, then record it here. A role can change
          {data ? ` ${data.max_changes_per_week} time(s)` : ""} in 7 days.
        </p>
        <div className="text-xs">
          This server&apos;s egress IP: <span className="font-mono">{data?.server_egress_ip ?? "not configured (SERVER_EGRESS_IP)"}</span>
          {data?.required_for_live ? " - LIVE entries need it registered." : " - not enforced yet (STATIC_IP_REQUIRED_FOR_LIVE off)."}
        </div>
        {rows.length > 0 && (
          <table className="w-full text-xs">
            <thead><tr className="text-left text-fg-muted"><th>Broker</th><th>Primary</th><th>Backup</th><th>Server IP</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.broker} className="border-t border-border">
                  <td className="py-1">{r.broker}</td>
                  <td className="font-mono">{r.primary ?? "-"}</td>
                  <td className="font-mono">{r.backup ?? "-"}</td>
                  <td>{r.serverIpRegistered === null ? "-" : r.serverIpRegistered ? "registered" : "not registered"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data?.warnings.map((w) => <p key={w} role="alert" className="text-xs text-warn">{w}</p>)}
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs">Broker
            <select className="ml-1 rounded border border-border bg-surface-2 px-2 py-1" value={broker} onChange={(e) => setBroker(e.target.value)}>
              {BROKERS.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </label>
          <label className="text-xs">Role
            <select className="ml-1 rounded border border-border bg-surface-2 px-2 py-1" value={role} onChange={(e) => setRole(e.target.value as "PRIMARY" | "BACKUP")}>
              <option value="PRIMARY">Primary</option><option value="BACKUP">Backup</option>
            </select>
          </label>
          <label className="text-xs">IP
            <input className="ml-1 w-40 rounded border border-border bg-surface-2 px-2 py-1 font-mono" value={ip} onChange={(e) => setIp(e.target.value)} placeholder="203.0.113.10" />
          </label>
          <label className="text-xs">Registered on
            <input type="date" className="ml-1 rounded border border-border bg-surface-2 px-2 py-1" value={registeredAt} onChange={(e) => setRegisteredAt(e.target.value)} />
          </label>
          <Button size="sm" onClick={save} disabled={busy || !ip.trim()}>Save</Button>
        </div>
        {message && <p className="text-xs text-fg-muted">{message}</p>}
      </div>
    </Card>
  );
}
