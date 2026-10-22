import { BookOpen, Crosshair, Info, ShoppingBasket, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { cx } from "../components/primitives/cx";
import { formatDateIST, formatNumber } from "../ui/format";
import { useFeatures } from "../ui/useFeatures";
import {
  FROM_YOUR_SETTINGS, GREEKS_NOTE, NOT_PICKED, STRUCTURE_EXPLAIN, TRADE_NOTE, greekLines, insightsApi, otherSetupExpiries, setupChainHref,
  setupTradeNote, type GreekValues, type InsightsApi, type RiskBudget, type RiskLine, type SetupItem, type SetupStrikes, type SetupTrade,
  type Structure,
} from "./insights";

/**
 * F&O workspace 8b, slice 1: the ATP-only information pieces (pure parts in insights.ts). None of them has an order
 * button, and none of them is read by one: the risk line is a line of text beside the max loss, never a refusal.
 */

/** The user's own risk budget, read once `on` is first true (null while unknown or unreadable: the line is then left
 * out). Kept for the life of the component: a risk setting changes rarely, and the line is information only. */
export function useRiskBudget(on: boolean, api: Pick<InsightsApi, "riskBudget"> = insightsApi): RiskBudget | null {
  const [budget, setBudget] = useState<RiskBudget | null>(null);
  useEffect(() => {
    if (!on || budget) return undefined;
    let alive = true;
    api.riskBudget().then((b) => { if (alive) setBudget(b); }).catch(() => { if (alive) setBudget(null); });
    return () => { alive = false; };
  }, [on, api, budget]);
  return budget;
}

/** 8b-d: the risk line (information only). */
export function RiskLineV2({ line }: { line: RiskLine | null }) {
  if (!line) return null;
  const Icon = line.tone === "warn" ? TriangleAlert : Info;
  return (
    <div role="note" data-risk-line={line.tone}
      className={cx("flex items-start gap-space-2 rounded-control px-space-3 py-space-2 text-t13", line.tone === "warn" ? "bg-warn-tint text-warn" : "bg-surface-2 text-fg")}>
      <Icon size={16} aria-hidden className="mt-0.5 shrink-0" />
      <span>
        <span className="block">{line.text}</span>
        <span className={cx("block text-t12", line.tone === "warn" ? "" : "text-fg-muted")}>{line.note}</span>
      </span>
    </div>
  );
}

/** 8b-h: "Explain this" - the structure and, for a basket, each Greek in plain words (education). */
export function ExplainV2({ structure, greeks, underlying, testId }: { structure: Structure; greeks?: GreekValues | null; underlying: string; testId: string }) {
  const s = STRUCTURE_EXPLAIN[structure];
  return (
    <details className="group rounded-control border border-subtle" data-explain={testId}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-space-2 px-space-3 text-t14 font-semibold text-brand">
        <BookOpen size={16} aria-hidden className="shrink-0" /> Explain this
        <span className="ml-auto text-t12 font-normal text-fg-muted">Learning</span>
      </summary>
      <div className="space-y-space-3 border-t border-subtle px-space-3 py-space-3 text-t13 text-fg">
        <div data-explain-structure={structure}>
          <p className="font-semibold">{s.name}</p>
          <p className="mt-space-1">{s.text}</p>
        </div>
        {greeks && (
          <dl className="space-y-space-2" data-explain-greeks>
            {greekLines(greeks, underlying).map((g) => (
              <div key={g.name}>
                <dt className="font-semibold">{g.name} <span className="font-tabular font-normal text-fg-muted">{g.value}</span></dt>
                <dd>{g.text}</dd>
              </div>
            ))}
          </dl>
        )}
        {greeks && <p className="text-t12 text-fg-muted">{GREEKS_NOTE}</p>}
      </div>
    </details>
  );
}

// --- 8b-a: the setup-aware chain -------------------------------------------------------------------------------------

export type SetupState = { status: "off" } | { status: "loading" } | { status: "error" } | { status: "ready"; data: SetupStrikes };

/** The strikes the user's own settings land on for HIS signal (`signal` from the address), read once the chain's price
 * is known. Off without a signal. */
export function useSetupStrikes(signal: string | null, spot: number | null, api: Pick<InsightsApi, "setupStrikes"> = insightsApi): SetupState {
  const [state, setState] = useState<SetupState>({ status: "off" });
  const ready = spot !== null && spot > 0;
  useEffect(() => {
    if (!signal) { setState({ status: "off" }); return undefined; }
    if (!ready) { setState({ status: "loading" }); return undefined; }
    let alive = true;
    setState({ status: "loading" });
    api.setupStrikes(signal, spot).then((data) => { if (alive) setState({ status: "ready", data }); })
      .catch(() => { if (alive) setState({ status: "error" }); });
    return () => { alive = false; };
    // The first price is enough: the marks do not jump about with every refresh of the chain.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signal, ready, api]);
  return state;
}

const RIGHT = { CE: "Call", PE: "Put" } as const;

/** The block above the chain when it was opened from one of the user's own signals. 8b-b: with `tradeOf` / `onTrade`
 * (the PAPER account on), each item has "Trade this setup", which opens the existing ticket / basket pre-filled. */
export function SetupPanelV2({ state, symbol, expiry, onExpiry, onClear, tradeOf, onTrade }: {
  state: SetupState; symbol: string; expiry: string | null; onExpiry: (expiry: string) => void; onClear: () => void;
  tradeOf?: (item: SetupItem) => SetupTrade; onTrade?: (t: SetupTrade) => void;
}) {
  if (state.status === "off") return null;
  const data = state.status === "ready" ? state.data : null;
  const elsewhere = data && data.underlying && data.underlying !== symbol;
  const others = data ? otherSetupExpiries(data, expiry) : [];
  return (
    <section aria-label="From your signal" data-setup-panel={state.status}
      className="space-y-space-2 rounded-panel border border-brand bg-surface-1 px-space-3 py-space-3 text-t13 text-fg md:px-space-4">
      <div className="flex flex-wrap items-center gap-space-2">
        <Crosshair size={16} aria-hidden className="shrink-0 text-brand" />
        <h2 className="text-t14 font-semibold">From your signal{data?.setup ? `: ${data.setup}` : ""}</h2>
        <span className="rounded-pill border border-brand px-space-2 py-0.5 text-t12 font-medium text-brand" data-setup="from-settings">{FROM_YOUR_SETTINGS}</span>
        <button type="button" onClick={onClear} className="ml-auto min-h-11 rounded-control px-space-2 text-t13 font-semibold text-fg-muted hover:text-fg" data-setup="clear">
          Hide marks
        </button>
      </div>
      {state.status === "loading" && <p aria-busy="true" className="text-fg-muted">Working out where your settings land…</p>}
      {state.status === "error" && <p role="status" className="text-fg-muted">Your settings could not be read just now. The chain works as usual.</p>}
      {data && (
        <>
          <p className="text-t12 text-fg-muted">{NOT_PICKED}</p>
          {elsewhere && <p role="status" data-setup="other-symbol">This signal is on {data.underlying}. Pick {data.underlying} above to see its marks.</p>}
          {data.message && <p role="status" data-setup="message">{data.message}</p>}
          {!elsewhere && data.reference && data.items.length > 0 && (
            <p>Worked out at {data.reference.text} <span className="font-tabular text-fg-muted">{formatNumber(data.reference.price, 2)}</span>. The marked strikes are shaded on the chain.</p>
          )}
          {!elsewhere && data.items.map((item) => (
            <div key={item.id} className="rounded-control bg-surface-2 px-space-3 py-space-2" data-setup-item={item.id}>
              <p className="font-medium">{item.settings}</p>
              {item.legs && (
                <ul className="mt-space-1 flex flex-wrap gap-x-space-3 gap-y-space-1 font-tabular">
                  {item.legs.map((l) => (
                    <li key={`${l.label}-${l.strike}-${l.right}`}>{l.label}: {formatNumber(l.strike, Number.isInteger(l.strike) ? 0 : 2)} {RIGHT[l.right]}{l.ratio > 1 ? ` × ${l.ratio}` : ""}</li>
                  ))}
                </ul>
              )}
              {item.expiry && <p className="text-t12 text-fg-muted">Expiry {formatDateIST(item.expiry)}</p>}
              {item.note && <p className="text-t12 text-fg-muted">{item.note}</p>}
              {tradeOf && onTrade && <TradeSetupV2 trade={tradeOf(item)} onTrade={onTrade} />}
            </div>
          ))}
          {!elsewhere && others.length > 0 && (
            <div className="flex flex-wrap items-center gap-space-2" data-setup="other-expiry">
              <span>Your settings use another expiry:</span>
              {others.map((e) => (
                <button key={e} type="button" onClick={() => onExpiry(e)} className="min-h-11 rounded-pill border border-border px-space-3 font-semibold text-fg hover:bg-surface-2">
                  Show {formatDateIST(e)}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}

/** 8b-b: "Trade this setup" on one item - opens the ticket / basket pre-filled; says why when it cannot. Never places. */
function TradeSetupV2({ trade, onTrade }: { trade: SetupTrade; onTrade: (t: SetupTrade) => void }) {
  if (trade.kind === "none") return <p className="mt-space-2 text-t12 text-fg-muted" data-setup-trade="none">{trade.problem}</p>;
  return (
    <div className="mt-space-2 space-y-space-1" data-setup-trade={trade.kind}>
      <button type="button" onClick={() => onTrade(trade)} data-setup="trade"
        className="inline-flex min-h-11 items-center gap-space-2 rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        <ShoppingBasket size={16} aria-hidden /> Trade this setup
      </button>
      <p className="text-t12 text-fg-muted">{setupTradeNote(trade)} {TRADE_NOTE}</p>
    </div>
  );
}

/** On a signal of the user's own with a side, on a symbol with an option chain: "Your strikes on the chain" (ui_v2): the
 * chain opens with the strikes his own settings land on marked. 8b-b: with the PAPER account on, "Trade this setup"
 * opens the same chain and the pre-filled ticket / basket at once (he still reviews and places it himself). */
export function SetupChainLinkV2({ item }: { item: { id: string; symbol: string; direction: "LONG" | "SHORT" | null } }) {
  const features = useFeatures();
  const href = features.on("ui_v2") ? setupChainHref(item) : null;
  const trade = href && features.on("paper_account") ? setupChainHref(item, true) : null;
  if (!href) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-space-4">
      <Link to={href} data-setup-link className="inline-flex min-h-11 items-center gap-space-1 text-t14 font-semibold text-brand hover:underline">
        <Crosshair size={16} aria-hidden className="shrink-0" /> Your strikes on the chain
      </Link>
      {trade && (
        <Link to={trade} data-setup-trade-link className="inline-flex min-h-11 items-center gap-space-1 text-t14 font-semibold text-brand hover:underline">
          <ShoppingBasket size={16} aria-hidden className="shrink-0" /> Trade this setup
        </Link>
      )}
    </span>
  );
}
