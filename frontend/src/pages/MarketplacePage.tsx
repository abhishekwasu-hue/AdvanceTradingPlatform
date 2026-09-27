import { CheckCircle2, Store, Upload, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, Disclaimer } from "../components/ui";
import type { BacktestRunSummary, CustomStrategyResponse, MarketplaceListing, MarketplaceSubscription } from "../types";

const input = "w-full rounded bg-panel2 border border-border px-2 py-1 text-xs";
const pct = (v: number | null) => (v == null ? "-" : `${(v * (v <= 1 ? 100 : 1)).toFixed(1)}%`);
const num = (v: number | null, d = 2) => (v == null ? "-" : v.toFixed(d));

function Performance({ l }: { l: MarketplaceListing }) {
  const p = l.performance;
  if (!p) return <div className="text-[11px] text-muted">No documented performance attached.</div>;
  return (
    <div className="text-[11px] text-slate-300 space-y-0.5">
      <div className="flex flex-wrap gap-3">
        <span>trades <b>{p.total_trades ?? "-"}</b></span>
        <span>win rate <b>{pct(p.win_rate)}</b></span>
        <span>net P&L <b className={(p.net_pnl ?? 0) >= 0 ? "text-accent" : "text-danger"}>{num(p.net_pnl, 0)}</b></span>
        <span>profit factor <b>{num(p.profit_factor)}</b></span>
        <span>max DD <b>{num(p.max_drawdown, 0)}</b></span>
        <span>expectancy <b>{num(p.expectancy)}</b></span>
      </div>
      <div className="text-muted">
        Methodology: backtest on {p.symbol} {p.base_timeframe}, {p.bars} bars {p.data_from ? `${p.data_from.slice(0, 10)} → ${p.data_to?.slice(0, 10)}` : ""}, source {p.data_source}, engine v{p.engine_version}. Costs modelled; no slippage beyond the engine's model.
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: MarketplaceListing["status"] }) {
  const cls = status === "PUBLISHED" ? "border-emerald-500/40 text-emerald-400" : status === "PENDING_REVIEW" ? "border-amber-500/40 text-amber-400" : status === "REJECTED" ? "border-rose-500/40 text-rose-400" : "border-border text-muted";
  return <span className={`rounded-md border px-2 py-0.5 text-[11px] font-bold ${cls}`}>{status.replace("_", " ")}</span>;
}

/** Phase K2: discover published strategies, subscribe (a frozen copy lands in your own custom
 * strategies), and publish your own through operator review. */
