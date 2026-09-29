import { useEffect, useState } from "react";
import { CheckCircle2, CircleAlert, CircleDashed, Info, RefreshCw } from "lucide-react";
import { api } from "../api/client";
import { Card } from "../components/ui";
import type { Page } from "../components/Sidebar";
import type { ReadinessChecklist, ReadinessItem } from "../types";

/** Phase AB: the go-live checklist. `kind="tenant"` is this organisation's list (PAPER or LIVE
 * target); `kind="platform"` is the operator's deployment list (SUPER_ADMIN). Read-only. */

const ICON = {
  ok: <CheckCircle2 size={14} className="text-emerald-400 shrink-0 mt-0.5" />,
  todo: <CircleAlert size={14} className="text-rose-400 shrink-0 mt-0.5" />,
  warn: <CircleAlert size={14} className="text-amber-300 shrink-0 mt-0.5" />,
  info: <Info size={14} className="text-muted shrink-0 mt-0.5" />,
};
const ORDER: Record<ReadinessItem["status"], number> = { todo: 0, warn: 1, info: 2, ok: 3 };
const PAGE_IDS = new Set(["dashboard", "settings", "instruments", "autopilot", "risk", "positions", "admin"]);

export default function GoLiveChecklist({ kind, onNavigate }: { kind: "tenant" | "platform"; onNavigate?: (page: Page) => void }) {
  const [target, setTarget] = useState<"PAPER" | "LIVE">("PAPER");
  const [data, setData] = useState<ReadinessChecklist | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true); setError(null);
    try { setData(kind === "tenant" ? await api.readiness(target) : await api.adminReadiness()); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [kind, target]);

  const items = data ? [...data.items].sort((a, b) => ORDER[a.status] - ORDER[b.status]) : [];
  const title = kind === "tenant" ? "Go-live checklist" : "Platform readiness (operator)";

  return (
    <Card title={title}>
      <div className="flex flex-wrap items-center gap-3 mb-3 text-xs">
        {kind === "tenant" && (
          <div className="flex rounded border border-border overflow-hidden">
            {(["PAPER", "LIVE"] as const).map((t) => (
              <button key={t} onClick={() => setTarget(t)} className={`px-2 py-0.5 ${target === t ? "bg-panel2 text-slate-200" : "text-muted"} ${t === "LIVE" ? "border-l border-border" : ""}`}>{t}</button>
            ))}
          </div>
        )}
        {data && (
          <span className={`rounded-md border px-2 py-0.5 font-bold ${data.ready ? "border-emerald-500/40 text-emerald-400" : "border-rose-500/40 text-rose-400"}`}>
            {data.ready ? `Ready for ${data.target}` : `${data.summary.todo} blocker${data.summary.todo === 1 ? "" : "s"} for ${data.target}`}
          </span>
        )}
        {data && <span className="text-muted">{data.summary.ok} ok · {data.summary.warn} warnings · {data.summary.info} optional</span>}
        <button onClick={load} disabled={busy} className="ml-auto text-muted hover:text-slate-200 disabled:opacity-50" title="Re-check"><RefreshCw size={13} className={busy ? "animate-spin" : ""} /></button>
      </div>
      {error && <div className="text-xs text-danger mb-2">{error}</div>}
      {!data && !error && <div className="text-xs text-muted flex items-center gap-1"><CircleDashed size={13} className="animate-spin" /> Checking…</div>}
      <ul className="space-y-1.5">
        {items.map((it) => (
          <li key={it.key} className="flex items-start gap-2 text-xs">
            {ICON[it.status]}
            <div className="min-w-0">
              <span className="text-slate-200 font-semibold">{it.title}</span>
              {it.scope === "LIVE" && <span className="ml-1 rounded border border-border px-1 text-[10px] text-muted">LIVE</span>}
              {it.scope === "OPTIONAL" && <span className="ml-1 rounded border border-border px-1 text-[10px] text-muted">optional</span>}
              <span className="text-muted"> · {it.detail}</span>
              {it.fix && it.status !== "ok" && (
                <div className="text-muted">
                  {it.fix}{" "}
                  {it.link && onNavigate && PAGE_IDS.has(it.link) && (
                    <button onClick={() => onNavigate(it.link as Page)} className="text-brand hover:underline">Open {it.link}</button>
                  )}
                  {it.link && !PAGE_IDS.has(it.link) && <code className="text-[10px] text-slate-400">{it.link}</code>}
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
      {data && <div className="mt-2 text-[10px] text-muted">{data.note}</div>}
    </Card>
  );
}
