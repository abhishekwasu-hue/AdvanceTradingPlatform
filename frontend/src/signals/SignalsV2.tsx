import { ChevronRight, Search, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { cx } from "../components/primitives";
import PageDishV2 from "../growth/PageDishV2";
import { formatTimeIST } from "../ui/format";
import { useFeatures } from "../ui/useFeatures";
import { PageIntro } from "../ui/PageIntro";
import {
  SOURCES, TABS, canDecide, canTradeRole, countLabel, defaultTab, itemForProposal, missingProposalMessage, sourceCaption,
  type FeedSource, type FeedTab, type SignalFeedItem,
} from "./feed";
import DayOneV2 from "./DayOneV2";
import { Direction, SignalDetail, StatusChip, signalDay, signalTime } from "./SignalPartsV2";
import { setupWords } from "./signalText";
import { useSignalFeed } from "./useSignalFeed";
import { PageTabs } from "../ui/PageTabs";

/** Desktop gets the list and the detail side by side from this width (UI v2 §8: "detail panels 430 px"). The seven
 * columns need about 660 px beside the 430 px panel, which a 1440 px window has and 1280-1439 does not; below it the
 * page uses the stacked cards (no table is ever cut off or scrolled sideways - Phase A rule). */
export const WIDE_QUERY = "(min-width: 1440px)";

function matches(query: string): boolean {
  try { return typeof window.matchMedia === "function" && window.matchMedia(query).matches; } catch { return false; }
}

export function useMediaQuery(query: string): boolean {
  const [on, setOn] = useState(() => matches(query));
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    const onChange = () => setOn(mq.matches);
    onChange();
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, [query]);
  return on;
}

const EMPTY: Record<FeedTab, { title: string; body: string }> = {
  waiting: { title: "Nothing is waiting for you.", body: "New signals also arrive as a notification." },
  today: { title: "No signals yet today.", body: "Signals from your paper bots, scans and Copilot show here as they come." },
  history: { title: "No signals in the last 30 days.", body: "Signals you approve, skip or let expire are kept here for 30 days." },
};

/** With his day-one watch adopted, an empty list points at it. */
const WATCHING_BODY = "Your watch above shows where each symbol on your list stands. Setups from your paper bots that wait for your approval land here.";

/**
 * UI2-3 (UI v2 proposal §7, §3 M6 / M7; MOBILE_APP_PLAN §2.1): Signals - "What should I act on?". An inbox of the trades
 * your Autopilot, scans and Copilot suggest, from the read-only `GET /api/signals/feed`:
 * - tabs Waiting for you (count) · Today (count) · History (30 days, paged); a source filter and a symbol filter;
 * - desktop (1440 px and up): the list with Time, Symbol + Long/Short, Setup + source, Status (UX3 fix 10: no single
 *   prices in the signal text; the user's own levels are in the detail's "Your levels" fold), and
 *   the detail of the selected signal on the right (default: Today, the first waiting signal selected);
 * - phone: cards (default: Waiting for you); a card opens in place with Approve / Skip / chart / More at 44 px.
 * Approve, Skip and Snooze are the existing proposal endpoints with their step-up and owner rules; the AI never places
 * an order (ADR-0006), and nothing here is in the way of an exit (ADR-0004). Shown only while `ui_v2` is on; the
 * signal generator that lived here is in the Library's strategy drawer now (M6).
 */
