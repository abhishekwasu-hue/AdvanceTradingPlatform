import { errorSentence } from "../api/errors";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { PaperRiskStatus, TicketSetup, TicketView, TradePlanView } from "../types";
import { Card, signClass } from "./ui";
import { Details, TextOnDemand } from "../ui/numbersFirst";
import { useUiV2 } from "../ui/useFeatures";
import { CapitalAsk, needsCapital } from "../settings/CapitalAsk";
import { EMPTY_FORM, costRows, gauge, lockoutLine, money, pendingLine, placedMessage, planFromForm, type TicketForm } from "./paperTicket";

/**
 * Z5 / P1-d (P1 spec 1.1, 1.2, 2.2; Phase 0): the plan-before-trade PAPER ticket and the risk-by-default strip.
 * The plan (setup, direction, entry, stop, target, time stop, invalidation, checklist) goes to the server, which sizes
 * the quantity from your risk cap, checks the lockout and the guardian, and returns the cost line. The page computes
 * nothing financial. The confirm button says exactly what will happen. Exits are never locked. Hidden while the
 * `risk_by_default` flag is off (the API answers 404).
 */
const TONE = { ok: "stroke-up", warn: "stroke-warn", over: "stroke-down" } as const;
const FIELD = "min-h-[44px] w-full rounded border border-border bg-surface-2 px-2 py-1 text-sm sm:min-h-0";

