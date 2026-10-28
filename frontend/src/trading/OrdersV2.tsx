import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { CardV2, ChipV2, EmptyStateV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { dataOf, usePolled } from "../dashboard/useDashboardData";
import type { PageProps } from "../routes";
import { useColourV2 } from "../ui/colourV2";
import { DirectionChip } from "../ui/DirectionChip";
import { EMPTY } from "../ui/format";
import { PageFootnote } from "../ui/numbersFirst";
import { orderFillRows, orderTiles, type OrderFillRow } from "./model";
import { BUTTON_SECONDARY, PageTitleV2, TileV2, TilesV2 } from "./PartsV2";

const REFRESH_MS = 60_000;
const every = (ms: number) => () => ms;
/** Fills shown before "Show all". */
export const ORDERS_SHOWN = 30;

type Filter = "all" | "Entry" | "Exit";
const FILTERS: readonly { key: Filter; label: string }[] = [
  { key: "all", label: "All" }, { key: "Entry", label: "Entries" }, { key: "Exit", label: "Exits" },
];

/**
 * UX-10 My trades wave (UI audit rows "My trades - Orders": H5-H fills table wider than a phone, H4-H browser-locale
 * times, H1-M legacy page): My trades › Orders in the v2 shell. H1 order: title → tiles → the fills as a list (one
 * row per fill, two lines, fits 360 px), IST times. Read-only: the same /trades answer today's page reads.
 */
export default function OrdersV2(_props: PageProps = {}) {
  const { user } = useAuth();
  const signedIn = !!user;
  const [trades] = usePolled(api.listTrades, every(REFRESH_MS), signedIn);
  const [filter, setFilter] = useState<Filter>("all");
  const [all, setAll] = useState(false);
  const rows = useMemo(() => orderFillRows(dataOf(trades) ?? []), [trades]);
  const tiles = orderTiles(rows);
  const listed = filter === "all" ? rows : rows.filter((r) => r.kind === filter);
  const shown = all ? listed : listed.slice(0, ORDERS_SHOWN);
  const ok = trades.status === "ok";

  if (!signedIn) return null;

  return (
    <div className="mx-auto w-full space-y-space-4 md:space-y-space-6" data-page="orders-v2">
      <PageTitleV2 title="Orders" subtitle="Every entry and exit of your trades, newest first." />

      <section aria-label="Summary" data-block="summary">
        {trades.status === "loading" ? <LoadingRowsV2 rows={2} /> : (
          <TilesV2>
            <TileV2 testId="fills" label="Fills" value={ok ? String(tiles.fills) : EMPTY} />
            <TileV2 testId="entries" label="Entries" value={ok ? String(tiles.entries) : EMPTY} />
            <TileV2 testId="exits" label="Exits" value={ok ? String(tiles.exits) : EMPTY} />
            <TileV2 testId="still-open" label="Still open" value={ok ? String(tiles.open) : EMPTY} />
          </TilesV2>
        )}
      </section>

      <CardV2 title="Fills" count={listed.length} testId="orders">
        {trades.status === "loading" ? <LoadingRowsV2 /> : trades.status === "error" ? <LoadFailedV2 what="your orders" /> : rows.length === 0 ? (
          <EmptyStateV2 text="No orders yet. A trade's entry and exit show here." action={{ label: "Signals", to: "/signals" }} />
        ) : (
          <>
            <div role="group" aria-label="Show" className="mb-space-3 inline-flex rounded-control border border-border p-px" data-orders-filter>
              {FILTERS.map((f) => (
                <button key={f.key} type="button" aria-pressed={filter === f.key} onClick={() => { setFilter(f.key); setAll(false); }}
                  className={`min-h-11 rounded-control px-space-3 text-t13 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9 ${filter === f.key ? "bg-surface-2 text-fg" : "text-fg-muted hover:text-fg"}`}>
                  {f.label}
                </button>
              ))}
            </div>
            <ul className="divide-y divide-subtle">
              {shown.map((r) => <OrderRowV2 key={r.key} row={r} />)}
            </ul>
            {listed.length > ORDERS_SHOWN && (
              <button type="button" onClick={() => setAll((a) => !a)} className={`${BUTTON_SECONDARY} mt-space-3 w-full`}>
                {all ? "Show fewer" : `Show all ${listed.length}`}
              </button>
            )}
          </>
        )}
      </CardV2>

      {/* UX-11 U5: the page's one footer line, as on Positions (owner default: the My trades pages). */}
      <PageFootnote />
    </div>
  );
}

export function OrderRowV2({ row }: { row: OrderFillRow }) {
  const colour = useColourV2();
  return (
    <li className="py-space-3 first:pt-0 last:pb-0" data-fill={row.key}>
      <div className="flex flex-wrap items-center gap-space-2">
        <span className="text-t15 font-semibold text-fg">{row.symbol}</span>
        <ChipV2 kind={row.kind === "Entry" ? "brand" : "outline"}>{row.kind}</ChipV2>
        {colour ? <DirectionChip side={row.side === "Long" ? "long" : "short"} />
          : <span className={`text-t13 font-medium ${row.side === "Long" ? "text-up" : "text-down"}`}>{row.side}</span>}
        <ChipV2 kind={row.mode === "Live" ? "solid" : "outline"}>{row.mode}</ChipV2>
      </div>
      <p className="mt-px font-tabular text-t13 text-fg-muted">{row.size} · at {row.price} · {row.when}</p>
      {(row.reason || row.strategy) && (
        <p className="mt-px text-t12 text-fg-muted">{[row.reason, row.strategy].filter(Boolean).join(" · ")}</p>
      )}
    </li>
  );
}