export default function SignalsV2() {
  const wide = useMediaQuery(WIDE_QUERY);
  const [params] = useSearchParams();
  const proposal = params.get("proposal");
  const { user } = useAuth();
  const canTrade = canTradeRole(user?.role);
  // A `?proposal=<id>` link (Watchtower, push, Telegram) names a waiting proposal: it opens on "Waiting for you".
  const [tab, setTab] = useState<FeedTab>(() => (proposal ? "waiting" : defaultTab(matches(WIDE_QUERY))));
  const [source, setSource] = useState<FeedSource>("all");
  const [symbol, setSymbol] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const [message, setMessage] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const feed = useSignalFeed(tab, source, symbol);
  // MO4 slice 4: the page's ready-made dish (ui_v2 + growth_v2).
  const dishOn = useFeatures().on("growth_v2");
  // Signals day one (ui_v2 + strategy_templates): the "pick a ready strategy and a list" card, then his watch.
  const dayOneOn = useFeatures().on("strategy_templates");
  const [watching, setWatching] = useState(false);
  const { items } = feed;

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 30_000);   // "Waiting · 8 min" stays current
    return () => window.clearInterval(id);
  }, []);

  // The selected (desktop) / opened (phone) signal: a `?proposal=<id>` link's, else the first waiting one, else the first.
  const [picked, setPicked] = useState<string | null>(null);
  const linked = itemForProposal(items, proposal);
  // The link's proposal is not waiting in this list: say so (and whether it is another member's), never fall back silently.
  const linkedAction = proposal ? feed.proposals.get(Number(proposal)) : undefined;
  const othersProposal = !!linkedAction && linkedAction.owner_user_id != null && linkedAction.owner_user_id !== user?.id;
  const missing = proposal && tab === "waiting" && !feed.loading && !linked && /^\d+$/.test(proposal)
    ? missingProposalMessage(proposal, othersProposal) : null;
  const fallback = useMemo(() => items.find((i) => canDecide(i, now)) ?? (wide ? items[0] : undefined), [items, now, wide]);
  const selected = items.find((i) => i.id === picked) ?? linked ?? fallback ?? null;

  const done = (text: string) => { setMessage(text); setProblem(null); feed.reload(); };
  const failed = (text: string, stale?: boolean) => { setProblem(text); setMessage(null); if (stale) feed.reload(); };
  const filtered = source !== "all" || !!symbol;
  const count = (t: FeedTab) => countLabel(feed.counts, t);

  return (
    <div className="space-y-space-4" data-page="signals-v2">
      <PageIntro id="signals" lines={["Open a signal to see its levels and why it was suggested, then approve or skip it."]} />
      <header>
        <h1 className="sr-only md:not-sr-only md:text-t24 md:font-semibold md:text-fg">Signals</h1>
        <p className="hidden text-t14 text-fg-muted md:mt-space-1 md:block">Trades your paper bots, scans and Copilot suggest. Nothing is placed until you approve it.</p>
      </header>

      {dishOn && <PageDishV2 page="signals" />}
      {dayOneOn && <DayOneV2 onAdopted={setWatching} />}

      <PageTabs label="Signals" value={tab} data={{ tour: "signals-tabs" }}
        onChange={(t) => { setTab(t); setPicked(null); setMessage(null); }}
        items={TABS.map((t) => {
          const n = count(t.id);
          return {
            key: t.id,
            short: `${t.short}${n !== undefined ? ` · ${n}` : ""}`,
            label: <>{t.label}{n !== undefined && <sup className="ml-space-1 text-t12 font-normal">{n}</sup>}</>,
          };
        })} />

      <p className="text-t14 text-fg-muted md:hidden">Nothing is placed until you approve it.</p>

      <Filters source={source} symbol={symbol} open={filtersOpen || filtered} onToggle={() => setFiltersOpen((o) => !o)}
        onSource={(s) => { setSource(s); setPicked(null); }} onSymbol={(s) => { setSymbol(s); setPicked(null); }}
        updated={feed.asOf ? formatTimeIST(feed.asOf, { suffix: false }) : null} />

      {missing && (
        <p className="rounded-panel border border-subtle bg-surface-1 px-space-4 py-space-3 text-t14 text-fg" data-missing-proposal>
          {missing}{" "}
          {othersProposal && (
            <Link to={`/copilot/watchtower?action=${proposal}`} className="font-semibold text-brand hover:underline">Open the Watchtower</Link>
          )}
        </p>
      )}
      {message && <p role="status" className="rounded-panel border border-subtle bg-surface-1 px-space-4 py-space-3 text-t14 text-fg">{message}</p>}
      {(problem || feed.error) && (
        <p role="alert" className="rounded-panel border border-warn/40 bg-warn-tint px-space-4 py-space-3 text-t14 text-fg">
          {problem ?? `${feed.error} Try again in a moment.`}
        </p>
      )}

      {feed.loading ? (
        <div className="h-40 animate-pulse rounded-panel bg-surface-2" aria-busy="true" aria-label="Loading signals" data-tour="signals-list" />
      ) : items.length === 0 ? (
        <div className="rounded-panel border border-subtle bg-surface-1 px-space-6 py-space-10 text-center shadow-card" data-empty data-tour="signals-list">
          <p className="text-t15 font-semibold text-fg">{filtered ? "No signals match these filters." : EMPTY[tab].title}</p>
          <p className="mt-space-1 text-t14 text-fg-muted">{filtered ? "Show every source and symbol to see the rest." : watching ? WATCHING_BODY : EMPTY[tab].body}</p>
          {filtered && (
            <button type="button" onClick={() => { setSource("all"); setSymbol(null); }}
              className="mt-space-4 inline-flex min-h-11 items-center rounded-control px-space-3 text-t14 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
              Show all signals
            </button>
          )}
        </div>
      ) : wide ? (
        <div className="grid grid-cols-[minmax(0,1fr)_400px] items-start gap-space-4" data-tour="signals-list">
          <SignalTable items={items} tab={tab} now={now} selectedId={selected?.id ?? null} onSelect={(id) => { setPicked(id); setMessage(null); }} />
          <div className="sticky top-space-6">
            {selected && <SignalDetail key={selected.id} item={selected} proposal={selected.action_id !== null ? feed.proposals.get(selected.action_id) : undefined}
              now={now} canTrade={canTrade} onDone={done} onError={failed} />}
          </div>
        </div>
      ) : (
        <ul className="space-y-space-3" aria-label="Signals list" data-tour="signals-list">
          {items.map((item) => (
            <SignalCard key={item.id} item={item} now={now} open={selected?.id === item.id} canTrade={canTrade}
              proposalAction={item.action_id !== null ? feed.proposals.get(item.action_id) : undefined}
              onOpen={() => { setPicked(item.id); setMessage(null); }} onDone={done} onError={failed} />
          ))}
        </ul>
      )}

      {!feed.loading && feed.more && (
        <button type="button" onClick={feed.loadMore} disabled={feed.loadingMore}
          className="flex min-h-11 w-full items-center justify-center rounded-control border border-border bg-surface-1 text-t14 font-semibold text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50">
          {feed.loadingMore ? "Loading…" : "Show more"}
        </button>
      )}
      {!feed.loading && items.length > 0 && tab === "waiting" && !feed.more && (
        <p className="text-center text-t14 text-fg-muted">That&apos;s all for now. New signals also arrive as a notification.</p>
      )}
    </div>
  );
}

