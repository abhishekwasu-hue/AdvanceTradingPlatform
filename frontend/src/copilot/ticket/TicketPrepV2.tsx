import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { errorSentence } from "../../api/errors";
import { fnoApi, type Side } from "../../fno/api";
import { sideWord, type TicketTarget } from "../../fno/ticketModel";
import { Combobox } from "../../ui/Combobox";
import { Details } from "../../ui/Details";
import { useFeatures } from "../../ui/useFeatures";
import { matchExpiry, parseTicketAsk, prefillChips } from "./intent";

// The ONE order ticket, loaded only when he opens it (the same ticket the chain and Positions open).
const OrderTicketV2 = lazy(() => import("../../fno/OrderTicketV2"));

/**
 * M72 PR-9 (CP-9, flag `copilot_ticket`): under a Copilot answer to a message that names a contract ("Sell 2 lots NIFTY
 * 25000 CE at 85, stop 120"), one card that PREPARES the order ticket in place: the contract and the values he typed, each
 * marked "from your message"; the expiry from the list (his typed one when it is on it, else he picks - never pre-chosen);
 * the side from his words, else he picks. "Open the ticket" opens the same ticket the chain opens, where he reviews and
 * presses Place himself. This card never previews or places anything (ADR-0006).
 */
const BTN = "inline-flex min-h-11 items-center justify-center rounded-control px-space-4 text-t14 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50";

export function TicketPrepV2({ question }: { question: string }) {
  const on = useFeatures().on("copilot_ticket");
  const ask = useMemo(() => parseTicketAsk(question), [question]);
  const [expiries, setExpiries] = useState<string[] | null>(null);
  const [expiry, setExpiry] = useState<string>("");
  const [side, setSide] = useState<Side | null>(ask?.side ?? null);
  const [target, setTarget] = useState<TicketTarget | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!on || !ask) return undefined;
    let live = true;
    api.optionChainExpiries(ask.underlying).then((r) => {
      if (!live) return;
      const list = r.expiries.map((e) => e.date);
      setExpiries(list);
      setExpiry(matchExpiry(ask.expiryWords, list) ?? "");
    }, () => { if (live) setExpiries([]); });
    return () => { live = false; };
  }, [on, ask]);

  if (!on || !ask) return null;
  const contract = ask.right === "FUT" ? `${ask.underlying} FUT` : `${ask.underlying} ${ask.strike} ${ask.right}`;
  const chips = prefillChips(ask.prefill);

  async function openTicket() {
    if (!ask || !expiry || !side) return;
    setBusy(true); setProblem(null);
    try {
      const c = await fnoApi.contract(ask.underlying, expiry, ask.strike, ask.right);
      setTarget({ underlying: c.underlying, expiry: c.expiry, strike: c.strike, right: c.right, label: c.label, lotSize: c.lot_size, ltp: null, bid: null, ask: null });
    } catch (e) {
      setProblem(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-space-3 space-y-space-2 rounded-panel border border-border p-space-3" data-ticket-prep>
      <p className="text-t14 font-semibold text-fg">Prepare the order ticket · <span className="font-tabular">{contract}</span></p>
      {chips.length > 0 && (
        <p className="flex flex-wrap gap-space-1">
          {chips.map((c) => <span key={c.field} data-prefill={c.field} className="rounded-pill bg-surface-2 px-space-2 py-space-1 text-t12 text-fg-muted">{c.text}</span>)}
        </p>
      )}
      <div className="flex flex-wrap items-end gap-space-3">
        <div className="flex min-w-[10rem] flex-col gap-space-1">
          <span className="text-t13 text-fg-muted">Expiry</span>
          <Combobox label="Expiry" value={expiry} onChange={setExpiry} disabled={!expiries || expiries.length === 0} dataAttrs={{ "data-ticket-expiry": "" }}
            placeholder={expiries === null ? "Loading…" : expiries.length ? "Pick an expiry" : "No expiries known"}
            options={(expiries ?? []).map((d) => ({ value: d, label: d }))} />
        </div>
        <div role="radiogroup" aria-label="Side" className="flex gap-space-1" data-ticket-side>
          {(["BUY", "SELL"] as const).map((s) => (
            <button key={s} type="button" role="radio" aria-checked={side === s} onClick={() => setSide(s)}
              className={`${BTN} border ${side === s ? "border-brand bg-surface-2 text-fg" : "border-border text-fg-muted"}`}>{sideWord(s)}</button>
          ))}
        </div>
        <button type="button" className={`${BTN} bg-brand text-on-brand`} disabled={busy || !expiry || !side} onClick={() => void openTicket()} data-ticket-open>
          Open the ticket
        </button>
      </div>
      {problem && <p role="alert" className="text-t13 text-fg">{problem}</p>}
      <Details id="copilot.ticket" className="mt-0">
        <p className="text-t13 text-fg-muted">Only what you typed is filled in; anything else stays empty and the ticket asks. You review it and press the
          button yourself - the Copilot never places, changes or cancels an order.</p>
      </Details>
      {target && side && (
        <Suspense fallback={null}>
          <OrderTicketV2 open target={target} side={side} prefill={ask.prefill} onClose={() => setTarget(null)} />
        </Suspense>
      )}
    </div>
  );
}

export default TicketPrepV2;
