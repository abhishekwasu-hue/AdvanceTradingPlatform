import { CreditCard, Receipt } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card } from "./ui";
import type { BillingOverview, BillingTransaction, PlanCatalogueEntry } from "../types";

const inr = (v: number, ccy = "INR") => (v === 0 ? "Free" : `${ccy === "INR" ? "₹" : ccy + " "}${v.toLocaleString("en-IN")}`);

/** Phase K1: the organisation's plan, subscription state, usage and invoices. Payment is
 * recorded by the operator (manual provider) or a gateway webhook - never typed here. */
export default function BillingCard() {
  const { user } = useAuth();
  const [overview, setOverview] = useState<BillingOverview | null>(null);
  const [plans, setPlans] = useState<PlanCatalogueEntry[]>([]);
  const [txns, setTxns] = useState<BillingTransaction[]>([]);
  const [cycle, setCycle] = useState<"MONTHLY" | "YEARLY">("MONTHLY");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isOwner = user?.role === "OWNER" || user?.role === "SUPER_ADMIN";

  function refresh() {
    api.billingOverview().then(setOverview).catch((e) => setError(String(e)));
    api.billingPlans().then(setPlans).catch(() => {});
    api.billingTransactions().then(setTxns).catch(() => {});
  }
  useEffect(refresh, []);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(true); setError(null); setMessage(null);
    try { await fn(); setMessage(label); refresh(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  const sub = overview?.subscription;
  const usage = overview?.metered_30d ?? {};
  return (
    <Card title="Plan & billing">
      {sub && (
        <div className="flex flex-wrap items-center gap-3 text-sm mb-3">
          <span className="inline-flex items-center gap-1.5 rounded-md border border-brand/40 bg-brand/10 px-2 py-0.5 font-bold text-brand"><CreditCard size={13} /> {sub.plan_name}</span>
          <span className={`rounded-md border px-2 py-0.5 text-[11px] font-bold ${sub.status === "ACTIVE" || sub.status === "TRIALING" ? "border-emerald-500/40 text-emerald-400" : sub.status === "PAST_DUE" ? "border-amber-500/40 text-amber-400" : "border-border text-muted"}`}>{sub.status}</span>
          {sub.billing_cycle && <span className="text-muted text-xs">{sub.billing_cycle.toLowerCase()} · {inr(sub.billing_cycle === "YEARLY" ? sub.price_yearly : sub.price_monthly, sub.currency)}</span>}
          {sub.current_period_end && <span className="text-muted text-xs">period ends {new Date(sub.current_period_end).toLocaleDateString()}</span>}
          {sub.trial_end && sub.status === "TRIALING" && <span className="text-amber-400 text-xs">trial ends {new Date(sub.trial_end).toLocaleDateString()}</span>}
          {sub.grace_until && <span className="text-danger text-xs">grace until {new Date(sub.grace_until).toLocaleDateString()} - pay to avoid suspension</span>}
          {sub.cancel_at_period_end && <span className="text-muted text-xs">cancels at period end</span>}
          {sub.checkout_url && sub.status !== "CANCELLED" && (
            <a href={sub.checkout_url} target="_blank" rel="noreferrer" className="rounded bg-emerald-600 hover:bg-emerald-500 text-white font-semibold px-3 py-1 text-xs">
              {sub.status === "ACTIVE" ? "Manage autopay" : "Pay / set up autopay"}
            </a>
          )}
          {overview?.status_reason && <span className="text-danger text-xs">{overview.status_reason}</span>}
        </div>
      )}

      <div className="grid md:grid-cols-3 gap-3">
        {plans.map((p) => {
          const current = sub?.plan_id === p.id;
          return (
            <div key={p.id} className={`rounded-lg border p-3 space-y-1 ${current ? "border-brand/60 bg-brand/5" : "border-border bg-panel2/40"}`}>
              <div className="flex items-center justify-between">
                <div className="font-bold text-sm">{p.name}</div>
                <div className="text-xs text-muted">{inr(cycle === "YEARLY" ? p.price_yearly : p.price_monthly, p.currency)}{p.price_monthly > 0 && <span>/{cycle === "YEARLY" ? "yr" : "mo"}</span>}</div>
              </div>
              <div className="text-[11px] text-muted">{p.description}</div>
              <ul className="text-[11px] text-slate-300 space-y-0.5">
                <li>{String(p.active_deployments ?? "-")} active deployments · {String(p.live_strategies ?? "-")} LIVE</li>
                <li>{String(p.custom_strategies ?? "-")} custom strategies · {String(p.backtests_per_month ?? "-")} backtests/mo</li>
                <li>{String(p.accounts ?? "-")} broker accounts · API {String(p.api_calls_per_day ?? 0) === "0" ? "no" : `${String(p.api_calls_per_day)}/day`}</li>
                <li>{p.option_features ? "options + spreads" : "cash/futures only"} · {p.marketplace_access ? "marketplace" : "no marketplace"} · {p.ai_features ? "AI tools" : "no AI"}</li>
                <li>support: {String(p.support_level)}{p.trial_days ? ` · ${p.trial_days}-day trial` : ""}</li>
              </ul>
              {isOwner && !current && p.price_monthly > 0 && (
                <button disabled={busy} onClick={() => run(`Switched to ${p.name} (${cycle.toLowerCase()}). An invoice is raised when the trial ends.`, () => api.billingSubscribe(p.id, cycle))} className="mt-1 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">Choose {p.name}</button>
              )}
              {current && <div className="text-[11px] text-brand font-bold">current plan</div>}
            </div>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-3 mt-3 text-xs">
        <label className="flex items-center gap-1 text-muted">billing cycle
          <select className="rounded bg-panel2 border border-border px-1 py-0.5" value={cycle} onChange={(e) => setCycle(e.target.value as "MONTHLY" | "YEARLY")}>
            <option value="MONTHLY">monthly</option><option value="YEARLY">yearly (2 months free)</option>
          </select>
        </label>
        {isOwner && sub && sub.status !== "NONE" && sub.status !== "CANCELLED" && !sub.cancel_at_period_end && (
          <button disabled={busy} onClick={() => run("Subscription will end at the period end (deployments keep running until then).", () => api.billingCancel(false))} className="text-danger hover:underline">Cancel at period end</button>
        )}
        <span className="text-muted">{sub?.provider === "razorpay"
          ? "Payments through Razorpay (UPI autopay, cards, netbanking) on Razorpay's own page; the plan activates when the charge is confirmed. Card details never touch this server."
          : "Payments: bank transfer / UPI to the operator; the plan activates once the payment is recorded. No card details are ever collected here."}</span>
      </div>

      {Object.keys(usage).length > 0 && (
        <div className="mt-3 text-xs">
          <div className="text-[11px] font-bold uppercase tracking-wider text-muted mb-1">Metered usage, last 30 days</div>
          <div className="flex flex-wrap gap-3">{Object.entries(usage).map(([k, v]) => <span key={k} className="rounded border border-border px-2 py-0.5">{k.replace(/_/g, " ")}: <b>{v}</b></span>)}</div>
        </div>
      )}

      {txns.length > 0 && (
        <div className="mt-3">
          <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-muted mb-1"><Receipt size={12} /> Invoices & payments</div>
          <table className="w-full text-xs"><tbody>
            {txns.slice(0, 8).map((t) => (
              <tr key={t.id} className="border-t border-border/60">
                <td className="py-1 text-muted">{new Date(t.created_at).toLocaleDateString()}</td>
                <td className="py-1">{t.kind}</td>
                <td className="py-1">{t.description}</td>
                <td className="py-1 text-right">{inr(t.amount, t.currency)}</td>
                <td className={`py-1 text-right font-bold ${t.status === "PAID" ? "text-emerald-400" : t.status === "OPEN" ? "text-amber-400" : "text-muted"}`}>{t.status}</td>
              </tr>
            ))}
          </tbody></table>
        </div>
      )}

      {error && <div className="mt-3 text-sm text-danger">{error}</div>}
      {message && <div className="mt-3 text-sm text-accent">{message}</div>}
    </Card>
  );
}