export default function PaperTicketCard({ canTrade, onPlaced }: { canTrade: boolean; onPlaced?: () => void }) {
  const v2 = useUiV2();                       // UX-11: the exits line and the cost basis under Details
  const [status, setStatus] = useState<PaperRiskStatus | null>(null);
  const [hidden, setHidden] = useState(false);
  const [form, setForm] = useState<TicketForm>(EMPTY_FORM);
  const [view, setView] = useState<TicketView | null>(null);
  const [plans, setPlans] = useState<TradePlanView[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  function load() {
    Promise.all([api.paperRisk(), api.paperPlans()])
      .then(([s, p]) => { setStatus(s); setPlans(p); setHidden(false); })
      .catch(() => setHidden(true));
  }
  useEffect(load, []);
  if (hidden || !status) return null;

  const set = <K extends keyof TicketForm>(key: K, value: TicketForm[K]) => { setForm((f) => ({ ...f, [key]: value })); setView(null); };
  const { plan, missing } = planFromForm(form);

  async function preview() {
    if (!plan) return;
    setBusy(true); setMessage(null);
    try { setView(await api.previewTicket(plan)); }
    catch (e) { setMessage(errorSentence(e)); }
    finally { setBusy(false); }
  }
  async function place() {
    if (!plan || !view?.can_place) return;
    setBusy(true); setMessage(null);
    try {
      const out = await api.placeTicket(plan);
      setMessage(placedMessage(out));
      setView(null);
      load();
      onPlaced?.();
    } catch (e) { setMessage(errorSentence(e)); }
    finally { setBusy(false); }
  }

  const lock = lockoutLine(status);
  const vix = status.vix_gate;
  const g = view ? gauge(view.sizing.gauge, view.sizing.gauge_zone) : null;
  return (
    <Card title="Plan before trade (PAPER)" className="space-y-3">
      <div data-testid="paper-ticket" className="space-y-3">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted" data-testid="risk-strip">
          {/* TT-12: a limit he did not set is off - "Not set", never a platform number. */}
          {status.risk_cap.amount !== null
            ? <span>Cap per trade <span className="text-fg">{money(status.risk_cap.amount)}</span>{status.risk_cap.binding === "pct" ? ` (${status.effective.risk_per_trade_pct}% of ${money(status.capital_basis)})` : ""}</span>
            : <span>Cap per trade <span className="text-fg">Not set</span></span>}
          <span>Today <span className={signClass(status.today.pnl)}>{money(status.today.pnl)}</span>{status.today.limit > 0 ? ` of limit −${money(status.today.limit)}` : " · daily loss limit not set"}</span>
          <span>Entries {status.today.entries}{status.today.max_entries ? ` / ${status.today.max_entries}` : ""}</span>
          <span>Overrides today {status.overrides.today} · total {status.overrides.total}</span>
          {vix.enabled && <span data-testid="vix-gate">India VIX gate: {vix.bullish_paused ? (vix.state === "no_reading" ? "no India VIX reading - bullish index entries paused" : "bullish index entries paused today") : vix.state === "high" ? "high VIX, size cut" : vix.state === "waiting" || vix.state === "before_check" ? "check from 09:20 IST" : "normal"}</span>}
        </div>
        {lock && (
          <p className="rounded bg-warn/15 px-2 py-1 text-xs text-warn" role="status" data-testid="paper-lockout">
            {status.lockout?.reason}. {lock}
          </p>
        )}
        {pendingLine(status) && <p className="text-xs text-fg-muted" data-testid="risk-pending">{pendingLine(status)}</p>}
        {!v2 && <p className="text-xs text-fg-muted">{status.exits}</p>}

        {canTrade && (
          <>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <label className="col-span-2 text-xs text-fg-muted sm:col-span-1">Symbol
                <input className={FIELD} value={form.symbol} onChange={(e) => set("symbol", e.target.value)} placeholder="RELIANCE" />
              </label>
              <label className="text-xs text-fg-muted">Direction
                <select className={FIELD} value={form.direction} onChange={(e) => set("direction", e.target.value as "LONG" | "SHORT")}>
                  <option value="LONG">Long</option><option value="SHORT">Short</option>
                </select>
              </label>
              <label className="text-xs text-fg-muted">Setup
                <select className={FIELD} value={form.setup} onChange={(e) => set("setup", e.target.value as TicketSetup)}>
                  {Object.entries(status.setups).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                </select>
              </label>
              <label className="text-xs text-fg-muted">Time stop (IST)
                <input className={FIELD} value={form.time_stop} onChange={(e) => set("time_stop", e.target.value)} placeholder="15:00" />
              </label>
              {(["entry", "stop_loss", "target", "quantity"] as const).map((k) => (
                <label key={k} className="text-xs text-fg-muted">{{ entry: "Entry", stop_loss: "Stop", target: "Target", quantity: "Quantity (blank = sized)" }[k]}
                  <input className={FIELD} type="number" inputMode="decimal" min={0} value={form[k]} onChange={(e) => set(k, e.target.value)} />
                </label>
              ))}
              <label className="col-span-2 text-xs text-fg-muted sm:col-span-4">Invalidation (what proves the plan wrong)
                <input className={FIELD} maxLength={300} value={form.invalidation} onChange={(e) => set("invalidation", e.target.value)} />
              </label>
            </div>
            <fieldset className="grid grid-cols-1 gap-1 text-xs sm:grid-cols-2">
              <legend className="mb-1 text-xs font-semibold text-fg">Pre-trade checklist</legend>
              {Object.entries(status.checklist).map(([key, label]) => (
                <label key={key} className="flex min-h-[44px] items-center gap-2 sm:min-h-0">
                  <input type="checkbox" checked={Boolean(form.checklist[key])} onChange={(e) => set("checklist", { ...form.checklist, [key]: e.target.checked })} />
                  <span>{label}</span>
                </label>
              ))}
            </fieldset>
            <label className="flex min-h-[44px] items-center gap-2 text-xs sm:min-h-0">
              <input type="checkbox" checked={form.override} onChange={(e) => set("override", e.target.checked)} />
              <span>Override my risk cap (logged and counted; at most {status.override_max_multiple}x the cap)</span>
            </label>
            {form.override && (
              <input className={FIELD} maxLength={300} placeholder={`Reason (at least ${status.override_min_reason_chars} characters)`}
                     value={form.override_reason} onChange={(e) => set("override_reason", e.target.value)} aria-label="Override reason" />
            )}
            <button disabled={busy || !plan} onClick={() => void preview()}
                    className="min-h-[44px] rounded border border-border px-3 py-1 text-sm text-fg hover:bg-surface-2 disabled:opacity-50 sm:min-h-0">
              Check the plan
            </button>
            {!plan && missing.length > 0 && <span className="ml-2 text-xs text-fg-muted">Needs: {missing.join(", ")}</span>}
          </>
        )}

        {view && (
          <div className="space-y-2 rounded-lg border border-border p-3" data-testid="ticket-view">
            <div className="flex flex-wrap items-center gap-4">
              {g && (
                <svg viewBox="0 0 100 56" className="h-16 w-28" role="img" aria-label={`Risk gauge: ${g.label}`} data-testid="risk-gauge">
                  <path d={g.track} className="stroke-surface-2" strokeWidth={8} fill="none" />
                  {g.path && <path d={g.path} className={TONE[g.tone]} strokeWidth={8} fill="none" strokeLinecap="round" />}
                  <text x={50} y={52} textAnchor="middle" className="fill-fg text-xs">{g.label}</text>
                </svg>
              )}
              <div className="text-xs text-fg-muted">
                <div>Quantity <span className="text-fg">{view.sizing.quantity}</span> (cap allows {view.sizing.suggested_quantity}) · risk at stop <span className="text-fg">{money(view.sizing.risk_amount)}</span> of cap {money(view.sizing.cap)} ({view.sizing.binding})</div>
                <div>Reward:risk {view.reward_risk ?? "-"}{view.min_reward_risk !== null ? ` (minimum ${view.min_reward_risk})` : ""}{view.costs?.net_reward_risk != null ? ` · after costs ${view.costs.net_reward_risk}` : ""}</div>
                {view.sizing.size_multiplier < 1 && <div>Size cut to {Math.round(view.sizing.size_multiplier * 100)}% by the guardian</div>}
              </div>
            </div>
            {view.costs && (
              <div className="text-xs" data-testid="cost-line">
                <div className="flex flex-wrap gap-x-3 gap-y-1 text-fg-muted">
                  {costRows(view.costs).map((r) => <span key={r.label}>{r.label} <span className="text-fg">{r.value}</span></span>)}
                </div>
                <div className="text-fg">Costs {money(view.costs.total)} (at the stop {money(view.costs.at_stop_total)}) · break-even move {view.costs.break_even_move ?? "-"} ({view.costs.break_even_pct ?? "-"}%)</div>
                <TextOnDemand id="mytrades.plan-costs" on={v2}><p className="text-xs text-fg-muted">{view.costs.basis}</p></TextOnDemand>
              </div>
            )}
            {[...view.errors, ...view.refusals].map((r) => <p key={r} className="text-xs text-down">{r}</p>)}
            {/* M68 PR-7 (TS-3, D4): no starting capital yet - asked here, then the plan is checked again. */}
            {v2 && needsCapital(view.refusals) && <CapitalAsk id="ticket-capital" onSaved={() => void preview()} />}
            {view.warnings.map((w) => <p key={w} className="text-xs text-warn">{w}</p>)}
            {view.can_place && view.confirm_label && (
              <button disabled={busy} onClick={() => void place()} data-testid="ticket-confirm"
                      className="min-h-[44px] rounded bg-info px-3 py-1 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50 sm:min-h-0">
                {view.confirm_label}
              </button>
            )}
          </div>
        )}
        {message && <p className="text-xs text-fg-muted" role="status">{message}</p>}

        {plans.length > 0 && (
          <details className="text-xs">
            <summary className="cursor-pointer text-fg-muted">Recent plans ({plans.length})</summary>
            <ul className="mt-1 space-y-1">
              {plans.slice(0, 10).map((p) => (
                <li key={p.id} className="text-fg-muted">
                  #{p.id} {p.status === "PROPOSED" && p.proposal ? `sent for approval (proposal #${p.proposal.id}, ${p.proposal.status.toLowerCase()})` : p.status} · {p.direction} {p.symbol} {p.setup_label} · R:R {p.reward_risk ?? "-"}{p.override ? " · override" : ""}
                  {!p.checklist_complete ? " · checklist skipped" : ""}{(p.status === "REFUSED" || p.status === "FAILED") && p.reasons[0] ? ` · ${p.reasons[0]}` : ""}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
      {v2 && <Details id="mytrades.plan-before-trade"><p>{status.exits}</p></Details>}
    </Card>
  );
}
