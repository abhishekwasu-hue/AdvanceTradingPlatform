/**
 * Terminal master order Part 7, SMART EXECUTION (flag `smart_execution`, behind `ui_v2`; backend
 * app/execution/smart_execution.py). Information and the user's own taps only - nothing here places an order by itself,
 * and exits are never blocked (ADR-0004):
 * - `SmartConfirmV2`: on the basket's confirm step, the order the legs go in (hedges first), how a large leg is split
 *   at the exchange's freeze limit, the margin (the broker's with the hedge benefit, or ATP's estimate - labelled) and
 *   the free balance after the order;
 * - `IncompleteV2`: when a later leg did not fill, the clear state with "Complete the leg(s)" / "Exit the filled leg(s)" (T1-4);
 * - `SettlementWarningsV2`: on Positions, stock F&O positions that may settle by delivery, with the value in rupees.
 */
import { Layers, ShieldCheck, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { basketApi, type BasketApi, type CloseFilledResult, type SettlementView, type SmartView } from "./api";
import { completeLabel, exitFilledLabel } from "./incompleteApi";
import { moneyText, sideWord } from "./ticketModel";
import { formatNumber } from "../ui/format";

function lotsText(n: number): string {
  return `${formatNumber(n, 0)} lot${n === 1 ? "" : "s"}`;
}

export function SmartConfirmV2({ smart }: { smart: SmartView }) {
  const m = smart.margin;
  const short = m.free_after !== null && m.free_after < 0;
  return (
    <section className="space-y-space-3 rounded-control border border-subtle px-space-3 py-space-3" data-smart="confirm" aria-label="How the order goes">
      <div className="flex items-start gap-space-2">
        <ShieldCheck size={16} aria-hidden className="mt-0.5 shrink-0 text-brand" />
        <div className="min-w-0 space-y-space-1">
          <p className="text-t14 font-semibold text-fg">How the order goes</p>
          <ol className="space-y-space-1 text-t13 text-fg" data-smart="sequence">
            {smart.sequence.map((s) => (
              <li key={s.seq} data-smart-step={s.seq} data-role={s.role}>
                <span className="font-semibold">{s.seq}.</span> {sideWord(s.side)} {s.label} · {lotsText(s.lots)}
                <span className="text-fg-muted">{s.role === "hedge" ? " · hedge, goes first" : " · sold leg, after the hedges"}</span>
                {s.slices.length > 1 && (
                  <span className="block text-fg-muted" data-smart="slices">
                    <Layers size={12} aria-hidden className="mr-space-1 inline" />
                    {s.note ?? `Sent as ${s.slices.length} orders: ${s.slices.map((n) => lotsText(n)).join(" + ")}`}
                  </span>
                )}
              </li>
            ))}
          </ol>
          <p className="text-t13 text-fg-muted">{smart.incomplete_rule}</p>
          <p className="text-t13 text-fg-muted">{smart.exit_rule}</p>
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-x-space-3 gap-y-space-2 border-t border-subtle pt-space-3" data-smart="margin" data-source={m.source}>
        <div className="col-span-2">
          <dt className="text-t13 text-fg-muted">Margin needed</dt>
          <dd className="text-t18 font-semibold text-fg font-tabular" data-smart="required">{moneyText(m.required)}</dd>
          <dd className="text-t12 text-fg-muted" data-smart="margin-label">{m.label}</dd>
        </div>
        {m.benefit !== null && m.benefit > 0 && (
          <div className="col-span-2">
            <dt className="text-t13 text-fg-muted">Saved by the hedge</dt>
            <dd className="text-t14 text-fg font-tabular" data-smart="benefit">{moneyText(m.benefit)}</dd>
          </div>
        )}
        <div>
          <dt className="text-t13 text-fg-muted">{m.free_source === "practice" ? "Practice balance now" : "Free balance now"}</dt>
          <dd className="text-t14 text-fg font-tabular" data-smart="free-before">{moneyText(m.free_before)}</dd>
        </div>
        <div>
          <dt className="text-t13 text-fg-muted">Free after this order</dt>
          <dd className={short ? "text-t14 font-semibold text-warn font-tabular" : "text-t14 font-semibold text-fg font-tabular"} data-smart="free-after">
            {moneyText(m.free_after)}
          </dd>
        </div>
        {m.notes.length > 0 && (
          <dd className="col-span-2 space-y-space-1 text-t12 text-fg-muted">{m.notes.map((n) => <p key={n}>{n}</p>)}</dd>
        )}
      </dl>
    </section>
  );
}

/** The incomplete state (TM-11): what filled, what did not, and the two choices - only on the user's tap. */
export function IncompleteV2({ message, filledTradeIds, pendingCount, onCompleteRest, api = basketApi, onClosed }: {
  message: string; filledTradeIds: number[]; onCompleteRest: () => void; api?: Pick<BasketApi, "closeFilled">;
  /** The legs still to place (T1-4 wording: "Complete the leg" for one). */
  pendingCount?: number;
  onClosed?: (r: CloseFilledResult) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [closed, setClosed] = useState<CloseFilledResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function closeFilled() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.closeFilled(filledTradeIds);
      setClosed(r);
      onClosed?.(r);
    } catch (e) {
      setError(`${e instanceof Error ? e.message : String(e)} Each leg can still be closed with Exit now on Positions.`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-space-3 rounded-control border border-warn/40 bg-warn-tint px-space-3 py-space-3" data-smart="incomplete" aria-label="Incomplete order">
      <p className="flex items-start gap-space-2 text-t14 font-semibold text-fg" role="alert">
        <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" />
        <span>{message}</span>
      </p>
      {closed ? (
        <div className="space-y-space-1" data-smart="closed" data-state={closed.state}>
          <p className="text-t13 text-fg">{closed.message}</p>
          <ul className="space-y-space-1 text-t13 text-fg-muted">
            {closed.legs.map((l) => (
              <li key={l.trade_id} data-closed-leg={l.trade_id} data-status={l.status}>
                {l.seq}. {l.symbol}: {l.status === "CLOSED" ? `closed${l.exit_price !== null ? ` at ${formatNumber(l.exit_price, 2)}` : ""}` : l.reasons[0] ?? "not closed"}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="flex flex-wrap gap-space-2">
          <button type="button" onClick={onCompleteRest} disabled={busy} data-smart="complete-rest"
            className="min-h-11 flex-1 rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand disabled:opacity-50">
            {completeLabel(pendingCount ?? 2)}
          </button>
          <button type="button" onClick={() => { void closeFilled(); }} disabled={busy || filledTradeIds.length === 0} data-smart="close-filled"
            className="min-h-11 flex-1 rounded-control border border-border bg-surface-1 px-space-4 text-t14 font-semibold text-fg disabled:opacity-50">
            {busy ? "Closing…" : exitFilledLabel(filledTradeIds.length)}
          </button>
        </div>
      )}
      <p className="text-t12 text-fg-muted">Closing goes sold legs first, then the hedges.</p>
      {error && <p className="text-t13 text-warn" role="alert">{error}</p>}
    </section>
  );
}

/** Positions: stock F&O that may settle by delivery (information only, nothing is closed). */
export function SettlementWarningsV2({ api = basketApi }: { api?: Pick<BasketApi, "settlement"> }) {
  const [view, setView] = useState<SettlementView | null>(null);
  useEffect(() => {
    let live = true;
    api.settlement().then((v) => { if (live) setView(v); }).catch(() => { if (live) setView(null); });
    return () => { live = false; };
  }, [api]);
  return view ? <SettlementListV2 view={view} /> : null;
}

export function SettlementListV2({ view }: { view: SettlementView }) {
  if (view.warnings.length === 0) return null;
  return (
    <section className="space-y-space-2 rounded-panel border border-warn/40 bg-warn-tint px-space-4 py-space-3" data-smart="settlement" aria-label="Delivery ahead">
      <p className="flex items-center gap-space-2 text-t15 font-semibold text-fg">
        <TriangleAlert size={16} aria-hidden className="shrink-0 text-warn" />
        Delivery ahead: {view.warnings.length} position{view.warnings.length === 1 ? "" : "s"}
      </p>
      <ul className="space-y-space-2">
        {view.warnings.map((w) => (
          <li key={w.trade_id} className="text-t14 text-fg" data-settlement={w.trade_id} data-state={w.state}>
            {w.message}
            {w.delivery_value !== null && (
              <span className="block text-t13 text-fg-muted font-tabular">Delivery value {moneyText(w.delivery_value)}</span>
            )}
          </li>
        ))}
      </ul>
      <p className="text-t13 text-fg-muted">{view.note}</p>
    </section>
  );
}
