import { ArrowLeft, ChartCandlestick, ChevronRight, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useId, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { CARD, LINK_SECONDARY } from "../dashboard/CardV2";
import { cx } from "../components/primitives/cx";
import type { PageProps } from "../routes";
import { chartTabPath } from "../shell/shellRoutes";
import { PageIntro } from "../ui/PageIntro";
import { SubTabs } from "../ui/SubTabs";
import {
  PERIODS, UNKNOWN, advanceShare, asOfText, breadthText, closeText, dishLines, fetchList, fetchOverview, heatClass, isPeriod,
  leadersAndLaggards, moveText, rankGroups, relativeText, toneText, type ListMember, type MarketGroup, type MarketList,
  type MarketsOverview, type Mover, type Period,
} from "./model";

/**
 * MO4 slice 2 (ATP_SESSION_GROWTH_MASTER.md PART 2 item 6): the Sector & index view at /markets (v2 only, flag
 * `markets_view`). It opens ready: the market's move and the strongest / weakest sector in plain words, then every sector
 * (or index) with its move, breadth and strongest / weakest stocks - a heat map on a desktop, a list on a phone. A tile
 * opens that index's or sector's stocks (`?list=<key>`), and each stock or index opens on Charts. The day / week / month
 * choice and the view are in the address (`?view=&period=`). Numbers come from the shared end-of-day closes; a missing
 * one says "I don't know" - never a sample. Information only: nothing here places an order.
 */

export type View = "sectors" | "indices";
const VIEWS: readonly { key: View; label: string }[] = [
  { key: "sectors", label: "Sectors" },
  { key: "indices", label: "Indices" },
];

export type Load<T> = { status: "loading" } | { status: "error" } | { status: "ok"; data: T };

function useLoad<T>(load: () => Promise<T>, key: string): [Load<T>, () => void] {
  const [state, setState] = useState<Load<T>>({ status: "loading" });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    load().then((data) => { if (live) setState({ status: "ok", data }); }, () => { if (live) setState({ status: "error" }); });
    return () => { live = false; };
    // `key` names what `load` reads; `nonce` is "Try again".
  }, [key, nonce]);
  return [state, useCallback(() => setNonce((n) => n + 1), [])];
}

/** UI-H Wave 0 (H2): the shared SubTabs (a toggle group: both rows pick a view of the same list). */
function Segmented<T extends string>({ label, items, value, onChange }: {
  label: string; items: readonly { key: T; label: string }[]; value: T; onChange: (v: T) => void;
}) {
  return <SubTabs kind="toggle" label={label} items={items} value={value} onChange={onChange} size="sm" />;
}

function Move({ value, className = "" }: { value: number | null; className?: string }) {
  return <span className={cx("font-tabular font-semibold", toneText(value), value === null && "font-normal", className)}>{moveText(value)}</span>;
}

/** The share of rising stocks as a thin bar (rose / fell), with the counts in words for screen readers. */
function BreadthBar({ group, period }: { group: MarketGroup; period: Period }) {
  const b = group.breadth?.[period] ?? null;
  const share = advanceShare(b);
  const text = breadthText(b);
  if (share === null || !text) return null;
  return (
    <div className="mt-space-2" data-breadth>
      <div aria-hidden className="flex h-1.5 overflow-hidden rounded-pill bg-down/40">
        <div className="h-full bg-up" style={{ width: `${share}%` }} />
      </div>
      <div className="mt-space-1 truncate text-t12 text-fg-muted">{text}</div>
    </div>
  );
}

