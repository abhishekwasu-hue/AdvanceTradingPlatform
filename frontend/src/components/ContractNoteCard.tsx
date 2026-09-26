import { FileUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { Card } from "../components/ui";
import type { ContractNoteIngest, ContractNoteSummary } from "../types";

/** Phase D4: upload a broker contract note / tradebook CSV so trades carry the broker's actual
 *  charges instead of the platform's estimate. Preview first, then apply. */
export default function ContractNoteCard({ onApplied }: { onApplied?: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [broker, setBroker] = useState("");
  const [notes, setNotes] = useState<ContractNoteSummary[]>([]);
  const [preview, setPreview] = useState<ContractNoteIngest | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    api.listContractNotes().then(setNotes).catch(() => setNotes([]));
  }
  useEffect(refresh, []);

  async function run(apply: boolean) {
    const file = fileRef.current?.files?.[0];
    if (!file) { setError("Choose the CSV your broker exported first."); return; }
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await api.uploadContractNote(file, broker, apply);
      if (apply) {
        setPreview(null);
        setMessage(`Applied: ${result.matched}/${result.lines} legs matched, ${result.trades_updated.length} trade(s) now carry actual charges (total ${result.total_charges.toFixed(2)}).`);
        if (fileRef.current) fileRef.current.value = "";
        refresh();
        onApplied?.();
      } else {
        setPreview(result);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Contract notes (actual charges)">
      <p className="text-xs text-muted mb-3">
        Charges and P&amp;L on a closed trade are the platform's estimate until you upload your broker's contract note or
        tradebook CSV for that day. Legs are matched to trades by broker order id first, then by symbol, side, quantity
        and date; matched trades get the broker's actual charges and a recomputed P&amp;L. Column names from Zerodha,
        Upstox and most other exports are recognised (symbol, buy/sell, quantity, price, order id, brokerage, STT,
        exchange charges, GST, SEBI fee, stamp duty or a total). Preview before applying.
      </p>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <input ref={fileRef} type="file" accept=".csv,text/csv" className="text-xs text-slate-300" disabled={busy} onChange={() => setPreview(null)} />
        <input className="rounded bg-panel2 border border-border px-2 py-1 text-sm w-32" placeholder="Broker (optional)" value={broker} onChange={(e) => setBroker(e.target.value)} />
        <button disabled={busy} onClick={() => run(false)} className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1 disabled:opacity-50">Preview</button>
        <button disabled={busy || !preview} onClick={() => run(true)} className="inline-flex items-center gap-1 rounded bg-brand/20 border border-brand/40 hover:bg-brand/30 text-slate-100 px-3 py-1 disabled:opacity-50">
          <FileUp className="w-3 h-3" /> Apply
        </button>
      </div>
      {error && <div className="mt-2 text-xs text-danger">{error}</div>}
      {message && <div className="mt-2 text-xs text-accent">{message}</div>}
      {preview && (
        <div className="mt-3 text-xs space-y-2">
          <div className="text-slate-200 font-semibold">Preview: {preview.matched}/{preview.lines} legs matched, {preview.trades_updated.length} trade(s) would change; total charges {preview.total_charges.toFixed(2)}</div>
          {preview.warnings.map((w) => <div key={w} className="text-warn">{w}</div>)}
          {preview.trades_updated.length > 0 && (
            <table className="w-full text-xs">
              <thead className="text-muted uppercase text-[10px] tracking-wide"><tr className="text-left"><th className="py-1 pr-3">Trade</th><th className="py-1 pr-3">Symbol</th><th className="py-1 pr-3">Legs</th><th className="py-1 pr-3">Charges est. → actual</th><th className="py-1 pr-3">P&amp;L est. → actual</th></tr></thead>
              <tbody>
                {preview.trades_updated.map((u) => (
                  <tr key={u.trade_id} className="border-t border-border">
                    <td className="py-1 pr-3">#{u.trade_id}</td><td className="py-1 pr-3">{u.symbol}</td><td className="py-1 pr-3">{u.legs}</td>
                    <td className="py-1 pr-3 font-tabular">{u.old_charges.toFixed(2)} → {u.new_charges.toFixed(2)}</td>
                    <td className="py-1 pr-3 font-tabular">{u.old_pnl?.toFixed(2) ?? "-"} → {u.new_pnl?.toFixed(2) ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {preview.unmatched.length > 0 && (
            <details><summary className="cursor-pointer text-muted">{preview.unmatched.length} unmatched leg(s)</summary>
              <ul className="mt-1 text-muted list-disc pl-4">{preview.unmatched.map((l) => <li key={l.row}>row {l.row}: {l.side} {l.quantity} {l.symbol} @ {l.price}{l.order_id ? ` (order ${l.order_id})` : ""}{l.date ? ` on ${l.date}` : ""}</li>)}</ul>
            </details>
          )}
        </div>
      )}
      {notes.length > 0 && (
        <div className="mt-3">
          <div className="text-[10px] uppercase tracking-wide text-muted mb-1">Uploaded</div>
          <ul className="text-xs text-slate-300 space-y-0.5">
            {notes.slice(0, 8).map((n) => (
              <li key={n.id}>{n.note_date ?? "?"} · {n.filename}{n.broker_name ? ` (${n.broker_name})` : ""} · {n.matched_lines}/{n.line_count} legs matched · {n.trades_updated} trade(s) · charges {n.total_charges.toFixed(2)}</li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
