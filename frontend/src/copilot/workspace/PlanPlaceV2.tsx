import { lazy, Suspense, useMemo, useState } from "react";
import { request } from "../../api/client";
import { errorSentence } from "../../api/errors";
import { useAuth } from "../../auth/AuthContext";
import type { TicketForm } from "../../components/paperTicket";
import { cx } from "../../components/primitives/cx";
import { BasketStore } from "../../fno/basket";
import { fnoApi, type OrderResult } from "../../fno/api";
import type { TicketTarget } from "../../fno/ticketModel";
import { money } from "../../optionsBuilder/model";
import { Details } from "../../ui/Details";
import { formatNumber } from "../../ui/format";
import { useFeatures } from "../../ui/useFeatures";
import type { TicketPrefill } from "../ticket/intent";
import { basketLegsOf, equityTicketOf, singleLegTicket, targetArm, type PlanState } from "./place";
import { openChart } from "../../chart/openChart";
import { similarApi } from "./similar";

/** M76 MP-14 (L-1): after a fill, the server records the open chart's area at entry on his new trades' journal entries
 * (it reads the chart itself, as of the entry; nothing about the order changes). Best effort: a failure changes nothing. */
function recordArea(tradeIds: (number | null | undefined)[]) {
  const chart = openChart();
  const ids = tradeIds.filter((t): t is number => typeof t === "number" && t > 0);
  if (!chart || !ids.length) return;
  similarApi.record(ids, chart.instrument, chart.tf).catch(() => undefined);   // intentional: the trade stands either way
}

// The ONE order ticket, the ONE basket and the equity ticket - loaded only when he opens them (the same parts the chain,
// the Terminal and Positions open). Nothing here has an order path of its own.
const OrderTicketV2 = lazy(() => import("../../fno/OrderTicketV2"));
const BasketPanelV2 = lazy(() => import("../../fno/BasketPanelV2"));
const PaperTicketCard = lazy(() => import("../../components/PaperTicketCard"));

/**
 * M75 W-7 (owner order M75 CW-5 / CW-7, flag `copilot_workspace`): placing the trade plan from the panel, in the same tab.
 * - One option or future leg: "Review and place" opens the ONE order ticket (`OrderTicketV2`) with the plan's lots,
 *   price and stop-loss marked "from your trade plan"; the ticket's own Review (confirm) and its Buy / Sell are his taps.
 * - Several legs: the ONE basket (`BasketPanelV2`, embedded) with the plan's legs in a basket of its own (memory only,
 *   the tab's basket is untouched); its confirm step and Execute are his taps; its exits (SX) are there.
 * - Equity: the equity ticket (`PaperTicketCard`) with the plan's numbers; its Review and Place are his taps.
 * After a fill the order's status, fill and position show here; a target he set can be armed on that position as a
 * waiting exit (the existing `POST /paper/fno/resting/exit`) - again only on his tap. PAPER = LIVE: the same routes as
 * everywhere (no mode in any body). The AI never presses any of these (ADR-0006). Exits are never blocked (ADR-0004).
 */
const BTN = "inline-flex min-h-11 items-center justify-center rounded-control px-space-4 text-t14 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 md:min-h-9";

type Opened = null | { kind: "ticket"; target: TicketTarget; prefill: TicketPrefill; side: "BUY" | "SELL" } | { kind: "basket"; store: BasketStore }
  | { kind: "equity"; initial: Partial<TicketForm> };

