/**
 * AUD-T2: the expiry check on Positions (flags `ui_v2` + `expiry_checklist`; src/fno/expiryChecklist.ts). Shown only
 * when a position expires today or next session. What needs his eye stays open (when it expires, in or out of the money,
 * delivery); the settlement explanations and his broker's rule (with the broker's own help link) live in Details.
 * "Add to basket" only adds the next-expiry contract to the basket - nothing is placed here.
 */
import { CalendarClock } from "lucide-react";
import { useEffect, useState } from "react";
import { ChipV2 } from "../dashboard/CardV2";
import { Details } from "../ui/Details";
import { basket as defaultBasket, type BasketStore } from "./basket";
import { addNextExpiry, expiryChecklistApi, type ExpiryChecklist, type ExpiryLeg, type ExpiryRow } from "./expiryChecklist";

export default function ExpiryChecklistV2({ api = expiryChecklistApi, store = defaultBasket }: {
  api?: Pick<typeof expiryChecklistApi, "get">;
  store?: BasketStore;
}) {
  const [view, setView] = useState<ExpiryChecklist | null>(null);
  useEffect(() => {
    let live = true;
    api.get().then((v) => { if (live) setView(v); }).catch(() => { if (live) setView(null); });
    return () => { live = false; };
  }, [api]);
  return view ? <ExpiryChecklistListV2 view={view} store={store} /> : null;
}

/** The one tap: adds the leg to the basket (nothing placed) and says what happened. */
export function addToBasketNote(store: BasketStore, leg: ExpiryLeg): string {
  return addNextExpiry(store, leg) ? `Added to basket: ${leg.label}. Review it there.` : "The basket is full. Remove a leg first.";
}

const MONEY_CHIP: Record<string, string> = { in: "In the money", out: "Out of the money", at: "At the money" };

function RowV2({ row, store }: { row: ExpiryRow; store: BasketStore }) {
  const [note, setNote] = useState<string | null>(null);
  const leg = row.basket_leg;
  return (
    <li className="space-y-space-1 border-t border-subtle pt-space-2 first:border-t-0 first:pt-0" data-expiry-row={row.trade_id}
      data-when={row.when} data-moneyness={row.moneyness ?? "unknown"} data-settlement={row.settlement}>
      <p className="text-t14 font-semibold text-fg">{row.headline}</p>
      <div className="flex flex-wrap gap-space-1">
        {row.moneyness && <ChipV2 kind={row.moneyness === "in" ? "warn" : "outline"}>{MONEY_CHIP[row.moneyness]}</ChipV2>}
        <ChipV2 kind={row.settlement === "delivery" ? "warn" : "outline"}>{row.settlement === "delivery" ? "Delivery" : "Cash settled"}</ChipV2>
      </div>
      {row.money_text && !row.priced && <p className="text-t13 text-fg-muted" data-expiry="no-price">{row.money_text}</p>}
      {row.settlement === "delivery" && <p className="text-t13 text-fg">{row.settlement_text}</p>}
      <div className="flex flex-wrap items-center gap-space-2">
        <span className="text-t13 text-fg-muted" data-expiry="next">{row.next_text}</span>
        {leg && (
          <button type="button" data-expiry="add-to-basket"
            className="min-h-11 rounded-control border border-border bg-surface-1 px-space-3 text-t13 font-semibold text-fg md:min-h-0 md:py-space-1"
            onClick={() => setNote(addToBasketNote(store, leg))}>
            Add to basket
          </button>
        )}
      </div>
      {note && <p className="text-t13 text-fg-muted" role="status">{note}</p>}
    </li>
  );
}

export function ExpiryChecklistListV2({ view, store = defaultBasket }: { view: ExpiryChecklist; store?: BasketStore }) {
  if (view.rows.length === 0) return null;
  return (
    <section className="space-y-space-2 rounded-panel border border-subtle bg-surface-1 px-space-4 py-space-3" data-block="expiry-checklist"
      aria-label="Expiry check">
      <p className="flex items-center gap-space-2 text-t15 font-semibold text-fg">
        <CalendarClock size={16} aria-hidden className="shrink-0 text-brand" />
        Expiry check: {view.rows.length} position{view.rows.length === 1 ? "" : "s"}
      </p>
      <ul className="space-y-space-2">
        {view.rows.map((r) => <RowV2 key={r.trade_id} row={r} store={store} />)}
      </ul>
      <Details id="positions.expiry-checklist">
        {view.notes.map((n) => <p key={n}>{n}</p>)}
        {view.brokers.map((b) => (
          <p key={b.broker} data-broker={b.broker}>
            {b.note}{" "}
            <a href={b.url} target="_blank" rel="noopener noreferrer" className="text-brand underline">{b.name} help page</a>
          </p>
        ))}
      </Details>
    </section>
  );
}
