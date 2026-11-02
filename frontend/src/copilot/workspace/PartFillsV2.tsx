import { useCallback, useEffect, useRef, useState } from "react";
import { watchlistsV2Api } from "../../chart/watchlistsV2";
import { IncompleteCardV2 } from "../../fno/IncompleteOrdersV2";
import { incompleteApi, type IncompleteAction, type IncompleteApi, type IncompleteItem } from "../../fno/incompleteApi";
import { useConfirm } from "../../ui/ConfirmDialog";
import { formatNumber } from "../../ui/format";
import { useFeatures } from "../../ui/useFeatures";
import { rootOf } from "./positionCards";

/**
 * M76 MP-9 (owner order M76 E-2; flag `copilot_workspace`, the incomplete orders' own `smart_execution`): a spread that
 * filled only in part, inside the workspace for the chart's underlying - each leg as it stands (what filled, what is
 * still to place, "N of M filled" for a part fill) and the re-price helper: every leg still to place shows its NEW last
 * price, and "Complete the leg(s)" asks him to confirm with those prices first.
 *
 * No new order path: his confirm sends his existing answer "Complete the rest" (`POST /api/trading/incomplete/{id}/answer`,
 * backend app/execution/incomplete_structures.py) - the trading worker then sends the rest through every entry check and
 * the engine at the price read then (hedge legs first), PAPER and LIVE through the same functions; a second tap answers
 * "Already answered" and nothing is sent twice. "Exit the filled leg(s)" is the existing exit (sold legs first), never
 * held back or asked twice (ADR-0004). Nothing here acts by itself (ADR-0006).
 */
const POLL_MS = 15_000;

type Quotes = typeof watchlistsV2Api.quotes;

/** The confirm's words for "Complete the rest" with each leg's new last price (never a price made up). Pure. */
export function repriceWords(item: IncompleteItem, ltp: Readonly<Record<string, number | null>>): string {
  const legs = item.pending.map((l) => `${l.symbol}: last price ${typeof ltp[l.symbol] === "number" ? formatNumber(ltp[l.symbol] as number, 2) : "not known"}`);
  return `${legs.join("; ")}. The rest is sent on the next check (within a minute) at the price then, hedge legs first; `
    + "every entry check runs again. Nothing is sent twice.";
}

/** His incomplete orders on the chart's underlying. Pure. */
export function itemsHere(items: readonly IncompleteItem[], symbol: string): IncompleteItem[] {
  const root = rootOf(symbol);
  return items.filter((i) => rootOf(i.underlying) === root);
}

export function PartFillsV2({ symbol, api = incompleteApi, quotes = watchlistsV2Api.quotes }: { symbol: string; api?: IncompleteApi; quotes?: Quotes }) {
  const on = useFeatures().on("smart_execution");
  const [items, setItems] = useState<IncompleteItem[]>([]);
  const [ltp, setLtp] = useState<Record<string, number | null>>({});
  const [readAt, setReadAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const inFlight = useRef(new Set<string>());
  const confirmBox = useConfirm();
  const load = useCallback(async () => {
    const v = await api.list();
    const here = itemsHere(v.items, symbol);
    setItems(here); setReadAt(Date.now());
    const legs = here.flatMap((i) => i.pending.map((l) => ({ symbol: l.symbol, exchange: l.exchange ?? "NFO" })));
    if (!legs.length) return;
    const r = await quotes(legs);
    setLtp(Object.fromEntries(legs.map((l) => {
      const q = r.items.find((x) => x.symbol.toUpperCase() === l.symbol.toUpperCase());
      return [l.symbol, q && !q.missing && typeof q.ltp === "number" && q.ltp > 0 ? q.ltp : null];
    })));
  }, [api, quotes, symbol]);
  useEffect(() => {
    if (!on) return undefined;
    void load().catch(() => undefined);                 // intentional: polled; the card keeps its last read and the next poll tries again
    const poll = window.setInterval(() => { void load().catch(() => undefined); }, POLL_MS);
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { window.clearInterval(poll); window.clearInterval(tick); };
  }, [on, load]);
  if (!on || !items.length) return null;

  async function answer(item: IncompleteItem, action: IncompleteAction) {
    if (inFlight.current.has(item.group_id)) return;          // a second tap while the first is on its way: nothing more
    if (action === "complete_rest") {
      const ok = await confirmBox.ask(item.pending.length === 1 ? "Complete the leg at the new price?" : "Complete the legs at the new price?",
        repriceWords(item, ltp), item.pending.length === 1 ? "Complete the leg" : "Complete the legs");
      if (!ok) return;
    }
    inFlight.current.add(item.group_id);
    setBusy(item.group_id);
    try {
      const r = await api.answer(item.group_id, action);
      setNotes((n) => ({ ...n, [item.group_id]: r.message }));
      await load();
    } catch (e) {
      setNotes((n) => ({ ...n, [item.group_id]: `${e instanceof Error ? e.message : String(e)} Each leg can still be closed with Exit now.` }));
    } finally {
      inFlight.current.delete(item.group_id);
      setBusy(null);
    }
  }
  const elapsed = Math.max(0, Math.floor((now - readAt) / 1000));
  return (
    <div className="space-y-space-3" data-workspace-part-fills={items.length}>
      {items.map((item) => (
        <IncompleteCardV2 key={item.group_id} item={item} busy={busy === item.group_id} note={notes[item.group_id]} ltp={ltp}
          secondsLeft={item.seconds_left === null ? null : item.seconds_left - elapsed}
          onAnswer={(action) => { void answer(item, action); }} />
      ))}
      {confirmBox.dialog}
    </div>
  );
}

export default PartFillsV2;
