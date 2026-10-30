import { RefreshCcw, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { Dialog } from "../components/primitives/Dialog";
import { failedText } from "../trading/model";
import { Signed } from "../ui/Signed";
import { formatINR, formatPrice } from "../ui/format";
import { basket as defaultBasket, type BasketStore } from "./basket";
import {
  NOT_ATOMIC, STEP_ONE, STEP_TWO, existingExits, exitWords, legWords, netWords, pendingStore, pendingText, rollApi, rollIds, runRoll, writePending,
  type RollApi, type RollAsk, type RollExits, type RollPreview,
} from "./roll";

const BUTTON = "inline-flex h-11 items-center justify-center gap-space-2 rounded-control px-space-4 text-t14 font-semibold transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface md:h-9";

type Read = { status: "loading" } | { status: "ok"; value: RollPreview } | { status: "error"; text: string };

/**
 * TERM-2 R1: the roll's confirm sheet. It reads the preview (the exit of the current legs and the same strikes on the next
 * weekly, with the new legs' credit and max loss from the server's basket calculation) and asks once. The confirm runs
 * step 1 - the EXISTING exit (Exit now, or the group exit for a basket's legs) - and, only when every leg closed, step 2:
 * the new legs go into the one basket, where he reviews and places them (`onDone(text, ok, toBasket)`). The exit button is
 * never disabled, also when the preview could not be read (ADR-0004); a second tap while it runs is ignored.
 */
export function RollSheetV2({ ask, onClose, onDone, spot = null, api = rollApi, exits = existingExits, store = defaultBasket }: {
  ask: RollAsk | null; onClose: () => void; onDone: (text: string, ok: boolean, toBasket: boolean) => void; spot?: number | null;
  api?: RollApi; exits?: RollExits; store?: BasketStore;
}) {
  const [read, setRead] = useState<Read>({ status: "loading" });
  const running = useRef(false);
  const [busy, setBusy] = useState(false);
  const key = ask ? rollIds(ask).join(",") : "";
  const spotNow = useRef(spot);
  spotNow.current = spot;                                               // the spot when the sheet opens; a tick never re-reads
  useEffect(() => {
    if (!key) return undefined;
    let live = true;
    setRead({ status: "loading" });
    api.preview(key.split(",").map(Number), spotNow.current).then((value) => { if (live) setRead({ status: "ok", value }); },
      (e: unknown) => { if (live) setRead({ status: "error", text: failedText("The roll could not be prepared.", e) }); });
    return () => { live = false; };
  }, [key, api]);
  if (!ask) return null;
  const p = read.status === "ok" ? read.value : null;
  const ready = !!p && p.ok && p.legs.length > 0;

  async function go() {
    if (!ask || running.current) return;
    running.current = true;
    setBusy(true);
    try {
      const out = await runRoll(ask, read.status === "ok" ? read.value : null, exits, store);
      onDone(out.text, out.ok, out.toBasket);
    } finally {
      running.current = false;
      setBusy(false);
    }
  }

  const max = p?.numbers?.extremes;
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }} title={`Roll ${ask.title} to the next weekly?`}
      description={<span className="text-t14 text-fg" data-roll="not-atomic">{NOT_ATOMIC}</span>}
      footer={(
        <>
          <button type="button" onClick={onClose} className={`${BUTTON} border border-border bg-surface-1 text-fg hover:bg-surface-2`}>Keep them</button>
          {/* ADR-0004: the exit is never disabled here - not while the preview loads, not when it failed. */}
          <button type="button" onClick={() => { void go(); }} aria-busy={busy} data-testid="roll-confirm"
            className={`${BUTTON} bg-fg text-surface-1 hover:opacity-90`}>
            <RefreshCcw size={16} aria-hidden />{ready ? "Exit, then fill the basket" : "Exit now"}
          </button>
        </>
      )}>
      <div className="space-y-space-4" data-roll-preview={read.status}>
        {read.status === "loading" && <p className="text-t13 text-fg-muted">Reading the roll…</p>}
        {read.status === "error" && <p className="text-t13 text-warn" role="alert" data-roll="error">{read.text}</p>}
        {p && (p.errors.length > 0 || p.numbers_errors.length > 0) && (
          <ul className="space-y-space-1 rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn" role="alert" data-roll="problems">
            {[...p.errors, ...p.numbers_errors].map((e) => <li key={e}>{e}</li>)}
          </ul>
        )}
        {p && (
          <>
            <section aria-label={STEP_ONE} data-roll="exit" className="space-y-space-1">
              <h3 className="text-t13 font-semibold text-fg-muted">{STEP_ONE}</h3>
              <ul className="divide-y divide-subtle text-t14">
                {p.exit.map((e) => (
                  <li key={e.trade_id} className="flex items-baseline justify-between gap-space-3 py-space-1">
                    <span className="min-w-0 text-fg">{exitWords(e)}</span>
                    <span className="shrink-0 font-tabular text-fg-muted">{e.price === null ? "No price" : `${formatPrice(e.price)}${e.price_source === "last" ? " (last)" : ""}`}</span>
                  </li>
                ))}
              </ul>
            </section>
            {p.legs.length > 0 && (
              <section aria-label={STEP_TWO} data-roll="legs" className="space-y-space-1">
                <h3 className="text-t13 font-semibold text-fg-muted">{STEP_TWO}</h3>
                <ul className="divide-y divide-subtle text-t14">
                  {p.legs.map((l) => (
                    <li key={l.label + l.side} className="flex items-baseline justify-between gap-space-3 py-space-1">
                      <span className="min-w-0 text-fg">{legWords(l)}</span>
                      <span className="shrink-0 font-tabular text-fg-muted">{l.price === null ? "No price" : formatPrice(l.price)}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <dl className="rounded-control border border-subtle px-space-3 text-t14" data-roll="figures">
              {p.exit_value !== null && <Row label="Exit" value={<Signed value={p.exit_value} kind="money" />} testId="exit-value" />}
              {p.numbers && <Row label="New legs" value={<Signed value={p.numbers.net_premium} kind="money" />} testId="new-premium" />}
              {p.net !== null && <Row label="Roll" value={<span className="font-semibold">{netWords(p.net)}</span>} testId="net" />}
              {max && <Row label="Max loss (new legs)" value={max.unbounded_loss ? "Unlimited" : max.max_loss === null ? "–" : formatINR(Math.abs(max.max_loss))} testId="max-loss" />}
            </dl>
          </>
        )}
        <p className="flex items-start gap-space-2 text-t12 text-fg-muted">
          <TriangleAlert size={14} aria-hidden className="mt-px shrink-0" />
          <span>{p?.steps ?? "Step 1 closes these legs through Exit now. Step 2 puts the new legs in your basket: you review and place them."}</span>
        </p>
      </div>
    </Dialog>
  );
}

function Row({ label, value, testId }: { label: string; value: ReactNode; testId: string }) {
  return (
    <div className="flex items-center justify-between gap-space-3 border-t border-subtle py-space-2 first:border-t-0" data-figure={testId}>
      <dt className="text-t13 text-fg-muted">{label}</dt>
      <dd className="text-right font-tabular text-fg">{value}</dd>
    </div>
  );
}

/**
 * TERM-2 R1: the roll that is half done (the old legs closed, the new legs in the basket but not placed) - shown above
 * the panels with the incomplete-orders notice, until the basket is placed or he says he is done. Nothing here places.
 */
export function RollPendingV2({ onBasket }: { onBasket: () => void }) {
  const pending = useSyncExternalStore(pendingStore.subscribe, pendingStore.snapshot, pendingStore.snapshot);
  if (!pending) return null;
  return (
    <section className="space-y-space-2 rounded-panel border border-warn/40 bg-warn-tint px-space-4 py-space-3" data-roll-pending aria-label="Roll half done">
      <p className="flex items-start gap-space-2 text-t14 font-semibold text-fg" role="alert">
        <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" />
        <span>{pendingText(pending)}</span>
      </p>
      <div className="flex flex-wrap gap-space-2">
        <button type="button" onClick={onBasket} data-roll-pending-action="basket"
          className="min-h-11 rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand">Open the basket</button>
        <button type="button" onClick={() => writePending(null)} data-roll-pending-action="done"
          className="min-h-11 rounded-control border border-border bg-surface-1 px-space-4 text-t14 font-semibold text-fg">Done with this roll</button>
      </div>
    </section>
  );
}
