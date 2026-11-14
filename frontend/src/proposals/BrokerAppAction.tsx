import { Copy, ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import { request } from "../api/client";
import { cx } from "../components/primitives/cx";
import { Dialog } from "../components/primitives/Dialog";
import { isIndex } from "../paperbot/model";
import { useTradeSettings } from "../settings/tradeSettings";
import { useFeatures } from "../ui/useFeatures";
import { entryFacts, ownEntrySettings } from "./brokerFacts";
import { BROKER_TABLE, brokerRow, factRows, factsText, kiteEntryLink, openBrokerAppLink, type BrokerAppLink, type OrderFacts } from "./brokerLinks";

/**
 * Route 3 (BIZ-1 B2-B4): "Trade in your broker app" - one button for the ticket, the basket, Reverse, signal cards,
 * scan matches and paper-bot signals. The user places the order himself on his broker's own page; ATP sends nothing
 * to any broker. Where a verified pre-filled format exists (Kite Publisher) and fits his order, the broker's page opens
 * pre-filled; otherwise a small sheet shows his own order facts to copy and opens his broker's official home.
 * Shown only with `ui_v2` and `broker_app_links` on (the server's /paper/fno/broker-app says so too).
 */
export interface BrokerAppInfo { on: boolean; broker: string | null; publisher_api_key: string | null }

let cached: Promise<BrokerAppInfo | null> | null = null;
/** Test hook: forget the one read (each test starts fresh). */
export function resetBrokerAppCache(): void { cached = null; }

/** The organisation's "Trade in your broker app" facts, read once per page load while both flags are on; null otherwise. */
export function useBrokerApp(fetcher: typeof request = request): BrokerAppInfo | null {
  const features = useFeatures();
  const enabled = features.on("ui_v2") && features.on("broker_app_links");
  const [info, setInfo] = useState<BrokerAppInfo | null>(null);
  useEffect(() => {
    if (!enabled) { setInfo(null); return undefined; }
    let live = true;
    if (!cached) cached = fetcher<BrokerAppInfo>("/paper/fno/broker-app").catch(() => { cached = null; return null; });
    void cached.then((v) => { if (live) setInfo(v && v.on ? v : null); });
    return () => { live = false; };
  }, [enabled, fetcher]);
  return info;
}

const BUTTON = "inline-flex min-h-11 items-center justify-center gap-space-1 rounded-control border border-border bg-surface-1 px-space-3 text-t14 font-semibold text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50";
const SMALL = "inline-flex min-h-11 items-center gap-space-1 rounded-control px-space-2 text-t13 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

async function copy(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

/** The sheet's body (no dialog, for tests too): each set fact with Copy, "Copy all", and his broker's official home -
 * or every broker's in the table while his broker is not known. Nothing here sends anything anywhere. */
export function BrokerFactsBody({ facts, broker, copied = null, onCopy }: {
  facts: OrderFacts; broker: string | null; copied?: string | null; onCopy: (name: string, text: string) => void;
}) {
  const row = brokerRow(broker);
  const homes = row ? [row] : BROKER_TABLE;
  return (
    <>
      <dl className="divide-y divide-subtle rounded-control border border-subtle" data-broker-facts>
        {factRows(facts).map((r) => (
          <div key={r.name} className="flex min-h-11 items-center gap-space-2 px-space-3 py-space-1" data-fact={r.name}>
            <dt className="w-24 shrink-0 text-t13 text-fg-muted">{r.name}</dt>
            <dd className={cx("min-w-0 flex-1 break-words text-t14 font-tabular", r.value === null ? "text-fg-muted" : "text-fg")}>
              {r.value ?? "Your choice"}
            </dd>
            {r.value !== null && (
              <button type="button" className={SMALL} aria-label={`Copy ${r.name}`} onClick={() => onCopy(r.name, (r.value as string).replace(/,/g, ""))} data-copy={r.name}>
                <Copy size={14} aria-hidden />{copied === r.name ? "Copied" : "Copy"}
              </button>
            )}
          </div>
        ))}
      </dl>
      <div className="mt-space-3 flex flex-wrap items-center gap-space-2">
        <button type="button" className={BUTTON} onClick={() => onCopy("all", factsText(facts))} data-copy="all">
          <Copy size={16} aria-hidden />{copied === "all" ? "Copied" : "Copy all"}
        </button>
        {homes.map((b) => (
          <a key={b.id} href={b.home} target="_blank" rel="noopener noreferrer" className={BUTTON} data-broker-home={b.id}>
            <ExternalLink size={16} aria-hidden />Open {b.label}
          </a>
        ))}
      </div>
    </>
  );
}

/** The copy sheet: his order facts to copy, then his broker's own home. */
export function BrokerFactsSheet({ open, onOpenChange, facts, broker }: {
  open: boolean; onOpenChange: (open: boolean) => void; facts: OrderFacts; broker: string | null;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  const row = brokerRow(broker);
  async function onCopy(name: string, text: string) { if (await copy(text)) setCopied(name); }
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Trade in your broker app"
      description={<span data-broker-sheet-note>Copy your order, then place it in {row ? row.label : "your broker app"}. ATP places nothing.</span>}>
      <BrokerFactsBody facts={facts} broker={broker} copied={copied} onCopy={(n, t) => { void onCopy(n, t); }} />
    </Dialog>
  );
}

/**
 * The button. `prefill` (optional) returns the verified pre-filled link for his order, or null (then the sheet opens).
 * Renders nothing while the feature is off for him (`info` null).
 */
export function BrokerAppAction({ info, facts, prefill, testId, className, compact = false }: {
  info: BrokerAppInfo | null; facts: OrderFacts | null; prefill?: () => Promise<BrokerAppLink | null> | BrokerAppLink | null;
  testId: string; className?: string;
  /** A row action (scan matches): the small link style of the row's other actions. */
  compact?: boolean;
}) {
  const [sheet, setSheet] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!info || !info.on || !facts) return null;
  async function go() {
    setNote(null);
    setBusy(true);
    try {
      const link = prefill ? await prefill() : null;
      if (link) {
        openBrokerAppLink(link);
        setNote(`Opened in ${link.brokerLabel}. Review and place it there.`);
        return;
      }
    } catch (e) {
      setNote(`The pre-filled order could not be made (${e instanceof Error ? e.message : String(e)}). Copy it instead.`);
    } finally {
      setBusy(false);
    }
    setSheet(true);
  }
  return (
    <div className={cx(compact ? "inline-block" : "space-y-space-1", className)} data-broker-app={testId}>
      <button type="button" className={compact ? SMALL : cx(BUTTON, "w-full sm:w-auto")} onClick={() => void go()} disabled={busy} data-testid={`${testId}-broker-app`}>
        <ExternalLink size={compact ? 14 : 16} aria-hidden />Trade in your broker app
      </button>
      {note && <p role="status" className="text-t12 text-fg-muted" data-broker-app-note>{note}</p>}
      {sheet && <BrokerFactsSheet open={sheet} onOpenChange={setSheet} facts={facts} broker={info.broker} />}
    </div>
  );
}

/**
 * B3: the entry-side button for a signal card, a scan match or a paper bot's signal - the symbol and his signal's own
 * direction, everything else from HIS saved Trade settings (read only while the feature is on). Pre-filled in Kite only
 * for a plain stock Market order of a quantity he set; otherwise the copy sheet.
 */
export function EntryBrokerApp({ signal, testId, compact = false, className }: {
  signal: { symbol: string; exchange?: string | null; direction: "LONG" | "SHORT" | null; legs?: readonly string[] };
  testId: string; compact?: boolean; className?: string;
}) {
  const info = useBrokerApp();
  const own = ownEntrySettings(useTradeSettings(info !== null));
  if (!info) return null;
  const facts = entryFacts({ ...signal, index: isIndex(signal.symbol) }, own);
  return <BrokerAppAction info={info} facts={facts} testId={testId} compact={compact} className={className}
    prefill={info.broker === "zerodha" ? () => kiteEntryLink(facts, info.publisher_api_key) : undefined} />;
}
