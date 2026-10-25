import { ChartCandlestick, ShoppingBasket, Table2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { cx } from "../components/primitives/cx";
import { CardV2 } from "../dashboard/CardV2";
import { formatCount } from "../optionChain/model";
import type { LiveChain } from "../optionChain/types";
import { chartsPath } from "../shell/shellRoutes";
import type { OHLCVBar } from "../types";
import { EMPTY, formatDateIST, formatNumber, formatPrice } from "../ui/format";
import { useBasket } from "./basket";
import { CandleSnapshotV2 } from "./CandleSnapshotV2";
import CockpitV2, { type CockpitApi } from "./CockpitV2";
import type { CockpitUnit } from "./cockpit";
import { DESK_NOTE, PANELS, basketOn, chainAround, deskUnderlyings, heldOn, type DeskPanel } from "./desk";
import { SubTabs } from "../ui/SubTabs";

/**
 * F&O workspace 8b-i, slice 4: the F&O cockpit as a desk on My positions (pure parts in desk.ts). On a desktop the
 * cockpit card ("F&O at a glance": his lines, P&L, Greeks, the most they can lose, expiries, alerts) sits beside a chart
 * of the underlying with his own strikes as lines, the part of the chain around his strikes and his basket - one screen.
 * On a phone a quick switch shows one panel at a time. Everything is read once when it opens (the underlying's candles,
 * one chain) and nothing here trades: the chart, the chain and the basket open on their own pages to work on, and every
 * Exit stays on the position rows below (ADR-0004).
 */
export interface DeskApi {
  candles: (symbol: string, exchange: string) => Promise<OHLCVBar[]>;
  chain: (underlying: string, expiry: string) => Promise<LiveChain>;
}
const defaultApi: DeskApi = {
  candles: async (symbol, exchange) => {
    const out = await api.marketDataCandles([symbol], "15min", 3, exchange);
    const entry = out.symbols?.[symbol.toUpperCase()] ?? out.symbols?.[symbol];
    if (!entry || entry.error) throw new Error(entry?.error ?? "no candles");
    return entry.bars.slice(-90);
  },
  chain: (underlying, expiry) => api.optionChainLive(underlying, expiry),
};

type Read<T> = { status: "loading" } | { status: "error" } | { status: "ok"; value: T };

function useRead<T>(key: string, read: () => Promise<T>): Read<T> {
  const [state, setState] = useState<{ key: string; read: Read<T> }>({ key: "", read: { status: "loading" } });
  const latest = useRef(read);
  latest.current = read;
  useEffect(() => {
    if (!key) return undefined;
    let alive = true;
    latest.current().then((value) => { if (alive) setState({ key, read: { status: "ok", value } }); })
      .catch(() => { if (alive) setState({ key, read: { status: "error" } }); });
    return () => { alive = false; };
  }, [key]);
  return state.key === key ? state.read : { status: "loading" };
}

export default function CockpitDeskV2({ units, alertsOn, deskApi = defaultApi, cockpitApi }: {
  units: readonly CockpitUnit[]; alertsOn: boolean; deskApi?: DeskApi; cockpitApi?: CockpitApi;
}) {
  const underlyings = useMemo(() => deskUnderlyings(units), [units]);
  const [picked, setPicked] = useState<string | null>(null);
  const [panel, setPanel] = useState<DeskPanel>("glance");
  const focus = underlyings.find((u) => u.underlying === picked) ?? underlyings[0] ?? null;
  const candles = useRead(focus ? `${focus.spotSymbol}|${focus.spotExchange}` : "",
    () => (focus ? deskApi.candles(focus.spotSymbol, focus.spotExchange) : Promise.resolve([])));
  const chain = useRead(focus ? `${focus.underlying}|${focus.expiry}` : "",
    () => (focus ? deskApi.chain(focus.underlying, focus.expiry) : Promise.reject(new Error("nothing held"))));
  const basket = useBasket();
  if (!focus) return null;
  const held = heldOn(units, focus.underlying, focus.expiry);
  const show = (p: DeskPanel) => cx(panel === p ? "block" : "hidden", "lg:block");
  return (
    <section aria-label="Your F&O desk" data-block="fno-desk" className="space-y-space-3">
      <div className="lg:hidden">
        <SubTabs label="Show" value={panel} onChange={setPanel} full data={{ desk: "switch" }}
          items={PANELS.map((p) => ({ key: p.key, label: p.label, data: { "desk-tab": p.key } }))} />
      </div>
      {underlyings.length > 1 && (
        <div className="flex flex-wrap items-center gap-space-2 text-t13" data-desk="underlyings">
          <span className="text-fg-muted">Panels follow</span>
          {underlyings.map((u) => (
            <button key={u.underlying} type="button" aria-pressed={u.underlying === focus.underlying} onClick={() => setPicked(u.underlying)}
              className={cx("inline-flex h-11 items-center rounded-control border px-space-3 font-semibold md:h-9",
                u.underlying === focus.underlying ? "border-brand text-brand" : "border-border text-fg")}>
              {u.underlying}
            </button>
          ))}
        </div>
      )}
      <div className={show("glance")} data-desk-panel="glance"><CockpitV2 units={units} alertsOn={alertsOn} cockpitApi={cockpitApi} /></div>
      <div className="grid gap-space-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="min-w-0 space-y-space-4">
          <div className={show("chart")} data-desk-panel="chart">
            <CardV2 testId="fno-desk-chart" more={{ label: "Open the chart", to: chartsPath(focus.spotSymbol) }}
              title={<span className="inline-flex items-center gap-space-2"><ChartCandlestick size={18} aria-hidden className="text-brand" />{focus.spotSymbol}</span>}>
              {candles.status === "loading" ? <p className="text-t13 text-fg-muted">Loading the chart…</p>
                : candles.status === "error" || !candles.value.length ? <p className="text-t13 text-fg-muted">The chart is not available from your data connection just now.</p>
                  : (
                    <>
                      <CandleSnapshotV2 bars={candles.value} levels={held.map((h) => ({ price: h.strike, label: h.label }))} testId="desk" height={240}
                        title={`${focus.spotSymbol}, 15-minute candles`} summary={`The last few sessions of ${focus.spotSymbol} with your strikes as lines.`} />
                      <p className="text-t12 text-fg-muted">15-minute candles. Dashed lines: the strikes you hold.</p>
                    </>
                  )}
            </CardV2>
          </div>
        </div>
        <div className="min-w-0 space-y-space-4">
          <div className={show("chain")} data-desk-panel="chain">
            <CardV2 testId="fno-desk-chain" more={{ label: "Open the chain", to: `/option-chain?underlying=${encodeURIComponent(focus.underlying)}` }}
              title={<span className="inline-flex items-center gap-space-2"><Table2 size={18} aria-hidden className="text-brand" />Chain near your strikes</span>}>
              <p className="mb-space-2 text-t12 text-fg-muted">{focus.underlying} · {formatDateIST(focus.expiry)} expiry</p>
              {chain.status === "loading" ? <p className="text-t13 text-fg-muted">Loading the chain…</p>
                : chain.status === "error" ? <p className="text-t13 text-fg-muted">The chain could not be read just now.</p>
                  : <ChainRows chain={chain.value} held={held} />}
            </CardV2>
          </div>
          <div className={show("basket")} data-desk-panel="basket">
            <CardV2 testId="fno-desk-basket" count={basketOn(basket, focus.underlying).length}
              more={{ label: "Open the basket", to: `/option-chain?underlying=${encodeURIComponent(focus.underlying)}&basket=open` }}
              title={<span className="inline-flex items-center gap-space-2"><ShoppingBasket size={18} aria-hidden className="text-brand" />Your basket</span>}>
              {basketOn(basket, focus.underlying).length === 0 ? (
                <p className="text-t13 text-fg-muted">No legs on {focus.underlying} in your basket. Build one on the chain to see its payoff and margin.</p>
              ) : (
                <ul className="divide-y divide-subtle text-t13" data-desk="basket-legs">
                  {basketOn(basket, focus.underlying).map((l) => (
                    <li key={l.id} className="flex items-baseline justify-between gap-space-2 py-space-1">
                      <span className="min-w-0 truncate text-fg">{l.side === "SELL" ? "Sell" : "Buy"} {l.label ?? `${formatNumber(l.strike, 0)} ${l.right}`}</span>
                      <span className="shrink-0 font-tabular text-fg-muted">{l.lots} lot{l.lots === 1 ? "" : "s"}</span>
                    </li>
                  ))}
                </ul>
              )}
            </CardV2>
          </div>
          <p className="text-t12 text-fg-muted">{DESK_NOTE}</p>
        </div>
      </div>
    </section>
  );
}

export function ChainRows({ chain, held }: { chain: LiveChain; held: readonly { strike: number; right: "CE" | "PE"; label: string }[] }) {
  const rows = chainAround(chain.rows, held.map((h) => h.strike), chain.summary.atm_strike);
  if (!rows.length) return <p className="text-t13 text-fg-muted">The chain on this expiry has no strikes to show.</p>;
  const mine = (strike: number, right: "CE" | "PE") => held.some((h) => h.strike === strike && h.right === right);
  const cell = (on: boolean) => cx("px-space-1 py-space-1 text-right font-tabular", on ? "font-semibold text-brand" : "text-fg");
  return (
    <table className="w-full table-fixed text-t13" data-desk="chain-rows">
      <thead>
        <tr className="text-t12 text-fg-muted">
          <th className="px-space-1 text-right font-medium">Call</th><th className="px-space-1 text-right font-medium">Call OI</th>
          <th className="px-space-1 text-center font-medium">Strike</th>
          <th className="px-space-1 text-right font-medium">Put</th><th className="px-space-1 text-right font-medium">Put OI</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-subtle">
        {rows.map((r) => (
          <tr key={r.strike} data-desk-strike={r.strike} data-atm={r.strike === chain.summary.atm_strike ? "" : undefined}>
            <td className={cell(mine(r.strike, "CE"))}>{r.call.ltp === null ? EMPTY : formatPrice(r.call.ltp)}</td>
            <td className="px-space-1 py-space-1 text-right font-tabular text-fg-muted">{formatCount(r.call.oi)}</td>
            <td className={cx("px-space-1 py-space-1 text-center font-tabular font-semibold", r.strike === chain.summary.atm_strike ? "text-brand" : "text-fg")}>
              {formatNumber(r.strike, 0)}
            </td>
            <td className={cell(mine(r.strike, "PE"))}>{r.put.ltp === null ? EMPTY : formatPrice(r.put.ltp)}</td>
            <td className="px-space-1 py-space-1 text-right font-tabular text-fg-muted">{formatCount(r.put.oi)}</td>
          </tr>
        ))}
      </tbody>
      <caption className="caption-bottom pt-space-1 text-left text-t12 text-fg-muted">Highlighted: the contracts you hold. ATM strike in colour.</caption>
    </table>
  );
}
