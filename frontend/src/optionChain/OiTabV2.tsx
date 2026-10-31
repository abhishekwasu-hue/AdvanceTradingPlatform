import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { useMarketStatus } from "../chart/useMarketStatus";
import { cx } from "../components/primitives/cx";
import type { OiBannerResponse, OiHistoryResponse, OiStrikeSeries, OiStrikesResponse } from "../oi/types";
import type { GlossaryKey } from "../ui/glossary";
import { HelpPopover } from "../ui/HelpPopover";
import { TermHint } from "../ui/TermHint";
import { EMPTY, formatCount, formatNumber, formatTimeIST } from "../ui/format";
import {
  HISTORY_COLUMNS, OI_POLL_MS, PHONE_PICKABLE, SIDES, changeLine, dataAsOf, dteText, highestOi, historyCell, historyRows,
  lastChangeAt, nextUpdateText, oiRead, shouldPoll, shownRows, sourceTimes, strikeText, strikeTick, type HistoryColumn,
  type HistoryRowView, type SourceTime,
} from "./oiTab";

/**
 * M61 (owner, 30 Oct): the v2 option chain's OI tab - a summary card (numbers first), the OI history (only the
 * readings that changed) and OI by strike, under ONE "Data as of HH:MM". Information only: no order path, no verdict
 * words. The rules are in ./oiTab.ts (pure, tested); this file reads the three OI endpoints and lays them out.
 */
export const INTERVALS = [5, 10, 15] as const;
type Interval = (typeof INTERVALS)[number];

export interface OiTabState {
  banner: OiBannerResponse | null;
  history: OiHistoryResponse | null;
  strikes: OiStrikesResponse | null;
  error: string | null;
  loading: boolean;
}

