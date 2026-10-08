import { CheckCircle2, Store, Upload, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, Disclaimer, signClass } from "../components/ui";
import { PageHeader } from "../components/primitives";
import type {
  BacktestRunSummary, CustomStrategyResponse, MarketplaceCharge, MarketplaceEarnings, MarketplaceListing, MarketplacePayout, MarketplaceRevenue,
  MarketplaceSubscription, MarketplaceTerms,
} from "../types";

const input = "w-full rounded bg-surface-2 border border-border px-2 py-1 text-xs";
const pct = (v: number | null) => (v == null ? "-" : `${(v * (v <= 1 ? 100 : 1)).toFixed(1)}%`);
const num = (v: number | null, d = 2) => (v == null ? "-" : v.toFixed(d));
const inr = (v: number, currency = "INR") => (v > 0 ? `${currency === "INR" ? "₹" : `${currency} `}${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "Free");

function Performance({ l }: { l: MarketplaceListing }) {
  const p = l.performance;
  if (!p) return <div className="text-[11px] text-fg-muted">No documented performance attached.</div>;
  return (
    <div className="text-[11px] text-fg-muted space-y-0.5">
      <div className="flex flex-wrap gap-3">
        <span>trades <b>{p.total_trades ?? "-"}</b></span>
        <span>win rate <b>{pct(p.win_rate)}</b></span>
        <span>net P&L <b className={signClass(p.net_pnl, 0)}>{num(p.net_pnl, 0)}</b></span>
        <span>profit factor <b>{num(p.profit_factor)}</b></span>
        <span>max DD <b>{num(p.max_drawdown, 0)}</b></span>
        <span>expectancy <b>{num(p.expectancy)}</b></span>
      </div>
      <div className="text-fg-muted">
        Methodology: backtest on {p.symbol} {p.base_timeframe}, {p.bars} bars {p.data_from ? `${p.data_from.slice(0, 10)} → ${p.data_to?.slice(0, 10)}` : ""}, source {p.data_source}, engine v{p.engine_version}. Costs modelled; no slippage beyond the engine's model.
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: MarketplaceListing["status"] }) {
  const cls = status === "PUBLISHED" ? "border-up/40 text-up" : status === "PENDING_REVIEW" ? "border-warn/40 text-warn" : status === "REJECTED" ? "border-down/40 text-down" : "border-border text-fg-muted";
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
  const [form, setForm] = useState({ custom_strategy_id: "", title: "", description: "", methodology: "", backtest_run_id: "", price: ""});
  const [gate, setGate] = useState<string | null>(null);
  // Phase X: revenue share.
  const [purchases, setPurchases] = useState<MarketplaceCharge[]>([]);
  const [earnings, setEarnings] = useState<MarketplaceEarnings | null>(null);
  const [terms, setTerms] = useState<MarketplaceTerms | null>(null);
  const [payoutDest, setPayoutDest] = useState("");
  const [openCharges, setOpenCharges] = useState<MarketplaceCharge[]>([]);
  const [payoutQueue, setPayoutQueue] = useState<MarketplacePayout[]>([]);
  const [shownDestination, setShownDestination] = useState<Record<number, string>>({});   // stays until the page reloads
  const [revenue, setRevenue] = useState<MarketplaceRevenue | null>(null);
  const [termsDraft, setTermsDraft] = useState<Record<string, string>>({});
  const [checkout, setCheckout] = useState<{ url: string | null; next: string } | null>(null);
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
    api.marketplacePurchases().then(setPurchases).catch(() => {});
    api.marketplaceEarnings().then(setEarnings).catch(() => {});
    api.marketplaceTerms().then(setTerms).catch(() => {});
    if (isAdmin) {
      api.adminMarketplacePending().then(setPending).catch(() => {});
      api.adminMarketplaceCharges("OPEN").then(setOpenCharges).catch(() => {});
      api.adminMarketplacePayouts("REQUESTED").then(setPayoutQueue).catch(() => {});
      api.adminMarketplaceRevenue().then((r) => { setRevenue(r); setTermsDraft(Object.fromEntries(Object.entries(r.terms).map(([k, v]) => [k, String(v)]))); }).catch(() => {});
    }
  }
  useEffect(refresh, [isAdmin]);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(true); setError(null); setMessage(null);
    try { await fn(); setMessage(label); refresh(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  const subscribed = (id: number) => subs.find((s) => s.listing_id === id && s.status === "ACTIVE");

  return (
    <div className="space-y-4">
      <PageHeader title="Marketplace" description="Strategies other users published, each with a saved backtest as its documented performance. A copy you subscribe to runs in your own account; nothing here is advice." />
      <Disclaimer kind="backtest" />
      {gate && <div className="rounded-lg border border-warn/40 bg-warn/5 p-3 text-sm text-warn">{gate}</div>}

      <Card title="Discover strategies">
        {listings.length === 0 ? <div className="text-xs text-fg-muted">Nothing published yet.</div> : (
          <div className="grid lg:grid-cols-2 gap-3">
            {listings.map((l) => (
              <div key={l.id} className="rounded-lg border border-border bg-surface-2/40 p-3 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-bold text-sm flex items-center gap-1.5"><Store size={14} className="text-brand" /> {l.title} <span className="text-fg-muted text-[11px]">v{l.version_number}</span></div>
                  <span className="text-[11px] text-fg-muted flex items-center gap-1"><Users size={11} /> {l.subscriber_count} · <b className="text-fg">{inr(l.price, l.currency)}</b>{l.price > 0 ? " one-time" : ""}</span>
                </div>
                <div className="text-xs text-fg-muted whitespace-pre-wrap">{l.description}</div>
                {l.methodology && <div className="text-[11px] text-fg-muted whitespace-pre-wrap"><b>How it trades:</b> {l.methodology}</div>}
                <Performance l={l} />
                <div className="flex items-center gap-2">
                  {subscribed(l.id) ? (
                    <>
                      <span className="text-[11px] text-up flex items-center gap-1"><CheckCircle2 size={12} /> in your strategies as {subscribed(l.id)?.strategy_id}</span>
                      <button disabled={busy} onClick={() => run("Unsubscribed. Your copy stays in your strategies.", () => api.marketplaceUnsubscribe(l.id))} className="text-[11px] text-down hover:underline">Unsubscribe</button>
                    </>
                  ) : (
                    <button disabled={busy} onClick={() => run(l.price > 0 ? "Purchase started." : "Subscribed - a copy is now under your custom strategies. Backtest and paper-trade it before going LIVE.", async () => {
                      const res = await api.marketplaceSubscribe(l.id);
                      setCheckout(res.status === "PENDING_PAYMENT" ? { url: res.checkout_url ?? null, next: res.next } : null);
                    })} className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1 text-xs disabled:opacity-50">
                      {l.price > 0 ? `Buy for ${inr(l.price, l.currency)} (copy to my strategies)` : "Subscribe (copy to my strategies)"}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {checkout && (
        <div className="rounded-lg border border-brand/40 bg-brand/5 p-3 text-xs text-fg">
          {checkout.url ? <>Pay securely at <a className="text-brand underline" href={checkout.url} target="_blank" rel="noreferrer">{checkout.url}</a>. </> : null}{checkout.next}
        </div>
      )}

      {purchases.length > 0 && (
        <Card title="Your purchases">
          <table className="w-full text-xs"><tbody>
            {purchases.map((c) => (
              <tr key={c.id} className="border-t border-border/60">
                <td className="py-1 font-bold">{c.listing_title ?? `listing #${c.listing_id}`}</td>
                <td className="py-1">{inr(c.amount, c.currency)}</td>
                <td className="py-1"><span className={`rounded-md border px-2 py-0.5 text-[11px] font-bold ${c.status === "PAID" ? "border-up/40 text-up" : c.status === "OPEN" ? "border-warn/40 text-warn" : "border-border text-fg-muted"}`}>{c.status}</span></td>
                <td className="py-1 text-fg-muted">{c.status === "OPEN" ? (c.checkout_url ? <a className="text-brand underline" href={c.checkout_url} target="_blank" rel="noreferrer">pay now</a> : `awaiting operator confirmation (charge #${c.id})`) : c.payment_ref ? `ref ${c.payment_ref}` : ""}</td>
                <td className="py-1 text-fg-muted text-right">{c.created_at ? new Date(c.created_at).toLocaleDateString() : ""}</td>
              </tr>
            ))}
          </tbody></table>
        </Card>
      )}

      <Card title="Publish one of your strategies">
        <p className="text-xs text-fg-muted mb-3">
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
          <input className={input} type="number" min={0} step="1" placeholder={`One-time price in INR (0 = free${terms ? `, max ${terms.max_listing_price}` : ""})`} value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })}
            title={terms ? `You keep ${100 - terms.platform_fee_pct}% of every sale; the platform fee is ${terms.platform_fee_pct}%.` : ""} />
          <textarea className={`${input} md:col-span-2`} rows={3} placeholder="Description for subscribers (40+ characters)" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </div>
        <button
          disabled={busy || !form.custom_strategy_id || form.title.length < 4 || form.description.length < 40}
          onClick={() => run("Draft listing created. Submit it for review when ready.", () => api.marketplaceCreate({
            custom_strategy_id: Number(form.custom_strategy_id), title: form.title, description: form.description,
            methodology: form.methodology || null, backtest_run_id: form.backtest_run_id ? Number(form.backtest_run_id) : null,
            price: form.price ? Number(form.price) : 0,
          }))}
          className="mt-2 rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1 text-xs disabled:opacity-50"
        >
          <Upload size={12} className="inline mr-1" /> Create draft listing
        </button>

        {mine.length > 0 && (
          <table className="w-full text-xs mt-3"><tbody>
            {mine.map((l) => (
              <tr key={l.id} className="border-t border-border/60">
                <td className="py-1 font-bold">{l.title} <span className="text-fg-muted">v{l.version_number}</span></td>
                <td className="py-1"><StatusBadge status={l.status} /></td>
                <td className="py-1 text-fg-muted">{l.review_note ?? ""}</td>
                <td className="py-1 text-fg-muted">{l.subscriber_count} subscribers · {inr(l.price, l.currency)}{l.price > 0 && l.creator_net_per_sale != null ? ` (you keep ${inr(l.creator_net_per_sale, l.currency)})` : ""}</td>
                <td className="py-1 text-right space-x-2">
                  {(l.status === "DRAFT" || l.status === "REJECTED" || l.status === "UNLISTED") && <button disabled={busy} onClick={() => run("Submitted for review.", () => api.marketplaceSubmit(l.id))} className="text-brand hover:underline">Submit</button>}
                  {l.status === "PUBLISHED" && <button disabled={busy} onClick={() => run("Unlisted. Existing subscribers keep their copies.", () => api.marketplaceUnlist(l.id))} className="text-down hover:underline">Unlist</button>}
                </td>
              </tr>
            ))}
          </tbody></table>
        )}
      </Card>

      {earnings && (earnings.sales > 0 || mine.some((l) => l.price > 0)) && (
        <Card title="Creator earnings">
          <div className="grid sm:grid-cols-5 gap-3 text-xs mb-3">
            {([["Sales", String(earnings.sales)], ["Gross", inr(earnings.gross)], [`Platform fee`, inr(earnings.platform_fees)], ["Available", inr(earnings.available)], ["Paid out", inr(earnings.paid_out)]] as const).map(([k, v]) => (
              <div key={k} className="rounded border border-border p-2"><div className="text-[10px] uppercase tracking-wide text-fg-muted">{k}</div><div className="font-bold text-fg">{v}</div></div>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <input className={`${input} max-w-xs`} placeholder="Payout destination: UPI id or IFSC / account (stored encrypted)" value={payoutDest} onChange={(e) => setPayoutDest(e.target.value)} />
            <button disabled={busy || !earnings.can_request_payout || payoutDest.length < 6} onClick={() => run("Payout requested. The operator settles it by bank/UPI transfer and records the reference.", () => api.marketplaceRequestPayout(payoutDest))}
              className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1 text-xs disabled:opacity-50">Request payout of {inr(earnings.available)}</button>
            <span className="text-fg-muted">minimum {inr(earnings.min_payout)}{earnings.pending_payout > 0 ? ` · ${inr(earnings.pending_payout)} being processed` : ""}</span>
          </div>
          {earnings.payouts.length > 0 && (
            <table className="w-full text-xs mt-3"><tbody>
              {earnings.payouts.map((p) => (
                <tr key={p.id} className="border-t border-border/60"><td className="py-1">Payout #{p.id}</td><td className="py-1">{inr(p.amount, p.currency)}</td><td className="py-1">{p.status}</td><td className="py-1 text-fg-muted">to {p.destination_hint}{p.reference ? ` · ref ${p.reference}` : ""}{p.note ? ` · ${p.note}` : ""}</td><td className="py-1 text-fg-muted text-right">{p.created_at ? new Date(p.created_at).toLocaleDateString() : ""}</td></tr>
              ))}
            </tbody></table>
          )}
          {earnings.sales_rows.length > 0 && (
            <table className="w-full text-xs mt-3"><thead className="text-fg-muted uppercase text-[10px]"><tr className="text-left"><th className="py-1">Sale</th><th className="py-1">Gross</th><th className="py-1">Fee</th><th className="py-1">Your share</th><th className="py-1">Payout</th></tr></thead><tbody>
              {earnings.sales_rows.map((c) => (
                <tr key={c.id} className="border-t border-border/60"><td className="py-1">{c.listing_title} · buyer org #{c.buyer_tenant_id} · {c.paid_at ? new Date(c.paid_at).toLocaleDateString() : ""}</td><td className="py-1">{inr(c.amount, c.currency)}</td><td className="py-1 text-fg-muted">{c.platform_fee_pct}% = {inr(c.platform_fee, c.currency)}</td><td className="py-1 text-up">{inr(c.creator_net, c.currency)}</td><td className="py-1 text-fg-muted">{c.payout_id ? `#${c.payout_id}` : "available"}</td></tr>
              ))}
            </tbody></table>
          )}
        </Card>
      )}

      {isAdmin && revenue && (
        <Card title="Marketplace revenue and terms (platform admin)">
          <div className="grid sm:grid-cols-6 gap-3 text-xs mb-3">
            {([["Sales", String(revenue.sales)], ["Gross", inr(revenue.gross)], ["Platform fees", inr(revenue.platform_fees)], ["Creators' share", inr(revenue.creator_net)], ["Paid out", inr(revenue.paid_out)], ["Open charges", String(revenue.open_charges)]] as const).map(([k, v]) => (
              <div key={k} className="rounded border border-border p-2"><div className="text-[10px] uppercase tracking-wide text-fg-muted">{k}</div><div className="font-bold text-fg">{v}</div></div>
            ))}
          </div>
          <div className="grid sm:grid-cols-4 gap-3 text-xs items-end">
            {Object.keys(revenue.terms).map((key) => (
              <div key={key}><label className="block text-[10px] text-fg-muted mb-0.5 font-mono">{key}</label>
                <input type="number" min={0} step="0.5" className={input} value={termsDraft[key] ?? ""} onChange={(e) => setTermsDraft({ ...termsDraft, [key]: e.target.value })} /></div>
            ))}
            <button disabled={busy} onClick={() => run("Marketplace terms saved. Published listings keep the fee they were published under.", () => api.adminSetMarketplaceTerms(Object.fromEntries(Object.entries(termsDraft).map(([k, v]) => [k, Number(v)]))))} className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1 text-xs disabled:opacity-50">Save terms</button>
          </div>
          {openCharges.length > 0 && (
            <div className="mt-3">
              <div className="text-[11px] font-bold uppercase tracking-wider text-fg-muted mb-1">Open charges (manual payments to confirm)</div>
              <table className="w-full text-xs"><tbody>
                {openCharges.map((c) => (
                  <tr key={c.id} className="border-t border-border/60"><td className="py-1">#{c.id} {c.listing_title} · buyer org #{c.buyer_tenant_id}</td><td className="py-1">{inr(c.amount, c.currency)} via {c.provider}</td>
                    <td className="py-1 text-right space-x-2">
                      <button disabled={busy} onClick={() => { const ref = window.prompt("Transfer reference (UTR / UPI ref)") ?? ""; if (ref) void run("Charge confirmed; the buyer's copy is made.", () => api.adminMarketplaceChargePaid(c.id, ref)); }} className="text-brand hover:underline">Mark paid</button>
                      <button disabled={busy} onClick={() => { const note = window.prompt("Reason for voiding") ?? ""; void run("Charge voided.", () => api.adminMarketplaceChargeVoid(c.id, note)); }} className="text-down hover:underline">Void</button>
                    </td></tr>
                ))}
              </tbody></table>
            </div>
          )}
          {payoutQueue.length > 0 && (
            <div className="mt-3">
              <div className="text-[11px] font-bold uppercase tracking-wider text-fg-muted mb-1">Payout requests</div>
              <table className="w-full text-xs"><tbody>
                {payoutQueue.map((p) => (
                  <tr key={p.id} className="border-t border-border/60"><td className="py-1">#{p.id} creator org #{p.tenant_id}</td><td className="py-1">{inr(p.amount, p.currency)} to {shownDestination[p.id] ? <span className="select-all font-mono text-fg">{shownDestination[p.id]}</span> : p.destination_hint}</td>
                    <td className="py-1 text-right space-x-2">
                      <button disabled={busy} onClick={() => api.adminMarketplacePayoutDestination(p.id).then((d) => setShownDestination((s) => ({ ...s, [p.id]: d.destination }))).catch((e) => setError(String(e)))} className="text-fg hover:underline">Show destination</button>
                      <button disabled={busy} onClick={() => { const ref = window.prompt("Transfer reference (UTR / UPI ref)") ?? ""; if (ref) void run("Payout marked paid.", () => api.adminMarketplacePayoutSettle(p.id, true, ref)); }} className="text-brand hover:underline">Mark paid</button>
                      <button disabled={busy} onClick={() => { const note = window.prompt("Reason (shown to the creator)") ?? ""; void run("Payout rejected; the earnings are available again.", () => api.adminMarketplacePayoutSettle(p.id, false, undefined, note)); }} className="text-down hover:underline">Reject</button>
                    </td></tr>
                ))}
              </tbody></table>
            </div>
          )}
        </Card>
      )}

      {isAdmin && (
        <Card title="Review queue (platform admin)">
          {pending.length === 0 ? <div className="text-xs text-fg-muted">No listings waiting for review.</div> : pending.map((l) => (
            <div key={l.id} className="rounded-lg border border-warn/30 p-3 mb-2 space-y-1">
              <div className="font-bold text-sm">{l.title} <span className="text-fg-muted text-[11px]">v{l.version_number} · tenant listing #{l.id}</span></div>
              <div className="text-xs text-fg-muted">{l.description}</div>
              <Performance l={l} />
              <div className="flex gap-2 mt-1">
                <button disabled={busy} onClick={() => run("Published.", () => api.adminMarketplaceReview(l.id, true))} className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1 text-xs">Publish</button>
                <button disabled={busy} onClick={() => { const note = window.prompt("Reason for rejection (shown to the creator)") ?? ""; void run("Rejected.", () => api.adminMarketplaceReview(l.id, false, note)); }} className="rounded border border-down/40 text-down px-3 py-1 text-xs">Reject</button>
              </div>
            </div>
          ))}
        </Card>
      )}

      {error && <div className="text-sm text-down">{error}</div>}
      {message && <div className="text-sm text-up">{message}</div>}
    </div>
  );
}
