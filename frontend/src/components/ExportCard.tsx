import { Download } from "lucide-react";
import { useState } from "react";
import { api, type ExportDataset } from "../api/client";
import { Card } from "../components/ui";

const DATASETS: { id: ExportDataset; label: string; hint: string }[] = [
  { id: "audit-logs", label: "Audit trail", hint: "hash-chained; includes prev_hash/hash and the chain verdict" },
  { id: "orders", label: "Orders", hint: "every attempt, with status, broker id and SEBI algo tag" },
  { id: "trades", label: "Trades", hint: "fills with exit, P&L and charges" },
  { id: "login-events", label: "Login attempts", hint: "successes and failures with IP and device" },
];

/** Compliance export controls (Phase D2). `scope` decides which endpoint family is used:
 *  the owner's own organisation, or the platform-wide admin export (optionally one tenant). */
export default function ExportCard({ scope, tenantId }: { scope: "tenant" | "platform"; tenantId?: number }) {
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const [from, setFrom] = useState(monthAgo);
  const [to, setTo] = useState(today);
  const [busy, setBusy] = useState<string | null>(null);
  const [last, setLast] = useState<{ name: string; sha: string; rows: string; chain?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function download(dataset: ExportDataset, format: "csv" | "json") {
    setBusy(`${dataset}:${format}`); setError(null);
    try {
      const result = await api.downloadExport(dataset, format, { from, to, scope, tenantId });
      const url = URL.createObjectURL(result.blob);
      const a = document.createElement("a");
      a.href = url; a.download = result.filename; a.click();
      URL.revokeObjectURL(url);
      setLast({ name: result.filename, sha: result.sha256, rows: result.rows, chain: result.chainIntact });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title={scope === "platform" ? (tenantId ? `Export records: tenant #${tenantId}` : "Export platform records") : "Export records (compliance)"}>
      <p className="text-xs text-muted mb-3">
        Downloads a CSV or JSON file of {scope === "platform" ? "platform-wide" : "your organisation's"} records for the date range
        (inclusive, UTC). Each file carries its SHA-256 and a manifest; the export itself is written to the audit trail.
        Keep these for the regulatory retention period (SEBI: five years for order and trade records).
      </p>
      <div className="flex flex-wrap items-end gap-3 mb-3 text-xs">
        <label className="block">From<input type="date" className="mt-1 block rounded bg-panel2 border border-border px-2 py-1 text-sm" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="block">To<input type="date" className="mt-1 block rounded bg-panel2 border border-border px-2 py-1 text-sm" value={to} onChange={(e) => setTo(e.target.value)} /></label>
      </div>
      <div className="grid sm:grid-cols-2 gap-2">
        {DATASETS.map((d) => (
          <div key={d.id} className="rounded-lg border border-border bg-panel2/40 p-3 flex items-center justify-between gap-2">
            <div>
              <div className="text-sm font-semibold text-slate-100">{d.label}</div>
              <div className="text-[11px] text-muted">{d.hint}</div>
            </div>
            <div className="flex gap-1">
              {(["csv", "json"] as const).map((f) => (
                <button key={f} disabled={busy !== null} onClick={() => download(d.id, f)}
                  className="inline-flex items-center gap-1 rounded border border-border hover:bg-panel2 text-slate-200 px-2 py-1 text-xs disabled:opacity-50 uppercase">
                  <Download className="w-3 h-3" /> {busy === `${d.id}:${f}` ? "..." : f}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      {error && <div className="mt-2 text-xs text-danger">{error}</div>}
      {last && (
        <div className="mt-3 text-[11px] text-muted font-mono break-all">
          <div>{last.name} - {last.rows} rows{last.chain ? ` - audit chain intact: ${last.chain}` : ""}</div>
          <div>sha256 {last.sha}</div>
        </div>
      )}
    </Card>
  );
}