function Movers({ title, rows }: { title: string; rows: Mover[] }) {
  if (!rows.length) return null;
  return (
    <div className="min-w-0">
      <div className="text-t12 font-medium text-fg-muted">{title}</div>
      <ul className="mt-space-1 space-y-space-1">
        {rows.map((m) => (
          <li key={m.symbol} className="flex items-baseline justify-between gap-space-2 text-t13">
            <Link to={chartTabPath(m.symbol, m.exchange)} className="inline-flex min-h-11 min-w-0 items-center truncate text-fg hover:text-brand hover:underline md:min-h-0">{m.symbol}</Link>
            <Move value={m.change} className="shrink-0 text-t13" />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** "Strongest BK1 +3.00% · Weakest BK2 −1.00%" for a tile or a row, or null when no stock has a move. */
function extremesText(g: MarketGroup, period: Period): string | null {
  const top = g.strongest[period]?.[0];
  const bottom = g.weakest[period]?.[0];
  const parts: string[] = [];
  if (top) parts.push(`Strongest ${top.symbol} ${moveText(top.change)}`);
  if (bottom) parts.push(`Weakest ${bottom.symbol} ${moveText(bottom.change)}`);
  return parts.length ? parts.join(" · ") : null;
}

function groupLink(g: MarketGroup, view: View, period: Period): string | null {
  if (g.list_key) return `/markets?${new URLSearchParams({ view, period, list: g.list_key }).toString()}`;
  if (g.kind === "index" && g.symbol) return chartTabPath(g.symbol, g.exchange);
  return null;
}

/** One heat-map tile (desktop). */
function Tile({ g, view, period, benchmark }: { g: MarketGroup; view: View; period: Period; benchmark: string }) {
  const to = groupLink(g, view, period);
  const v = g.change[period];
  // The benchmark against itself says nothing.
  const rel = g.name === benchmark ? null : relativeText(g.vs_benchmark[period], benchmark);
  const body = (
    <>
      <div className="flex items-start justify-between gap-space-2">
        <span className="min-w-0 truncate text-t13 font-semibold text-fg" title={g.name}>{g.name}</span>
        {to && <ChevronRight size={14} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />}
      </div>
      <Move value={v} className={cx("mt-space-1 block", v === null ? "text-t14" : "text-t20")} />
      {rel && <div className="truncate text-t12 text-fg-muted">{rel}</div>}
      <BreadthBar group={g} period={period} />
      {extremesText(g, period) && <div data-extremes className="mt-space-1 truncate text-t12 text-fg-muted" title={extremesText(g, period) ?? ""}>{extremesText(g, period)}</div>}
    </>
  );
  const cls = cx("block min-h-[7.5rem] rounded-panel border border-subtle p-space-3 transition-colors duration-fast", heatClass(v, period));
  return to
    ? <Link to={to} data-tile={g.name} className={cx(cls, "hover:border-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand")}>{body}</Link>
    : <div data-tile={g.name} className={cls}>{body}</div>;
}

/** One list row (phone). */
function Row({ g, view, period }: { g: MarketGroup; view: View; period: Period }) {
  const to = groupLink(g, view, period);
  const text = breadthText(g.breadth?.[period] ?? null);
  const extremes = extremesText(g, period);
  const body = (
    <>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-t14 font-medium text-fg">{g.name}</span>
        {text && <span className="block truncate text-t12 text-fg-muted">{text}</span>}
        {extremes && <span className="block truncate text-t12 text-fg-muted">{extremes}</span>}
      </span>
      <span className={cx("shrink-0 rounded-control px-space-2 py-space-1 text-t14", heatClass(g.change[period], period))}>
        <Move value={g.change[period]} />
      </span>
      {to && <ChevronRight size={16} aria-hidden className="shrink-0 text-fg-muted" />}
    </>
  );
  const cls = "flex min-h-12 items-center gap-space-3 px-space-3 py-space-2";
  return (
    <li data-row={g.name} className="border-b border-subtle last:border-b-0">
      {to ? <Link to={to} className={cx(cls, "hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand")}>{body}</Link> : <div className={cls}>{body}</div>}
    </li>
  );
}

function GroupBoard({ groups, view, period, benchmark, label }: { groups: MarketGroup[]; view: View; period: Period; benchmark: string; label: string }) {
  const ranked = rankGroups(groups, period);
  return (
    <>
      <div data-heatmap aria-label={`${label} heat map`} role="list"
        className="hidden gap-space-2 md:grid md:grid-cols-3 lg:grid-cols-4">
        {ranked.map((g) => <div role="listitem" key={`${g.kind}-${g.name}`}><Tile g={g} view={view} period={period} benchmark={benchmark} /></div>)}
      </div>
      <ul data-list aria-label={label} className={cx(CARD, "overflow-hidden md:hidden")}>
        {ranked.map((g) => <Row key={`${g.kind}-${g.name}`} g={g} view={view} period={period} />)}
      </ul>
    </>
  );
}

function LeadersCard({ data, period, view }: { data: MarketsOverview; period: Period; view: View }) {
  const groups = view === "sectors" ? data.sectors : data.indices;
  const { leaders, laggards } = leadersAndLaggards(groups, period);
  if (!leaders.length) return null;
  const item = (g: MarketGroup) => {
    const to = groupLink(g, view, period);
    return (
      <li key={g.name} className="flex items-baseline justify-between gap-space-2 text-t14">
        {to ? <Link to={to} className="inline-flex min-h-11 min-w-0 items-center truncate text-fg hover:text-brand hover:underline md:min-h-0">{g.name}</Link> : <span className="min-w-0 truncate text-fg">{g.name}</span>}
        <Move value={g.change[period]} className="shrink-0" />
      </li>
    );
  };
  return (
    <section aria-label="Leaders and laggards" data-block="leaders" className={cx(CARD, "grid gap-space-4 p-space-4 sm:grid-cols-2 md:p-space-5")}>
      <div className="min-w-0">
        <h2 className="text-t13 font-semibold text-fg">Leading</h2>
        <ul className="mt-space-2 space-y-space-1 sm:max-w-sm">{leaders.map(item)}</ul>
      </div>
      {laggards.length > 0 && (
        <div className="min-w-0">
          <h2 className="text-t13 font-semibold text-fg">Lagging</h2>
          <ul className="mt-space-2 space-y-space-1 sm:max-w-sm">{laggards.map(item)}</ul>
        </div>
      )}
    </section>
  );
}

function Message({ text, onRetry }: { text: string; onRetry?: () => void }) {
  return (
    <div role="status" className={cx(CARD, "flex flex-col items-start gap-space-3 p-space-4 sm:flex-row sm:items-center sm:justify-between md:p-space-5")}>
      <p className="text-t14 text-fg-muted">{text}</p>
      {onRetry && <button type="button" onClick={onRetry} className={LINK_SECONDARY}><RefreshCw size={14} aria-hidden /> Try again</button>}
    </div>
  );
}

function Loading() {
  return (
    <div aria-busy="true" className="space-y-space-3">
      <span className="sr-only">Loading</span>
      <div className="h-20 animate-pulse rounded-panel bg-surface-2" />
      <div className="grid gap-space-2 md:grid-cols-4">
        {Array.from({ length: 8 }, (_, i) => <div key={i} className="h-24 animate-pulse rounded-panel bg-surface-2" />)}
      </div>
    </div>
  );
}

function Footer({ source, note }: { source: string; note: string }) {
  return <p className="text-t12 text-fg-muted">{note} Source: {source}.</p>;
}

function MemberRow({ m, period, benchmark }: { m: ListMember; period: Period; benchmark: string }) {
  const rel = relativeText(m.vs_benchmark[period], benchmark);
  return (
    <li data-member={m.symbol} className="border-b border-subtle last:border-b-0">
      <Link to={chartTabPath(m.symbol, m.exchange)}
        className="grid min-h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-space-3 px-space-3 py-space-2 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand md:grid-cols-[minmax(0,1.4fr)_minmax(0,0.8fr)_minmax(0,0.7fr)_minmax(0,1fr)]">
        <span className="min-w-0">
          <span className="block truncate text-t14 font-semibold text-fg">{m.symbol}</span>
          <span className="block truncate text-t12 text-fg-muted">{m.name}</span>
        </span>
        <span className="hidden text-right font-tabular text-t14 text-fg md:block">{closeText(m.close)}</span>
        <span className="text-right"><Move value={m.change[period]} className="text-t14" /></span>
        <span className="col-span-2 truncate text-t12 text-fg-muted md:col-span-1 md:text-right">{rel ?? ""}</span>
      </Link>
    </li>
  );
}

function ListDetail({ listKey, view, period, onBack }: { listKey: string; view: View; period: Period; onBack: () => void }) {
  const [state, retry] = useLoad<MarketList>(() => fetchList(listKey), listKey);
  return <ListDetailView state={state} view={view} period={period} onBack={onBack} onRetry={retry} />;
}

/** One index's or sector's stocks for a read already in hand (pure). */
export function ListDetailView({ state, view, period, onBack, onRetry }: {
  state: Load<MarketList>; view: View; period: Period; onBack: () => void; onRetry: () => void;
}) {
  const back = (
    <button type="button" onClick={onBack} className="inline-flex h-11 items-center gap-space-1 rounded-control text-t14 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9">
      <ArrowLeft size={16} aria-hidden /> {view === "sectors" ? "All sectors" : "All indices"}
    </button>
  );
  if (state.status === "loading") return <div className="space-y-space-3">{back}<Loading /></div>;
  if (state.status === "error") return <div className="space-y-space-3">{back}<Message text="We could not load these stocks. We will try again in a moment." onRetry={onRetry} /></div>;
  const d = state.data;
  const s = d.summary;
  const bench = d.benchmark.name;
  return (
    <div className="space-y-space-4" data-block="list-detail" data-list-key={d.list_key}>
      {back}
      <section aria-label={d.name} className={cx(CARD, "p-space-4 md:p-space-5")}>
        <div className="flex flex-col gap-space-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h2 className="text-t20 font-semibold text-fg">{d.name}</h2>
            <p className="mt-space-1 text-t13 text-fg-muted">
              {d.kind === "sector" ? `${d.members.length} stocks. ${s?.method ?? ""}` : `${d.members.length} stocks in the index.`}
            </p>
          </div>
          {d.kind === "index" && d.symbol && (
            <Link to={chartTabPath(d.symbol)} className={LINK_SECONDARY}><ChartCandlestick size={16} aria-hidden /> Open chart</Link>
          )}
        </div>
        {s && (
          <dl className="mt-space-4 grid grid-cols-3 gap-space-3">
            {PERIODS.map((p) => (
              <div key={p.key} className={cx("rounded-control p-space-2", p.key === period ? "bg-surface-2" : "")}>
                <dt className="text-t12 text-fg-muted">{p.label}</dt>
                <dd><Move value={s.change[p.key]} className={s.change[p.key] === null ? "text-t13" : "text-t18"} /></dd>
              </div>
            ))}
          </dl>
        )}
        {s && <BreadthBar group={s} period={period} />}
        {s && relativeText(s.vs_benchmark[period], bench) && (
          <p className="mt-space-2 text-t13 text-fg-muted">{relativeText(s.vs_benchmark[period], bench)} {PERIODS.find((p) => p.key === period)?.long}.</p>
        )}
        {s && (s.strongest[period]?.length || s.weakest[period]?.length) ? (
          <div className="mt-space-4 grid gap-space-4 sm:grid-cols-2" data-block="movers">
            <Movers title="Strongest" rows={s.strongest[period] ?? []} />
            <Movers title="Weakest" rows={s.weakest[period] ?? []} />
          </div>
        ) : null}
      </section>
      {d.state !== "ok" && d.message && <Message text={d.message} />}
      <section aria-label="Stocks" className={cx(CARD, "overflow-hidden")}>
        <div aria-hidden className="hidden grid-cols-[minmax(0,1.4fr)_minmax(0,0.8fr)_minmax(0,0.7fr)_minmax(0,1fr)] gap-x-space-3 border-b border-subtle px-space-3 py-space-2 text-t12 font-medium text-fg-muted md:grid">
          <span>Stock</span><span className="text-right">Close</span><span className="text-right">Move</span><span className="text-right">Against {bench}</span>
        </div>
        <ul>{d.members.map((m) => <MemberRow key={m.symbol} m={m} period={period} benchmark={bench} />)}</ul>
      </section>
      <Footer source={d.source} note={d.note} />
    </div>
  );
}

function Board({ data, view, period }: { data: MarketsOverview; view: View; period: Period }) {
  const bench = data.benchmark.name;
  if (view === "sectors") {
    if (!data.sectors.length) return <Message text="The sector lists are not ready yet. They are built once a day after the market data update." />;
    return (
      <div className="space-y-space-4">
        <LeadersCard data={data} period={period} view={view} />
        <GroupBoard groups={data.sectors} view={view} period={period} benchmark={bench} label="Sectors" />
      </div>
    );
  }
  const families = data.families.length ? data.families : [{ key: "", label: "Indices" }];
  return (
    <div className="space-y-space-5">
      {families.map((f) => {
        const groups = data.indices.filter((g) => !f.key || g.family === f.key);
        if (!groups.length) return null;
        return (
          <section key={f.key || "all"} aria-label={f.label} data-family={f.key} className="space-y-space-2">
            <h2 className="text-t15 font-semibold text-fg">{f.label}</h2>
            <GroupBoard groups={groups} view={view} period={period} benchmark={bench} label={f.label} />
          </section>
        );
      })}
    </div>
  );
}

function Dish({ data, period }: { data: MarketsOverview; period: Period }) {
  const lines = dishLines(data, period);
  return (
    <section aria-label="The market at a glance" data-block="markets-dish" className={cx(CARD, "p-space-4 md:p-space-5")}>
      <div className="text-t12 font-medium text-fg-muted">{asOfText(data.as_of, period)}</div>
      <ul className="mt-space-2 space-y-space-1">
        {lines.map((l) => <li key={l} className="text-t15 text-fg">{l}</li>)}
      </ul>
      {data.stale && <p role="status" className="mt-space-3 rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn">The latest closes are not in yet, so these moves are from an earlier session.</p>}
    </section>
  );
}

export default function MarketsPageV2(_props: PageProps) {
  const [params, setParams] = useSearchParams();
  const rawView = params.get("view");
  const view: View = rawView === "indices" ? "indices" : "sectors";
  const rawPeriod = params.get("period");
  const period: Period = isPeriod(rawPeriod) ? rawPeriod : "day";
  const listKey = params.get("list");
  const [state, retry] = useLoad<MarketsOverview>(fetchOverview, "overview");
  const headingId = useId();

  const set = useCallback((patch: Record<string, string | null>) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      for (const [k, v] of Object.entries(patch)) { if (v === null) next.delete(k); else next.set(k, v); }
      return next;
    });
  }, [setParams]);

  return (
    <div className="space-y-space-4" data-page="markets-v2">
      <PageIntro id="markets" lines={["How each sector and index moved. Tap one to see its stocks, then a stock to open its chart."]} />
      <header className="flex flex-col gap-space-3 md:flex-row md:items-end md:justify-between">
        <h1 id={headingId} className="sr-only md:not-sr-only md:text-t24 md:font-semibold md:text-fg">Sectors &amp; indices</h1>
        {!listKey && (
          <div className="flex flex-wrap items-center gap-space-2">
            <Segmented label="Show" items={VIEWS} value={view} onChange={(v) => set({ view: v })} />
            <Segmented label="Period" items={PERIODS} value={period} onChange={(p) => set({ period: p })} />
          </div>
        )}
        {listKey && <Segmented label="Period" items={PERIODS} value={period} onChange={(p) => set({ period: p })} />}
      </header>

      {listKey
        ? <ListDetail listKey={listKey} view={view} period={period} onBack={() => set({ list: null })} />
        : <MarketsBody state={state} view={view} period={period} onRetry={retry} />}
    </div>
  );
}

/** The overview for a read already in hand (pure): the dish, the board and the source line, or a plain state. */
export function MarketsBody({ state, view, period, onRetry }: { state: Load<MarketsOverview>; view: View; period: Period; onRetry: () => void }) {
  if (state.status === "loading") return <Loading />;
  if (state.status === "error") return <Message text="We could not load the market view. We will try again in a moment." onRetry={onRetry} />;
  if (state.data.state !== "ok") return <Message text={state.data.message ?? UNKNOWN} />;
  return (
    <>
      <Dish data={state.data} period={period} />
      <Board data={state.data} view={view} period={period} />
      <Footer source={state.data.source} note={state.data.note} />
    </>
  );
}