export function PlanPlaceV2({ plan }: { plan: PlanState }) {
  const { user } = useAuth();
  const features = useFeatures();
  const canTrade = !!user && user.role !== "VIEWER" && features.on("paper_account");
  const [opened, setOpened] = useState<Opened>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [placed, setPlaced] = useState<OrderResult | null>(null);
  const [armType, setArmType] = useState<"LIMIT" | "MARKET" | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const single = useMemo(() => singleLegTicket(plan), [plan]);
  const basketLegs = useMemo(() => basketLegsOf(plan), [plan]);
  const equity = useMemo(() => equityTicketOf(plan), [plan]);
  const arm = placed ? targetArm(plan, placed) : null;

  if (!canTrade) return <p className="text-t12 text-fg-muted" data-plan="place-off">Placing from here needs a practice account.</p>;

  async function openTicket() {
    if (!single) return;
    setBusy(true); setProblem(null); setPlaced(null); setArmed(null);
    try {
      const c = await fnoApi.contract(single.underlying, single.expiry, single.strike, single.right);
      setOpened({ kind: "ticket", side: single.side, prefill: single.prefill,
        target: { underlying: c.underlying, expiry: c.expiry, strike: c.strike, right: c.right, label: c.label, lotSize: c.lot_size, ltp: null, bid: null, ask: null } });
    } catch (e) {
      setProblem(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  async function armTarget() {
    if (!arm || !armType || !placed?.trade_id) return;
    setBusy(true); setProblem(null);
    try {
      await request("/paper/fno/resting/exit", { method: "POST", body: JSON.stringify({ trade_id: placed.trade_id, kind: "TARGET",
        order_type: armType, level: arm.level, client_ref: `plan-${placed.trade_id}-target` }) });
      setArmed(`Target armed at ${formatNumber(arm.level, 2)}.`);
    } catch (e) {
      setProblem(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  const ready = single || basketLegs || equity;
  return (
    <div className="space-y-space-2" data-plan="place">
      <div className="flex flex-wrap items-center gap-space-2">
        <button type="button" className={cx(BTN, "bg-brand text-on-brand")} disabled={!ready || busy} data-plan="review-place"
          onClick={() => {
            if (single) void openTicket();
            else if (basketLegs) setOpened({ kind: "basket", store: new BasketStore(basketLegs.map((l, i) => ({ ...l, id: `plan-${i}` })), null) });
            else if (equity) setOpened({ kind: "equity", initial: equity });
          }}>
          Review and place
        </button>
        {!ready && <span className="text-t12 text-fg-muted" data-plan="place-missing">Add a leg (or fill in the equity side, quantity and price) to review an order.</span>}
      </div>
      <Details id="copilot.plan.place" className="mt-0">
        <p className="text-t13 text-fg-muted">The order opens in the same ticket as the option chain (or the basket, or the equity ticket). You review it
          and press Place yourself; the Copilot never places, changes or cancels an order. Practice mode (PAPER).</p>
      </Details>
      {problem && <p role="alert" className="text-t13 text-fg" data-plan="place-problem">{problem}</p>}

      {placed && (
        <dl className="divide-y divide-subtle rounded-panel border border-subtle px-space-3 text-t13" data-plan="placed" data-status={placed.status}>
          <div className="flex justify-between gap-space-3 py-space-2"><dt className="text-fg-muted">Order</dt>
            <dd className={cx("font-semibold", placed.status === "PLACED" ? "text-up" : "text-down")}>{placed.status === "PLACED" ? "Placed" : "Not placed"}</dd></div>
          {placed.filled && (
            <div className="flex justify-between gap-space-3 py-space-2"><dt className="text-fg-muted">Filled</dt>
              <dd className="font-tabular text-fg">{placed.filled.lots} lots at {formatNumber(placed.filled.price, 2)}</dd></div>
          )}
          {placed.reasons.length > 0 && <div className="py-space-2 text-fg">{placed.reasons.join(" ")}</div>}
          {placed.trade_id && <div className="py-space-2 text-fg-muted">The position is open; Exit now always works from Positions and the chain.</div>}
          {arm && !armed && (
            <div className="space-y-space-2 py-space-2" data-plan="arm-target">
              <p className="text-fg">Your target: {formatNumber(arm.level, 2)} ({money(arm.rupees)}). Arm it on this position?</p>
              <div role="radiogroup" aria-label="Target order type" className="flex gap-space-1">
                {(["LIMIT", "MARKET"] as const).map((t) => (
                  <button key={t} type="button" role="radio" aria-checked={armType === t} onClick={() => setArmType(t)} data-option={t}
                    className={cx(BTN, "border", armType === t ? "border-brand bg-surface-2 text-fg" : "border-border text-fg-muted")}>{t === "LIMIT" ? "Limit" : "Market"}</button>
                ))}
              </div>
              <button type="button" className={cx(BTN, "border border-border text-fg")} disabled={!armType || busy} onClick={() => void armTarget()} data-plan="arm">Arm the target</button>
            </div>
          )}
          {armed && <div className="py-space-2 text-up" data-plan="armed">{armed}</div>}
        </dl>
      )}

      <Suspense fallback={null}>
        {opened?.kind === "ticket" && (
          <OrderTicketV2 open target={opened.target} side={opened.side} prefill={opened.prefill} onClose={() => setOpened(null)}
            onPlaced={(r) => { setPlaced(r); setArmed(null); recordArea([r.trade_id]); }} />
        )}
        {opened?.kind === "basket" && (
          <div className="rounded-panel border border-subtle" data-plan="basket">
            <BasketPanelV2 embedded open store={opened.store} onClose={() => setOpened(null)} chain={{ symbol: plan.underlying, live: null, expiries: [] }}
              onPlaced={(r) => recordArea(r.legs.map((l) => l.trade_id))} />
          </div>
        )}
        {opened?.kind === "equity" && <div data-plan="equity-ticket"><PaperTicketCard canTrade initial={opened.initial} /></div>}
      </Suspense>
    </div>
  );
}

export default PlanPlaceV2;
