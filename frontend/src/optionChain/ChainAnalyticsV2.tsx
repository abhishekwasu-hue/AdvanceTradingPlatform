import { useEffect, useRef, useState, type ReactNode } from "react";
import { request } from "../api/client";
import { friendlyError } from "../api/errors";
import { cx } from "../components/primitives/cx";
import { Details } from "../ui/Details";
import { SubTabs } from "../ui/SubTabs";
import { EMPTY, formatNumber, formatTimeIST } from "../ui/format";
import { type AnalysesResponse, fetchAnalyses, fetchAnalysesShared, stateMessage } from "./analyses";
import { MiniChart } from "./AnalysisTabsV2";
import {
  type AnalyticsKey, type BuildUpSection, type ExposureSection, type IvSection, type PcrSection, type VolOiSection,
  type WallsSection, PREFS_NAMESPACE, SECTIONS, TONE_TEXT, WINDOWS, analyticsOf, buildUpTone, lakhs, lakhsChange, plain, signTone, signed,
  strikeBars, strikeText, thresholdOf, timeLines, windowRows,
} from "./chainAnalytics";

/**
 * M96 N1 (owner 11 Nov): "Chain analytics" below the option chain's analysis tabs, for the symbol and expiry on screen.
 * Six sections (build-up, OI walls, PCR, IV rank, DEX / GEX, volume / OI): numbers first, a table and a small chart each,
 * how each is worked out inside Details. It reads the same shared analyses answer as the tabs above (one broker chain
 * read per refresh); every number comes from the server. Information only: nothing here places an order.
 */
export interface ChainAnalyticsProps {
  symbol: string;
  expiry?: string | null;
  refreshKey?: number | string;
  load?: (symbol: string, expiry?: string | null) => Promise<AnalysesResponse>;
  /** A fresh read (not the shared one), after he saves his own threshold. */
  reload?: (symbol: string, expiry?: string | null) => Promise<AnalysesResponse>;
  fetcher?: typeof request;
}