function Filters({ source, symbol, open, onToggle, onSource, onSymbol, updated }: {
  source: FeedSource; symbol: string | null; open: boolean; onToggle: () => void;
  onSource: (s: FeedSource) => void; onSymbol: (s: string | null) => void; updated: string | null;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(symbol ?? "");
  const fieldId = useId();
  const panelId = useId();
  useEffect(() => { setDraft(symbol ?? ""); }, [symbol]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next = draft.trim().toUpperCase();
    onSymbol(next || null);
    setEditing(false);
  };
  return (
    <div className="space-y-space-3" data-tour="signals-filters">
      <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={panelId}
        className="inline-flex min-h-11 items-center gap-space-2 rounded-control border border-border bg-surface-1 px-space-3 text-t14 font-medium text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:hidden">
        <SlidersHorizontal size={16} aria-hidden />Filter
      </button>
      <div id={panelId} className={cx("flex-wrap items-center gap-space-3 md:flex", open ? "flex" : "hidden")}>
        <div role="radiogroup" aria-label="Source" className="grid w-full grid-cols-2 gap-space-1 rounded-panel bg-surface-2 p-space-1 sm:w-auto sm:grid-cols-4">
          {SOURCES.map((s) => (
            <button key={s.id} type="button" role="radio" aria-checked={source === s.id} onClick={() => onSource(s.id)}
              className={cx("min-h-11 whitespace-nowrap rounded-control px-space-3 text-t14 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9",
                source === s.id ? "bg-surface-1 font-semibold text-fg shadow-card" : "text-fg-muted hover:text-fg")}>
              {s.label}
            </button>
          ))}
        </div>
        {editing ? (
          <form onSubmit={submit} className="flex items-center gap-space-2">
            <label htmlFor={fieldId} className="sr-only">Symbol</label>
            <input id={fieldId} value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus autoComplete="off" autoCapitalize="characters" spellCheck={false}
              placeholder="Any symbol"
              className="h-11 w-48 rounded-control border border-border bg-surface-2 px-space-3 text-sm text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9 md:text-t14" />
            <button type="submit" className="inline-flex min-h-11 items-center rounded-control border border-border bg-surface-1 px-space-3 text-t14 font-semibold text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">Show</button>
          </form>
        ) : symbol ? (
          <span className="inline-flex items-center gap-space-1 rounded-control border border-brand bg-brand/10 pl-space-3 text-t14 font-semibold text-fg">
            {symbol}
            <button type="button" aria-label="Clear symbol" onClick={() => onSymbol(null)}
              className="flex h-11 w-11 items-center justify-center rounded-control text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9 md:w-9">
              <X size={16} aria-hidden />
            </button>
          </span>
        ) : (
          <button type="button" onClick={() => setEditing(true)}
            className="inline-flex min-h-11 items-center gap-space-2 rounded-control border border-border bg-surface-1 px-space-3 text-t14 font-semibold text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
            <Search size={16} aria-hidden />Symbol
          </button>
        )}
        {updated && <p className="ml-auto hidden text-t12 text-fg-muted md:block">Updated {updated} · new signals arrive live</p>}
      </div>
    </div>
  );
}

function SignalTable({ items, tab, now, selectedId, onSelect }: {
  items: SignalFeedItem[]; tab: FeedTab; now: Date; selectedId: string | null; onSelect: (id: string) => void;
}) {
  // Every column but Setup sizes to its content (`w-px` + no wrapping); Setup takes the rest and wraps. When space runs
  // out it may break inside a word, hyphenated where possible (`overflow-wrap: anywhere` + `hyphens: auto`), instead of a
  // long word pushing Status past the edge. UX3 fix 10: no price columns (the levels are in the detail's fold). History
  // shows its date above the time, so the Time column stays as narrow as on Today.
  const th = "w-px whitespace-nowrap px-space-2 py-space-2 font-medium";
  return (
    <div className="overflow-hidden rounded-panel border border-subtle bg-surface-1 shadow-card">
      <table className="w-full table-auto text-t14" aria-label="Signals list">
        <thead>
          <tr className="border-b border-subtle text-left text-t12 text-fg-muted">
            <th className={th}>Time</th><th className={th}>Symbol</th><th className="px-space-2 py-space-2 font-medium">Setup</th>
            <th className={th}>Status</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const on = item.id === selectedId;
            return (
              <tr key={item.id} onClick={() => onSelect(item.id)} data-row={item.id}
                className={cx("cursor-pointer border-b border-subtle last:border-b-0 transition-colors duration-fast", on ? "bg-surface-2" : "hover:bg-surface-2")}>
                <td className={cx("whitespace-nowrap border-l-2 px-space-2 py-space-3 font-tabular text-fg-muted", on ? "border-l-brand" : "border-l-transparent")}>{tab === "history" ? (
                  <><span className="block">{signalDay(item.at)}</span><span className="block">{signalTime(item.at, "today")}</span></>   // two lines: the column stays narrow
                ) : signalTime(item.at, tab)}</td>
                <td className="px-space-2 py-space-3">
                  <div className="whitespace-nowrap">
                    <button type="button" onClick={(e) => { e.stopPropagation(); onSelect(item.id); }} aria-current={on || undefined} title={item.symbol}
                      className="block max-w-[10rem] truncate rounded-control text-left font-semibold text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                      {item.symbol}
                    </button>
                    <Direction value={item.direction} className="block text-t12" />
                  </div>
                </td>
                <td className="px-space-2 py-space-3">
                  <span className="block hyphens-auto text-fg [overflow-wrap:anywhere]">{setupWords(item.setup)}</span>
                  <span className="block hyphens-auto text-t12 text-fg-muted [overflow-wrap:anywhere]">{sourceCaption(item)}</span>
                </td>
                <td className="whitespace-nowrap px-space-2 py-space-3"><StatusChip item={item} now={now} short /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SignalCard({ item, now, open, canTrade, proposalAction, onOpen, onDone, onError }: {
  item: SignalFeedItem; now: Date; open: boolean; canTrade: boolean; proposalAction?: Parameters<typeof SignalDetail>[0]["proposal"];
  onOpen: () => void; onDone: (m: string) => void; onError: (m: string, stale?: boolean) => void;
}) {
  const decidable = canDecide(item, now);
  return (
    <li>
      <article aria-label={`${item.symbol} signal`} data-signal-card={item.id}
        className={cx("space-y-space-3 rounded-panel border bg-surface-1 p-space-4 shadow-card", open ? "border-border" : "border-subtle")}>
        <div className="flex items-start justify-between gap-space-2">
          <h2 className="flex min-w-0 flex-wrap items-baseline gap-x-space-2 text-t18 font-semibold text-fg">
            <span className="truncate">{item.symbol}</span><Direction value={item.direction} className="text-t15" />
          </h2>
          <StatusChip item={item} now={now} />
        </div>
        <p className="text-t14 text-fg-muted">{setupWords(item.setup)} · {sourceCaption(item)}{item.mode === "LIVE" ? " · Live" : ""}</p>
        {open ? (
          <SignalDetail item={item} proposal={proposalAction} now={now} compact canTrade={canTrade} onDone={onDone} onError={onError} />
        ) : (
          <button type="button" onClick={onOpen}
            className="inline-flex min-h-11 items-center gap-space-1 rounded-control text-t15 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
            {decidable ? "Review" : "Details"}<ChevronRight size={16} aria-hidden />
          </button>
        )}
      </article>
    </li>
  );
}