/** The three reads together; again every minute only while the market is open (OI-4: never while it is closed). */
export function useOiTab(underlying: string, interval: Interval, statusOpen: boolean | null): OiTabState {
  const [state, setState] = useState<OiTabState>({ banner: null, history: null, strikes: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  const shown = useRef(underlying);
  useEffect(() => {
    let live = true;
    if (shown.current !== underlying) {           // another symbol: never show the old one's numbers
      shown.current = underlying;
      setState({ banner: null, history: null, strikes: null, error: null, loading: true });
    }
    Promise.allSettled([api.oiBanner(underlying), api.oiHistory(underlying, interval), api.oiStrikes(underlying)]).then(([b, h, s]) => {
      if (!live) return;
      setState((prev) => ({
        banner: b.status === "fulfilled" ? b.value : prev.banner,
        history: h.status === "fulfilled" ? h.value : prev.history,
        strikes: s.status === "fulfilled" ? s.value : prev.strikes,
        error: [b, h, s].every((r) => r.status === "rejected") ? "The OI readings are not available right now." : null,
        loading: false,
      }));
    });
    return () => { live = false; };
  }, [underlying, interval, tick]);
  const poll = shouldPoll(state.banner?.market_open, statusOpen);
  useEffect(() => {
    if (!poll) return undefined;
    const id = window.setInterval(() => setTick((t) => t + 1), OI_POLL_MS);
    return () => window.clearInterval(id);
  }, [poll]);
  return state;
}

const CARD = "min-w-0 rounded-panel border border-subtle bg-surface-1 p-space-3 md:p-space-4";

/** The tab, with its reads. */
export function OiTab({ underlying }: { underlying: string }) {
  const market = useMarketStatus();
  const nse = market.status?.sessions?.NSE;
  const [interval, setIntervalMinutes] = useState<Interval>(5);
  const state = useOiTab(underlying, interval, nse ? nse.is_open : null);
  return <OiTabView underlying={underlying} state={state} interval={interval} onInterval={setIntervalMinutes}
    statusOpen={nse ? nse.is_open : null} nextOpen={nse?.next_open ?? null} />;
}

/** The tab for data already in hand (pure: no reads, no timers) - tests and screenshots render it directly. */
export function OiTabView({ underlying, state, interval, onInterval, statusOpen, nextOpen, now = Date.now() }: {
  underlying: string; state: OiTabState; interval: Interval; onInterval: (i: Interval) => void;
  statusOpen: boolean | null; nextOpen: string | null; now?: number;
}) {
  const { banner, history, strikes } = state;
  const closed = statusOpen === false || banner?.market_open === false;
  const views = useMemo(() => historyRows(history?.rows ?? []), [history]);
  const sources = sourceTimes(banner, history, strikes);
  const asOf = dataAsOf(sources);
  if (!banner && !history && !strikes) {
    return <p className="text-t14 text-fg-muted" data-oi-state="empty">{state.error ?? (state.loading ? "Reading the OI…" : "No OI readings yet.")}</p>;
  }
  return (
    <div className="space-y-space-3" data-oi-tab={underlying}>
      <div className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1 text-t13 text-fg-muted" data-oi-asof>
        <span className="inline-flex items-center gap-space-1 font-medium text-fg">
          {asOf ?? "No reading yet today"}
          {asOf && <DataTimes sources={sources} />}
        </span>
        {closed && <span className="rounded-pill border border-subtle px-space-2 py-0.5" data-oi-closed>Market closed</span>}
        {closed && <span data-oi-next>{nextUpdateText(nextOpen, now)}</span>}
        {!closed && banner?.stale && banner.age_minutes !== null && (
          <span className="rounded-pill border border-warn px-space-2 py-0.5 text-warn">Last update {Math.round(banner.age_minutes)} min ago</span>
        )}
      </div>
      <OiSummaryCard banner={banner} strikes={strikes?.strikes ?? null} views={views} />
      <div className="grid gap-space-3 lg:grid-cols-2">
        <section aria-label={`${underlying} OI history`} className={CARD} data-card>
          <h3 className="mb-space-2 text-t15 font-semibold text-fg">OI history</h3>
          <OiHistory views={views} date={history?.date ?? null} interval={interval} onInterval={onInterval} closed={closed} />
        </section>
        <section aria-label={`${underlying} OI by strike`} className={CARD} data-card>
          <h3 className="mb-space-2 text-t15 font-semibold text-fg">OI by strike</h3>
          <OiByStrike series={strikes?.strikes ?? []} atm={banner?.banner?.atm_strike ?? null} />
        </section>
      </div>
    </div>
  );
}

/** OI-4: each source's own data time, behind (?). */
function DataTimes({ sources }: { sources: readonly SourceTime[] }) {
  return (
    <HelpPopover title="Data times">
      <p>The tab shows the oldest of these times, so no figure on it is newer than it says.</p>
      <ul className="space-y-space-1">
        {sources.map((s) => (
          <li key={s.label} className="flex justify-between gap-space-3"><span>{s.label}</span>
            <span className="font-tabular text-fg">{s.at ? formatTimeIST(s.at, { suffix: false }) : EMPTY}</span></li>
        ))}
      </ul>
    </HelpPopover>
  );
}

/** OI-5: numbers first - PCR, max pain, DTE, the highest call and put OI strikes; then one neutral read. */
export function OiSummaryCard({ banner, strikes, views }: { banner: OiBannerResponse | null; strikes: readonly OiStrikeSeries[] | null;
  views: readonly HistoryRowView[] }) {
  const b = banner?.banner ?? null;
  const highCall = highestOi(strikes, "call");
  const highPut = highestOi(strikes, "put");
  const read = oiRead(b);
  const change = changeLine(views);
  const items: { key: string; label: string; term?: GlossaryKey; value: string; sub?: string; tone?: string }[] = [
    { key: "pcr", label: "PCR", term: "pcr", value: b?.pcr === null || b?.pcr === undefined ? EMPTY : formatNumber(b.pcr, 2) },
    { key: "max_pain", label: "Max pain", term: "max-pain", value: strikeText(b?.max_pain) },
    { key: "dte", label: "DTE", term: "dte", value: dteText(b?.dte) },
    { key: "high_call", label: "Highest call OI", term: "oi", value: highCall ? strikeText(highCall.strike) : EMPTY, sub: highCall ? formatCount(highCall.oi) : undefined,
      tone: SIDES[0].text },
    { key: "high_put", label: "Highest put OI", value: highPut ? strikeText(highPut.strike) : EMPTY, sub: highPut ? formatCount(highPut.oi) : undefined,
      tone: SIDES[1].text },
  ];
  return (
    <section aria-label="OI summary" className={CARD} data-oi-summary>
      <dl className="grid grid-cols-2 gap-x-space-4 gap-y-space-2 sm:grid-cols-3 lg:grid-cols-5">
        {items.map((it) => (
          <div key={it.key} className="min-w-0" data-oi-number={it.key}>
            <dt className="flex items-center gap-space-1 text-t12 text-fg-muted">{it.label}{it.term && <TermHint term={it.term} floating />}</dt>
            <dd className="font-tabular text-t18 font-semibold text-fg">
              {it.value}
              {it.sub && <span className={cx("ml-space-1 text-t13 font-medium", it.tone)}>{it.sub}</span>}
            </dd>
          </div>
        ))}
      </dl>
      {(read || change) && (
        <p className="mt-space-2 text-t13 text-fg-muted" data-oi-read>
          {[read, change].filter(Boolean).join(" · ")}
        </p>
      )}
    </section>
  );
}

/** OI-2: the history - only the readings that changed (or all), full words, a column picker on a phone. */
export function OiHistory({ views, date, interval, onInterval, closed }: {
  views: readonly HistoryRowView[]; date: string | null; interval: Interval; onInterval: (i: Interval) => void; closed: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  const [phoneCols, setPhoneCols] = useState<ReadonlySet<HistoryColumn>>(() => new Set<HistoryColumn>(["pcr"]));
  const rows = shownRows(views, showAll);
  const last = lastChangeAt(views);
  const colClass = (key: HistoryColumn) => (PHONE_PICKABLE.includes(key) && !phoneCols.has(key) ? "hidden md:table-cell" : "");
  return (
    <div className="space-y-space-2" data-oi-history>
      <div className="flex flex-wrap items-center gap-space-2 text-t12">
        <span className="text-fg-muted">View</span>
        {INTERVALS.map((m) => (
          <button key={m} type="button" onClick={() => onInterval(m)} disabled={closed} aria-pressed={interval === m}
            title={closed ? "The market is closed: the readings do not change" : undefined}
            className={cx("min-h-8 rounded-control border px-space-2", interval === m ? "border-brand text-fg" : "border-subtle text-fg-muted",
              closed && "cursor-not-allowed opacity-60")}>{m} min</button>
        ))}
        <button type="button" aria-pressed={showAll} onClick={() => setShowAll((s) => !s)} data-oi-show-all
          className={cx("min-h-8 rounded-control border px-space-2", showAll ? "border-brand text-fg" : "border-subtle text-fg-muted")}>Show all</button>
        <details className="relative md:hidden" data-oi-columns>
          <summary className="inline-flex min-h-8 cursor-pointer list-none items-center rounded-control border border-subtle px-space-2 text-fg-muted [&::-webkit-details-marker]:hidden">Columns</summary>
          <div className="absolute left-0 z-20 mt-space-1 space-y-space-1 rounded-panel border border-subtle bg-surface-1 p-space-2 shadow-raised">
            {HISTORY_COLUMNS.filter((c) => PHONE_PICKABLE.includes(c.key)).map((c) => (
              <label key={c.key} className="flex min-h-8 items-center gap-space-2 whitespace-nowrap text-t13 text-fg">
                <input type="checkbox" checked={phoneCols.has(c.key)} onChange={() => setPhoneCols((prev) => {
                  const next = new Set(prev);
                  if (next.has(c.key)) next.delete(c.key); else next.add(c.key);
                  return next;
                })} />{c.label}
              </label>
            ))}
          </div>
        </details>
      </div>
      {closed && last && <p className="text-t13 text-fg" data-oi-last-change>Last change at {last}</p>}
      {views.length === 0 && <p className="text-t13 text-fg-muted">No readings{date ? ` for ${date}` : ""} yet.</p>}
      {views.length > 0 && (
        <>
          <p className="text-t12 text-fg-muted">
            {showAll ? `All ${views.length} readings` : `${rows.length} of ${views.length} readings: only those where call OI or put OI changed`}
          </p>
          <div className="max-h-[28rem] overflow-auto" data-oi-history-scroll>
            <table className="w-full border-collapse text-t13">
              <thead>
                <tr className="border-b border-subtle text-left">
                  {HISTORY_COLUMNS.map((c) => (
                    <th key={c.key} scope="col" className={cx("sticky top-0 whitespace-nowrap bg-surface-1 px-space-1 py-space-1 font-normal text-fg-muted",
                      c.key !== "time" && c.key !== "read" && "text-right", colClass(c.key))}>{c.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((v) => (
                  <tr key={v.row.slot} className={cx("border-b border-subtle last:border-0", !v.session && "text-fg-muted")} data-oi-row={v.session ? "session" : "outside"}>
                    {HISTORY_COLUMNS.map((c) => (
                      <td key={c.key} className={cx("px-space-1 py-space-1", c.key === "read" ? "min-w-48 text-left" : "whitespace-nowrap",
                        c.key !== "time" && c.key !== "read" && "text-right font-tabular", c.key === "call" && SIDES[0].text, c.key === "put" && SIDES[1].text,
                        colClass(c.key))}>{historyCell(v, c.key)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

/** OI-3: call and put OI per strike (latest reading) - labels rotated so they never overlap, ATM marked, the figures on
 * hover / tap; calls first in red, puts second in green, as everywhere on the page. */
export function OiByStrike({ series, atm }: { series: readonly OiStrikeSeries[]; atm: number | null }) {
  const bars = useMemo(() => [...series].sort((a, b) => a.strike - b.strike).map((s) => {
    const last = s.points[s.points.length - 1];
    return { strike: s.strike, call: last?.call_oi ?? null, put: last?.put_oi ?? null };
  }), [series]);
  const [active, setActive] = useState<number | null>(null);
  if (!bars.length) return <p className="text-t13 text-fg-muted">No per-strike OI yet.</p>;
  const max = Math.max(1, ...bars.flatMap((b) => [b.call ?? 0, b.put ?? 0]));
  const slot = 30, padLeft = 66, padTop = 16, plot = 150, labels = 50;
  const width = padLeft + bars.length * slot + 6;
  const height = padTop + plot + labels;
  const y = (v: number) => padTop + plot - (v / max) * plot;
  const every = slot >= 22 ? 1 : 2;
  const shownStrike = active ?? atm ?? bars[Math.floor(bars.length / 2)].strike;
  const pick = bars.find((b) => b.strike === shownStrike) ?? null;
  const note = (b: { strike: number; call: number | null; put: number | null }) =>
    `${strikeText(b.strike)}: Call OI ${formatCount(b.call)} / Put OI ${formatCount(b.put)}`;
  return (
    <figure className="space-y-space-1" data-oi-strikes>
      <div className="overflow-x-auto">
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="OI by strike: call OI and put OI at each strike"
          onMouseLeave={() => setActive(null)}>
          {[max, max / 2, 0].map((t) => (
            <g key={t}>
              <line x1={padLeft} x2={width - 6} y1={y(t)} y2={y(t)} className="stroke-border-subtle" strokeWidth={1} />
              <text x={padLeft - 6} y={y(t) + 4} textAnchor="end" className="fill-fg-muted text-t12">{formatCount(t)}</text>
            </g>
          ))}
          {bars.map((b, i) => {
            const x = padLeft + i * slot;
            const isAtm = atm !== null && b.strike === atm;
            const ch = b.call ?? 0, ph = b.put ?? 0;
            return (
              <g key={b.strike} data-strike={b.strike} data-atm={isAtm || undefined} onMouseEnter={() => setActive(b.strike)} onClick={() => setActive(b.strike)}>
                {isAtm && <rect x={x} y={padTop - 12} width={slot} height={plot + 12} className="fill-brand/10" />}
                {isAtm && <text x={x + slot / 2} y={padTop - 3} textAnchor="middle" className="fill-fg text-t12 font-semibold">ATM</text>}
                <rect x={x + 3} y={y(ch)} width={slot / 2 - 4} height={Math.max(0, padTop + plot - y(ch))} className={cx(SIDES[0].fill, "opacity-80")} />
                <rect x={x + slot / 2 + 1} y={y(ph)} width={slot / 2 - 4} height={Math.max(0, padTop + plot - y(ph))} className={cx(SIDES[1].fill, "opacity-80")} />
                <rect x={x} y={padTop} width={slot} height={plot} className={cx("fill-transparent", active === b.strike && "stroke-fg-muted")} strokeWidth={1}>
                  <title>{note(b)}</title>
                </rect>
                {i % every === 0 && (
                  <text x={x + slot / 2} y={padTop + plot + 10} textAnchor="end" transform={`rotate(-45 ${x + slot / 2} ${padTop + plot + 10})`}
                    className={cx("text-t12", isAtm ? "fill-fg font-semibold" : "fill-fg-muted")}>{strikeTick(b.strike)}</text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
      <figcaption className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1 text-t12 text-fg-muted">
        {SIDES.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-space-1"><span aria-hidden className={cx("inline-block h-2 w-3 rounded-sm", s.swatch)} />{s.label}</span>
        ))}
        {atm !== null && <span className="inline-flex items-center gap-space-1">Shaded: ATM <TermHint term="atm" floating /> {strikeText(atm)}</span>}
      </figcaption>
      {pick && <p className="font-tabular text-t13 text-fg" data-oi-strike-note aria-live="polite">{note(pick)}</p>}
    </figure>
  );
}
