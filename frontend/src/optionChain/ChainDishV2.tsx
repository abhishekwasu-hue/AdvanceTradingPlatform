import { useEffect, useRef, useState } from "react";
import { cx } from "../components/primitives/cx";
import { fetchAnalysesShared, type AnalysesResponse, type ChainDish } from "./analyses";

/**
 * MO4 slice 2 (ATP_SESSION_GROWTH_MASTER.md PART 2 item 4): the Option chain "dish" - "What the chain shows", five plain
 * lines at the top of the page (PCR zone, OI walls, max pain against the spot, IV change since the session's first
 * snapshot, the expected move), written by the server from the same six analyses the tabs below show
 * (backend app/option_chain/dish.py). It shares the tabs' read (`fetchAnalysesShared`), refreshes with the chain and
 * shows nothing while there is no answer with a summary (the tabs below say why). Information only - never what to trade.
 * The page mounts it only while `markets_view` is on.
 */
export interface ChainDishProps {
  symbol: string;
  expiry: string | null;
  refreshKey: string | null;
  load?: (symbol: string, expiry?: string | null) => Promise<AnalysesResponse>;
}

export default function ChainDishV2({ symbol, expiry, refreshKey, load = fetchAnalysesShared }: ChainDishProps) {
  const [dish, setDish] = useState<ChainDish | null>(null);
  const [loading, setLoading] = useState(!!symbol.trim());
  const loader = useRef(load);
  loader.current = load;
  const asked = `${symbol.trim().toUpperCase()}|${expiry ?? ""}`;
  const shown = useRef(asked);

  useEffect(() => {
    if (shown.current !== asked) { shown.current = asked; setDish(null); }   // another symbol: never the old one's lines
    if (!symbol.trim()) { setLoading(false); return; }
    let live = true;
    setLoading(true);
    loader.current(symbol, expiry)
      .then((d) => { if (live) setDish(d.state === "ok" && d.dish ? d.dish : null); })
      .catch(() => { if (live) setDish(null); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [asked, refreshKey]);   // eslint-disable-line react-hooks/exhaustive-deps

  return <ChainDishView dish={dish} loading={loading} />;
}

/** The summary for an answer already in hand (pure). */
export function ChainDishView({ dish, loading }: { dish: ChainDish | null; loading: boolean }) {
  if (!dish) {
    if (!loading) return null;
    return (
      <section aria-busy="true" aria-label="What the chain shows" className="space-y-space-2 rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card">
        <span className="sr-only">Loading the summary</span>
        {Array.from({ length: 3 }, (_, i) => <div key={i} className="h-5 animate-pulse rounded-control bg-surface-2" />)}
      </section>
    );
  }
  return (
    <section aria-label={dish.title} data-block="chain-dish"
      className="rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5">
      <h2 className="text-t15 font-semibold text-fg">{dish.title}</h2>
      <ul className="mt-space-2 space-y-space-2">
        {dish.points.map((p) => (
          <li key={p.key} data-point={p.key} data-state={p.state} className="text-t14 md:flex md:gap-space-3">
            <span className="block shrink-0 text-t12 font-semibold uppercase text-fg-muted md:w-32 md:pt-0.5">{p.label}</span>
            <span className={cx("block", p.state === "ok" ? "text-fg" : "text-fg-muted")}>{p.text}</span>
          </li>
        ))}
      </ul>
      <p className="mt-space-3 text-t12 text-fg-muted">{dish.note}</p>
    </section>
  );
}
