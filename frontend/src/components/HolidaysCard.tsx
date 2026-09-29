import { useEffect, useState } from "react";
import { CalendarOff, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card } from "../components/ui";
import type { MarketHoliday } from "../types";

/** Phase AD: the exchange holiday calendar the worker treats as closed days. Shared reference
 * data: everyone can read it, only a SUPER_ADMIN edits it. Paste the annual NSE circular as
 * "YYYY-MM-DD description" lines to load a year in one go. */
export default function HolidaysCard() {
  const { user } = useAuth();
  const isAdmin = user?.role === "SUPER_ADMIN";
  const [year, setYear] = useState(new Date().getFullYear());
  const [exchange, setExchange] = useState("NSE");
  const [rows, setRows] = useState<MarketHoliday[]>([]);
  const [bulk, setBulk] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api.marketHolidays(year, exchange).then(setRows).catch((e) => setError(String(e)));
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [year, exchange]);

  async function addBulk() {
    const lines = bulk.split("\n").map((l) => l.trim()).filter(Boolean);
    if (!lines.length) return;
    setBusy(true); setError(null); setMessage(null);
    let added = 0; const failed: string[] = [];
    for (const line of lines) {
      const m = line.match(/^(\d{4}-\d{2}-\d{2})\s*(.*)$/);
      if (!m) { failed.push(`${line}: expected YYYY-MM-DD description`); continue; }
      try { await api.addMarketHoliday({ exchange, holiday_date: m[1], description: m[2] ?? "" }); added += 1; }
      catch (e) { failed.push(`${m[1]}: ${String(e)}`); }
    }
    setBusy(false); setBulk(failed.length ? failed.map((f) => f.split(":")[0]).join("\n") : "");
    setMessage(`${added} added${failed.length ? `, ${failed.length} skipped (${failed[0]})` : ""}`);
    load();
  }

  async function remove(row: MarketHoliday) {
    setBusy(true); setError(null);
    try { await api.deleteMarketHoliday(row.id); load(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  return (
    <Card title="Exchange holidays">
      <div className="flex flex-wrap items-center gap-3 text-xs mb-3">
        <CalendarOff size={14} className="text-muted" />
        <span className="text-muted">Closed days the worker stays idle on. Shared across every organisation.</span>
        <select value={exchange} onChange={(e) => setExchange(e.target.value)} className="rounded bg-panel2 border border-border px-2 py-0.5 text-xs text-slate-200">
          {["NSE", "BSE", "MCX"].map((x) => <option key={x}>{x}</option>)}
        </select>
        <select value={year} onChange={(e) => setYear(Number(e.target.value))} className="rounded bg-panel2 border border-border px-2 py-0.5 text-xs text-slate-200">
          {[year - 1, year, year + 1, year + 2].filter((v, i, a) => a.indexOf(v) === i).sort().map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
        <span className="text-muted ml-auto">{rows.length} holiday{rows.length === 1 ? "" : "s"}</span>
      </div>
      {error && <div className="text-xs text-danger mb-2">{error}</div>}
      {message && <div className="text-xs text-emerald-400 mb-2">{message}</div>}
      {rows.length === 0 ? (
        <div className="text-xs text-amber-300 mb-3">No {exchange} holidays recorded for {year}. Without them the worker treats every weekday as a trading day.</div>
      ) : (
        <ul className="grid sm:grid-cols-2 lg:grid-cols-3 gap-1 text-xs mb-3">
          {rows.map((r) => (
            <li key={r.id} className="flex items-center justify-between rounded border border-border/60 px-2 py-1">
              <span><span className="text-slate-200 font-semibold">{r.holiday_date}</span> <span className="text-muted">{r.description}</span></span>
              {isAdmin && <button disabled={busy} onClick={() => remove(r)} title="Remove" className="text-muted hover:text-danger disabled:opacity-50"><Trash2 size={12} /></button>}
            </li>
          ))}
        </ul>
      )}
      {isAdmin ? (
        <div>
          <label className="block text-[11px] text-muted mb-1">Add holidays, one per line as <code>YYYY-MM-DD description</code> (paste the NSE annual circular)</label>
          <textarea value={bulk} onChange={(e) => setBulk(e.target.value)} rows={3} placeholder={`${year}-10-20 Diwali Laxmi Pujan\n${year}-11-05 Guru Nanak Jayanti`}
            className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-xs font-mono" />
          <button disabled={busy || !bulk.trim()} onClick={addBulk} className="mt-2 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">Add to {exchange} {year}</button>
        </div>
      ) : (
        <div className="text-[11px] text-muted">Only a platform administrator can change this list.</div>
      )}
    </Card>
  );
}