export default function ChainAnalyticsV2({ symbol, expiry = null, refreshKey, load = fetchAnalysesShared, reload = fetchAnalyses,
  fetcher = request }: ChainAnalyticsProps) {
  const [data, setData] = useState<AnalysesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!symbol.trim());
  const [section, setSection] = useState<AnalyticsKey>("buildup");
  const [each, setEach] = useState<number | null>(10);
  const [fresh, setFresh] = useState(0);
  const loader = useRef(load);
  loader.current = load;
  const reloader = useRef(reload);
  reloader.current = reload;
  const asked = `${symbol.trim().toUpperCase()}|${expiry ?? ""}`;
  const shown = useRef(asked);

  useEffect(() => {
    if (shown.current !== asked) { shown.current = asked; setData(null); setError(null); }   // another symbol: never the old numbers
    if (!symbol.trim()) { setLoading(false); return; }
    let live = true;
    setLoading(true);
    (fresh ? reloader.current : loader.current)(symbol, expiry)
      .then((d) => { if (live) { setData(d); setError(null); } })
      .catch(() => { if (live) setError("The chain analytics could not be read. Try again in a moment."); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [asked, symbol, expiry, refreshKey, fresh]);

  return (
    <ChainAnalyticsView data={data} error={error} loading={loading} section={section} onSection={setSection} each={each} onEach={setEach}
      threshold={<ThresholdEditor fetcher={fetcher} onSaved={() => setFresh((n) => n + 1)} />} />
  );
}

const PANEL = "rounded-panel border border-subtle bg-surface-1 p-space-3 md:p-space-4";

/** The analytics for a response already in hand (pure: no reads, no timers). */
export function ChainAnalyticsView({ data, error, loading, section, onSection, each, onEach, threshold }: {
  data: AnalysesResponse | null; error: string | null; loading: boolean; section: AnalyticsKey; onSection: (k: AnalyticsKey) => void;
  each: number | null; onEach: (e: number | null) => void; threshold?: ReactNode;
}) {
  const plainState = data ? stateMessage(data) : null;
  const a = analyticsOf(data);
  if (!data || plainState || !a) {
    const text = plainState ?? error ?? (loading ? "Reading the option chain…" : data ? "Not available for this chain." : "Pick a symbol.");
    return (
      <section className={PANEL} aria-label="Chain analytics" data-chain-analytics={data?.state ?? "empty"}>
        <h2 className="text-t15 font-semibold text-fg">Chain analytics</h2>
        <p className="mt-space-1 text-t14 text-fg-muted">{text}</p>
      </section>
    );
  }
  const spot = data.spot ?? null;
  const current = a[section];
  return (
    <section className={cx("space-y-space-3", PANEL)} aria-label="Chain analytics" data-chain-analytics="ok">
      <div className="flex flex-wrap items-baseline justify-between gap-space-2">
        <h2 className="text-t15 font-semibold text-fg">Chain analytics · {data.symbol}{data.expiry ? ` · expiry ${data.expiry}` : ""}</h2>
        <p className="text-t12 text-fg-muted" data-chain-analytics-note>{a.note}</p>
      </div>
      {error && <p className="text-t12 text-fg-muted">{error}</p>}
      <SubTabs label="Chain analytics" idPrefix="oca" value={section} onChange={onSection} layout="scroll"
        items={SECTIONS.map((s) => ({ key: s.key, label: s.label, short: s.short, ariaLabel: s.label }))} />
      <div role="tabpanel" id={`oca-panel-${section}`} aria-labelledby={`oca-tab-${section}`} tabIndex={0} data-section={section}
        data-state={current.state} className="space-y-space-3 rounded-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        {section !== "walls" && (
          <div className="flex flex-wrap items-center gap-space-1" role="group" aria-label="Strikes shown">
            <span className="text-t12 text-fg-muted">Strikes</span>
            {WINDOWS.map((w) => (
              <button key={w.label} type="button" aria-pressed={each === w.each} onClick={() => onEach(w.each)} data-window={w.label}
                className={cx("min-h-8 rounded-control border px-space-2 text-t12", each === w.each ? "border-brand text-fg" : "border-subtle text-fg-muted")}>
                {w.label}
              </button>
            ))}
          </div>
        )}
        {section === "buildup" && <BuildUp s={a.buildup} spot={spot} each={each} />}
        {section === "walls" && <Walls s={a.walls} />}
        {section === "pcr" && <Pcr s={a.pcr} spot={spot} each={each} />}
        {section === "iv" && <Iv s={a.iv} spot={spot} each={each} />}
        {section === "exposure" && <Exposure s={a.exposure} spot={spot} each={each} />}
        {section === "volume_oi" && <VolOi s={a.volume_oi} spot={spot} each={each} threshold={threshold} />}
        <Details id={`option-chain.analytics.${section}`} label="How it is worked out">
          <p>{current.method}</p>
          {a.snapshots > 0 && a.since && <p>{a.snapshots} earlier snapshots of this session since {formatTimeIST(a.since)}.</p>}
        </Details>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- parts

function Tile({ label, value, sub, tone = "muted", testId }: { label: string; value: string; sub?: string; tone?: "up" | "down" | "muted"; testId?: string }) {
  return (
    <div className="min-w-0 rounded-control border border-subtle bg-surface-2 px-space-2 py-space-1" data-tile={testId}>
      <p className="truncate text-t12 text-fg-muted">{label}</p>
      <p className={cx("font-tabular text-t15 font-semibold", tone === "muted" ? "text-fg" : TONE_TEXT[tone])}>{value}</p>
      {sub && <p className="truncate text-t12 text-fg-muted">{sub}</p>}
    </div>
  );
}

const TILES = "grid grid-cols-2 gap-space-2 md:grid-cols-4";

function Message({ text }: { text: string | null | undefined }) {
  return text ? <p className="text-t14 text-fg-muted" data-section-message>{text}</p> : null;
}

interface Col<T> {
  key: string; label: string; cell: (r: T) => ReactNode; figure?: boolean; tone?: (r: T) => "up" | "down" | "muted"; mark?: (r: T) => boolean;
  /** A mirrored chain table's side: on a phone only the chosen side shows, after the strike. */
  side?: "call" | "put";
}

const PHONE_SIDES = [{ key: "call", label: "Calls" }, { key: "put", label: "Puts" }] as const;

/**
 * A table of server numbers. Phone (390): a mirrored table (calls | strike | puts) shows the strike FIRST and one side at
 * a time (Calls / Puts toggle); from md all columns show in the mirrored order. A plain table keeps its first column
 * (strike or time) pinned while it scrolls sideways.
 */
function Table<T extends { strike?: number; time?: string }>({ title, rows, cols, atm }: { title: string; rows: readonly T[]; cols: Col<T>[]; atm?: number | null }) {
  const [phoneSide, setPhoneSide] = useState<"call" | "put">("call");
  const mirrored = cols.some((c) => c.side);
  const strike = cols.find((c) => c.key === "k");
  const shown: (Col<T> & { cls?: string })[] = mirrored && strike
    ? [{ ...strike, key: "k-phone", cls: "sticky left-0 z-10 md:hidden" },
       ...cols.map((c) => ({ ...c, cls: c.key === "k" || (c.side && c.side !== phoneSide) ? "hidden md:table-cell" : undefined }))]
    : cols.map((c, i) => ({ ...c, cls: i === 0 ? "sticky left-0 z-10" : undefined }));
  const pinned = (c: { cls?: string }) => !!c.cls?.includes("sticky");
  return (
    <div className="space-y-space-1" data-table={title}>
      {mirrored && (
        <div className="md:hidden" data-phone-sides>
          <SubTabs label={`${title}: side`} kind="toggle" size="sm" value={phoneSide} onChange={setPhoneSide} items={PHONE_SIDES} />
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-max border-collapse text-t13">
          <caption className="pb-space-1 text-left text-t12 font-semibold text-fg-muted">{title}</caption>
          <thead>
            <tr className="border-b border-subtle">
              {shown.map((c) => (
                <th key={c.key} scope="col" data-col={c.key}
                  className={cx("px-space-2 py-space-1 font-normal text-fg-muted", c.figure === false ? "text-left" : "text-right", c.cls, pinned(c) && "bg-surface-1")}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const isAtm = atm != null && r.strike === atm;
              return (
                <tr key={r.strike ?? r.time ?? i} className={cx("border-b border-subtle last:border-0", isAtm && "bg-surface-2")} data-row={r.strike ?? r.time}>
                  {shown.map((c) => {
                    const marked = c.mark?.(r) ?? false;
                    return (
                      <td key={c.key} data-unusual={marked || undefined} data-col={c.key}
                        className={cx("px-space-2 py-space-1", c.figure === false ? "text-left" : "text-right font-tabular",
                          c.tone ? TONE_TEXT[c.tone(r)] : "text-fg", marked && "bg-warn/10 font-semibold", c.cls,
                          pinned(c) && (isAtm ? "bg-surface-2" : "bg-surface-1"))}>
                        {c.cell(r)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function atmOf(rows: readonly { strike: number }[], spot: number | null): number | null {
  if (spot == null || !rows.length) return null;
  return rows.reduce((b, r) => (Math.abs(r.strike - spot) < Math.abs(b - spot) || (Math.abs(r.strike - spot) === Math.abs(b - spot) && r.strike < b) ? r.strike : b), rows[0].strike);
}

// ---------------------------------------------------------------- 1. build-up

function BuildUp({ s, spot, each }: { s: BuildUpSection; spot: number | null; each: number | null }) {
  const rows = windowRows(s.rows ?? [], spot, each);
  const f = s.futures;
  return (
    <div className="space-y-space-3" data-analytics="buildup">
      <Message text={s.message} />
      {s.state === "ok" && (
        <>
          <p className="text-t12 text-fg-muted">Since {s.reference_time ? formatTimeIST(s.reference_time) : EMPTY}</p>
          <MiniChart title="OI change by strike" chart={strikeBars(rows, [
            { key: "put", label: "Put OI change", tone: "up", value: (r) => r.put_oi_change },
            { key: "call", label: "Call OI change", tone: "down", value: (r) => r.call_oi_change },
          ], spot, "OI change")} />
          <Table title="Build-up per strike" atm={atmOf(s.rows, spot)} rows={rows} cols={[
            { key: "cp", side: "call", label: "Call price chg", cell: (r) => signed(r.call_price_change), tone: (r) => signTone(r.call_price_change) },
            { key: "co", side: "call", label: "Call OI chg", cell: (r) => lakhsChange(r.call_oi_change), tone: (r) => signTone(r.call_oi_change) },
            { key: "ct", side: "call", label: "Call", figure: false, cell: (r) => r.call_label, tone: (r) => buildUpTone(r.call_class) },
            { key: "k", label: "Strike", cell: (r) => strikeText(r.strike) },
            { key: "pt", side: "put", label: "Put", figure: false, cell: (r) => r.put_label, tone: (r) => buildUpTone(r.put_class) },
            { key: "po", side: "put", label: "Put OI chg", cell: (r) => lakhsChange(r.put_oi_change), tone: (r) => signTone(r.put_oi_change) },
            { key: "pp", side: "put", label: "Put price chg", cell: (r) => signed(r.put_price_change), tone: (r) => signTone(r.put_price_change) },
          ]} />
        </>
      )}
      <div className="space-y-space-2" data-analytics="futures">
        <h3 className="text-t14 font-semibold text-fg">Futures OI build-up</h3>
        {f.state !== "ok" || !f.latest ? <Message text={f.message} /> : (
          <>
            <div className={TILES}>
              <Tile label="Now" value={f.latest.label} tone={buildUpTone(f.latest.class)} testId="futures-tag" />
              <Tile label="Price chg" value={signed(f.latest.price_change)} tone={signTone(f.latest.price_change)} />
              <Tile label="OI chg" value={lakhsChange(f.latest.oi_change)} tone={signTone(f.latest.oi_change)} />
              <Tile label="Futures OI" value={lakhs(f.latest.oi)} sub={f.since ? `since ${formatTimeIST(f.since)}` : undefined} />
            </div>
            <MiniChart title="Futures OI" chart={timeLines(f.points, [{ key: "oi", label: "Futures OI", tone: "warn", value: (p) => p.oi }], "Futures OI")} />
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 2. walls

function Walls({ s }: { s: WallsSection }) {
  const n = s.now;
  const series = s.series ?? [];
  return (
    <div className="space-y-space-3" data-analytics="walls">
      {n && (
        <div className={TILES}>
          <Tile label="Highest call OI" value={strikeText(n.call_oi_strike)} sub={lakhs(n.call_oi)} tone="down" testId="call-wall" />
          <Tile label="Highest put OI" value={strikeText(n.put_oi_strike)} sub={lakhs(n.put_oi)} tone="up" testId="put-wall" />
          <Tile label="Most call OI added" value={strikeText(n.call_add_strike)} sub={lakhsChange(n.call_add)} tone="down" testId="call-add" />
          <Tile label="Most put OI added" value={strikeText(n.put_add_strike)} sub={lakhsChange(n.put_add)} tone="up" testId="put-add" />
        </div>
      )}
      <Message text={s.message} />
      {series.length > 1 && (
        <>
          <MiniChart title="Wall strikes through the session" chart={timeLines(series, [
            { key: "call", label: "Highest call OI", tone: "down", value: (p) => p.call_oi_strike },
            { key: "put", label: "Highest put OI", tone: "up", value: (p) => p.put_oi_strike },
          ], "Strike")} />
          <Table title={`Shift through the session (${s.strikes_in_every_snapshot ?? 0} strikes in every snapshot)`} rows={series} cols={[
            { key: "t", label: "Time", figure: false, cell: (p) => formatTimeIST(p.time) },
            { key: "s", label: "Spot", cell: (p) => plain(p.spot) },
            { key: "c", label: "Call OI wall", cell: (p) => strikeText(p.call_oi_strike) },
            { key: "p", label: "Put OI wall", cell: (p) => strikeText(p.put_oi_strike) },
            { key: "ca", label: "Call added", cell: (p) => strikeText(p.call_add_strike) },
            { key: "pa", label: "Put added", cell: (p) => strikeText(p.put_add_strike) },
          ]} />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 3. PCR

function Pcr({ s, spot, each }: { s: PcrSection; spot: number | null; each: number | null }) {
  const per = s.per_strike ?? [];
  const rows = windowRows(per, spot, each);
  return (
    <div className="space-y-space-3" data-analytics="pcr">
      <div className={TILES}>
        <Tile label="OI PCR" value={plain(s.oi?.pcr)} sub={`P ${lakhs(s.oi?.put)} · C ${lakhs(s.oi?.call)}`} testId="pcr-oi" />
        <Tile label="Change-in-OI PCR" value={plain(s.change_oi?.pcr)} sub={`P ${lakhsChange(s.change_oi?.put)} · C ${lakhsChange(s.change_oi?.call)}`} testId="pcr-chg" />
        <Tile label="Volume PCR" value={plain(s.volume?.pcr)} sub={`P ${lakhs(s.volume?.put)} · C ${lakhs(s.volume?.call)}`} testId="pcr-vol" />
        <Tile label="Strikes" value={formatNumber(s.strikes ?? null)} sub="whole chain" />
      </div>
      {s.change_oi?.basis === "session" && <p className="text-t12 text-fg-muted">OI change since the session&apos;s first snapshot</p>}
      <MiniChart title="PCR by strike" chart={strikeBars(rows, [{ key: "pcr", label: "Put / call OI", tone: "brand", value: (r) => r.pcr }], spot, "PCR")} />
      <Table title="PCR per strike" atm={atmOf(per, spot)} rows={rows} cols={[
        { key: "k", label: "Strike", cell: (r) => strikeText(r.strike) },
        { key: "c", label: "Call OI", cell: (r) => lakhs(r.call_oi) },
        { key: "p", label: "Put OI", cell: (r) => lakhs(r.put_oi) },
        { key: "r", label: "PCR", cell: (r) => plain(r.pcr) },
      ]} />
    </div>
  );
}

// ---------------------------------------------------------------- 4. IV

function Iv({ s, spot, each }: { s: IvSection; spot: number | null; each: number | null }) {
  const r = s.rank;
  const per = s.per_strike ?? [];
  const rows = windowRows(per, spot, each);
  const history = r ? `${r.sessions} sessions` : EMPTY;
  return (
    <div className="space-y-space-3" data-analytics="iv">
      {r && (
        <div className={TILES}>
          <Tile label="ATM IV" value={r.atm_iv == null ? EMPTY : `${plain(r.atm_iv)}%`} testId="atm-iv" />
          <Tile label="IV percentile" value={r.enough ? plain(r.percentile, 1) : EMPTY} sub={history} testId="iv-percentile" />
          <Tile label="IV rank" value={r.enough && r.rank != null ? plain(r.rank, 1) : EMPTY} sub={history} testId="iv-rank" />
          <Tile label="Low · high" value={r.enough ? `${plain(r.low)} · ${plain(r.high)}` : EMPTY} />
        </div>
      )}
      {r && !r.enough && (
        <p className="text-t14 text-fg-muted" data-iv-history="short">Not enough history: {r.sessions} of {r.min_sessions} sessions stored.</p>
      )}
      <Message text={s.message} />
      {rows.length > 0 && (
        <>
          {s.reference_time && (
            <MiniChart title="IV change by strike" chart={strikeBars(rows, [
              { key: "put", label: "Put IV change", tone: "up", value: (x) => x.put_iv_change },
              { key: "call", label: "Call IV change", tone: "down", value: (x) => x.call_iv_change },
            ], spot, "IV points")} />
          )}
          <Table title={s.reference_time ? `IV per strike, change since ${formatTimeIST(s.reference_time)}` : "IV per strike"} atm={atmOf(per, spot)} rows={rows} cols={[
            { key: "ci", side: "call", label: "Call IV %", cell: (x) => plain(x.call_iv) },
            { key: "cc", side: "call", label: "Call IV chg", cell: (x) => signed(x.call_iv_change), tone: (x) => signTone(x.call_iv_change) },
            { key: "k", label: "Strike", cell: (x) => strikeText(x.strike) },
            { key: "pc", side: "put", label: "Put IV chg", cell: (x) => signed(x.put_iv_change), tone: (x) => signTone(x.put_iv_change) },
            { key: "pi", side: "put", label: "Put IV %", cell: (x) => plain(x.put_iv) },
          ]} />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 5. DEX / GEX

function Exposure({ s, spot, each }: { s: ExposureSection; spot: number | null; each: number | null }) {
  if (s.state !== "ok") return <Message text={s.message} />;
  const all = s.rows ?? [];
  const rows = windowRows(all, spot, each);
  return (
    <div className="space-y-space-3" data-analytics="exposure">
      <div className={TILES}>
        <Tile label="Net DEX (₹ Cr)" value={signed(s.net_dex)} tone={signTone(s.net_dex)} testId="net-dex" />
        <Tile label="Net GEX (₹ Cr / 1%)" value={signed(s.net_gex)} tone={signTone(s.net_gex)} testId="net-gex" />
        <Tile label="GEX flip" value={strikeText(s.flip ?? null)} sub={s.flips && s.flips.length > 1 ? `${s.flips.length} sign changes` : undefined} testId="flip" />
        <Tile label="Lot size" value={formatNumber(s.lot_size ?? null)} />
      </div>
      <MiniChart title="Net GEX by strike" chart={strikeBars(rows, [{ key: "gex", label: "Net GEX (₹ Cr / 1%)", tone: "brand", value: (r) => r.net_gex }], spot, "₹ Cr")} />
      <Table title="Exposure per strike (₹ Cr)" atm={atmOf(all, spot)} rows={rows} cols={[
        { key: "k", label: "Strike", cell: (r) => strikeText(r.strike) },
        { key: "cd", label: "Call DEX", cell: (r) => signed(r.call_dex), tone: (r) => signTone(r.call_dex) },
        { key: "pd", label: "Put DEX", cell: (r) => signed(r.put_dex), tone: (r) => signTone(r.put_dex) },
        { key: "nd", label: "Net DEX", cell: (r) => signed(r.net_dex), tone: (r) => signTone(r.net_dex) },
        { key: "ng", label: "Net GEX", cell: (r) => signed(r.net_gex), tone: (r) => signTone(r.net_gex) },
        { key: "cg", label: "Running GEX", cell: (r) => signed(r.cumulative_gex ?? null), tone: (r) => signTone(r.cumulative_gex ?? null) },
      ]} />
    </div>
  );
}

// ---------------------------------------------------------------- 6. volume / OI

function VolOi({ s, spot, each, threshold }: { s: VolOiSection; spot: number | null; each: number | null; threshold?: ReactNode }) {
  const all = s.rows ?? [];
  const rows = windowRows(all, spot, each);
  return (
    <div className="space-y-space-3" data-analytics="volume_oi">
      <div className={TILES}>
        <Tile label="Your threshold" value={s.threshold == null ? "Off" : plain(s.threshold)} testId="threshold" />
        <Tile label="Above it" value={s.threshold == null ? EMPTY : formatNumber(s.flagged ?? 0)} sub="strike sides" testId="flagged" />
      </div>
      {threshold}
      <Message text={s.message} />
      {s.state === "ok" && (
        <>
          <MiniChart title="Volume / OI by strike" chart={strikeBars(rows, [
            { key: "put", label: "Put volume / OI", tone: "up", value: (r) => r.put_ratio },
            { key: "call", label: "Call volume / OI", tone: "down", value: (r) => r.call_ratio },
          ], spot, "Volume / OI")} />
          <Table title="Volume / OI per strike" atm={atmOf(all, spot)} rows={rows} cols={[
            { key: "cv", side: "call", label: "Call vol", cell: (r) => lakhs(r.call_volume) },
            { key: "co", side: "call", label: "Call OI", cell: (r) => lakhs(r.call_oi) },
            { key: "cr", side: "call", label: "Call vol/OI", cell: (r) => plain(r.call_ratio), mark: (r) => r.call_unusual },
            { key: "k", label: "Strike", cell: (r) => strikeText(r.strike) },
            { key: "pr", side: "put", label: "Put vol/OI", cell: (r) => plain(r.put_ratio), mark: (r) => r.put_unusual },
            { key: "po", side: "put", label: "Put OI", cell: (r) => lakhs(r.put_oi) },
            { key: "pv", side: "put", label: "Put vol", cell: (r) => lakhs(r.put_volume) },
          ]} />
        </>
      )}
    </div>
  );
}

interface PrefsRead { prefs?: Record<string, unknown>; versions?: Record<string, number> }

/** His own volume / OI highlight threshold (prefs `chain_analytics`): empty until he sets one (M68: nothing pre-set). */
export function ThresholdEditor({ fetcher = request, onSaved }: { fetcher?: typeof request; onSaved?: () => void }) {
  const [text, setText] = useState("");
  const [version, setVersion] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    fetcher<PrefsRead>("/me/prefs").then((r) => {
      if (!live) return;
      const own = r.prefs?.[PREFS_NAMESPACE] as { unusual_vol_oi?: unknown } | undefined;
      setText(typeof own?.unusual_vol_oi === "number" ? String(own.unusual_vol_oi) : "");
      setVersion(r.versions?.[PREFS_NAMESPACE] ?? 0);
    }).catch(() => { /* intentional: the field stays empty; saving still works */ });
    return () => { live = false; };
  }, [fetcher]);

  async function save() {
    const { value, problem } = thresholdOf(text);
    if (problem) { setNote(problem); return; }
    setBusy(true);
    try {
      const q = version === null ? "" : `?version=${version}`;
      const r = await fetcher<{ value: { unusual_vol_oi?: number | null }; version: number }>(`/me/prefs/${PREFS_NAMESPACE}${q}`,
        { method: "PUT", body: JSON.stringify({ unusual_vol_oi: value }) });
      setVersion(r.version);
      setText(typeof r.value?.unusual_vol_oi === "number" ? String(r.value.unusual_vol_oi) : "");
      setNote(value === null ? "Cleared." : "Saved.");
      onSaved?.();
    } catch (e) {
      setNote(friendlyError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-end gap-space-2" data-threshold-editor>
      <label className="space-y-space-1 text-t13 text-fg">
        <span>Highlight volume / OI above</span>
        <input className="h-11 w-28 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg focus:border-brand focus:outline-none md:h-9"
          inputMode="decimal" value={text} placeholder="Your number" data-threshold-input
          onChange={(e) => { setText(e.target.value); setNote(null); }} />
      </label>
      <button type="button" disabled={busy} onClick={() => void save()} data-threshold-save
        className="inline-flex h-11 items-center rounded-control border border-subtle px-space-3 text-t14 font-semibold text-brand hover:bg-surface-2 md:h-9">
        {busy ? "Saving…" : "Save"}
      </button>
      {note && <p className="text-t12 text-fg-muted" role="status">{note}</p>}
    </div>
  );
}