export default function MarketplacePage() {
  const { user } = useAuth();
  const [listings, setListings] = useState<MarketplaceListing[]>([]);
  const [mine, setMine] = useState<MarketplaceListing[]>([]);
  const [subs, setSubs] = useState<MarketplaceSubscription[]>([]);
  const [pending, setPending] = useState<MarketplaceListing[]>([]);
  const [strategies, setStrategies] = useState<CustomStrategyResponse[]>([]);
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [form, setForm] = useState({ custom_strategy_id: "", title: "", description: "", methodology: "", backtest_run_id: "" });
  const [gate, setGate] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isAdmin = user?.role === "SUPER_ADMIN";

  function refresh() {
    api.marketplace().then((l) => { setListings(l); setGate(null); }).catch((e) => setGate(String(e)));
    api.marketplaceMine().then(setMine).catch(() => {});
    api.marketplaceSubscriptions().then(setSubs).catch(() => {});
    api.listCustomStrategies().then(setStrategies).catch(() => {});
    api.listBacktests().then(setRuns).catch(() => {});
    if (isAdmin) api.adminMarketplacePending().then(setPending).catch(() => {});
  }
  useEffect(refresh, [isAdmin]);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(true); setError(null); setMessage(null);
    try { await fn(); setMessage(label); refresh(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  const subscribed = (id: number) => subs.find((s) => s.listing_id === id && s.status === "ACTIVE");

  return (
    <div className="space-y-4">
      <Disclaimer kind="backtest" />
      {gate && <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm text-amber-300">{gate}</div>}

      <Card title="Discover strategies">
        {listings.length === 0 ? <div className="text-xs text-muted">Nothing published yet.</div> : (
          <div className="grid lg:grid-cols-2 gap-3">
            {listings.map((l) => (
              <div key={l.id} className="rounded-lg border border-border bg-panel2/40 p-3 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-bold text-sm flex items-center gap-1.5"><Store size={14} className="text-brand" /> {l.title} <span className="text-muted text-[11px]">v{l.version_number}</span></div>
                  <span className="text-[11px] text-muted flex items-center gap-1"><Users size={11} /> {l.subscriber_count}</span>
                </div>
                <div className="text-xs text-slate-300 whitespace-pre-wrap">{l.description}</div>
                {l.methodology && <div className="text-[11px] text-muted whitespace-pre-wrap"><b>How it trades:</b> {l.methodology}</div>}
                <Performance l={l} />
                <div className="flex items-center gap-2">
                  {subscribed(l.id) ? (
                    <>
                      <span className="text-[11px] text-emerald-400 flex items-center gap-1"><CheckCircle2 size={12} /> in your strategies as {subscribed(l.id)?.strategy_id}</span>
                      <button disabled={busy} onClick={() => run("Unsubscribed. Your copy stays in your strategies.", () => api.marketplaceUnsubscribe(l.id))} className="text-[11px] text-danger hover:underline">Unsubscribe</button>
                    </>
                  ) : (
                    <button disabled={busy} onClick={() => run("Subscribed - a copy is now under your custom strategies. Backtest and paper-trade it before going LIVE.", () => api.marketplaceSubscribe(l.id))} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">Subscribe (copy to my strategies)</button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Publish one of your strategies">
        <p className="text-xs text-muted mb-3">
          A listing freezes one version of your strategy - later edits never reach subscribers. Attach a saved backtest run as the
          documented performance (required); the operator reviews every listing before it appears. Describe how the strategy trades
          and when it does not work.
        </p>
        <div className="grid md:grid-cols-2 gap-2">
          <select className={input} value={form.custom_strategy_id} onChange={(e) => setForm({ ...form, custom_strategy_id: e.target.value })}>
            <option value="">Strategy to publish…</option>
            {strategies.map((s) => <option key={s.id} value={s.id}>{s.config.name} ({s.strategy_id})</option>)}
          </select>
          <select className={input} value={form.backtest_run_id} onChange={(e) => setForm({ ...form, backtest_run_id: e.target.value })}>
            <option value="">Documented performance (saved backtest run)…</option>
            {runs.map((r) => <option key={r.id} value={r.id}>#{r.id} {r.strategy_id} {r.symbol} {r.base_timeframe} · {r.total_trades} trades · P&L {r.net_pnl?.toFixed(0)}</option>)}
          </select>
          <input className={input} placeholder="Title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          <input className={input} placeholder="Methodology (timeframes, filters, exits, regimes it avoids)" value={form.methodology} onChange={(e) => setForm({ ...form, methodology: e.target.value })} />
          <textarea className={`${input} md:col-span-2`} rows={3} placeholder="Description for subscribers (40+ characters)" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </div>
        <button
          disabled={busy || !form.custom_strategy_id || form.title.length < 4 || form.description.length < 40}
          onClick={() => run("Draft listing created. Submit it for review when ready.", () => api.marketplaceCreate({
            custom_strategy_id: Number(form.custom_strategy_id), title: form.title, description: form.description,
            methodology: form.methodology || null, backtest_run_id: form.backtest_run_id ? Number(form.backtest_run_id) : null,
          }))}
          className="mt-2 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50"
        >
          <Upload size={12} className="inline mr-1" /> Create draft listing
        </button>

        {mine.length > 0 && (
          <table className="w-full text-xs mt-3"><tbody>
            {mine.map((l) => (
              <tr key={l.id} className="border-t border-border/60">
                <td className="py-1 font-bold">{l.title} <span className="text-muted">v{l.version_number}</span></td>
                <td className="py-1"><StatusBadge status={l.status} /></td>
                <td className="py-1 text-muted">{l.review_note ?? ""}</td>
                <td className="py-1 text-muted">{l.subscriber_count} subscribers</td>
                <td className="py-1 text-right space-x-2">
                  {(l.status === "DRAFT" || l.status === "REJECTED" || l.status === "UNLISTED") && <button disabled={busy} onClick={() => run("Submitted for review.", () => api.marketplaceSubmit(l.id))} className="text-brand hover:underline">Submit</button>}
                  {l.status === "PUBLISHED" && <button disabled={busy} onClick={() => run("Unlisted. Existing subscribers keep their copies.", () => api.marketplaceUnlist(l.id))} className="text-danger hover:underline">Unlist</button>}
                </td>
              </tr>
            ))}
          </tbody></table>
        )}
      </Card>

      {isAdmin && (
        <Card title="Review queue (platform admin)">
          {pending.length === 0 ? <div className="text-xs text-muted">No listings waiting for review.</div> : pending.map((l) => (
            <div key={l.id} className="rounded-lg border border-amber-500/30 p-3 mb-2 space-y-1">
              <div className="font-bold text-sm">{l.title} <span className="text-muted text-[11px]">v{l.version_number} · tenant listing #{l.id}</span></div>
              <div className="text-xs text-slate-300">{l.description}</div>
              <Performance l={l} />
              <div className="flex gap-2 mt-1">
                <button disabled={busy} onClick={() => run("Published.", () => api.adminMarketplaceReview(l.id, true))} className="rounded bg-emerald-600 hover:bg-emerald-500 text-white font-semibold px-3 py-1 text-xs">Publish</button>
                <button disabled={busy} onClick={() => { const note = window.prompt("Reason for rejection (shown to the creator)") ?? ""; void run("Rejected.", () => api.adminMarketplaceReview(l.id, false, note)); }} className="rounded border border-rose-500/40 text-rose-400 px-3 py-1 text-xs">Reject</button>
              </div>
            </div>
          ))}
        </Card>
      )}

      {error && <div className="text-sm text-danger">{error}</div>}
      {message && <div className="text-sm text-accent">{message}</div>}
    </div>
  );
}
