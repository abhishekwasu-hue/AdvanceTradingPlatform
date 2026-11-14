import { ChevronDown } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { useMarketStatus } from "../chart/useMarketStatus";
import { cx } from "../components/primitives/cx";
import type { OiBannerResponse, OiHistoryResponse } from "../oi/types";
import { formatLakhs, formatNumber, formatTimeIST } from "../ui/format";
import { OI_POLL_MS, shouldPoll } from "./oiTab";
import {
  PCR_ZONE_LABEL, PCR_ZONE_TONE, bannerTone, calendarDte, classText, expiryLine, pcrZone, signalTone, todayIst, type BannerTone,
} from "./oiDiff";
import OppositeOiAlertV2 from "./OppositeOiAlertV2";
import { useShownBlock } from "./panelBlocks";
import type { LiveChain } from "./types";

/**
 * M96 L1 (owner, 11 Nov: "taken from the Trade repo AS IS"): the Trade dashboard's OI banner on top of the chain view,
 * with ATP's port values (the day's stored readings replayed by app/option_chain/oi_regime.py, served by
 * /option-chain/{u}/banner and /history):
 * - the colour by direction (green / red / amber / grey);
 * - the message: Trade's meaning in neutral fact words with the symbol's name (owner defaults 1 and 4; the server's
 *   `fact_message`); "Market closed" while the market is closed (Trade);
 * - "Put: <class> · Call: <class>" in English (default 2);
 * - the PCR over ATM ± 6 (the OI-difference table's window, the same number) with Trade's five zones in English;
 * - the expiry with CALENDAR days left (Trade compute_dte), "Expiry today" at 0, in red at one day or less;
 * - Trade's caption while this is the day's first reading ("Not enough data" until the history builds), and the time;
 * - under it Trade's History table: Time, Total Call OI, Total Put OI, Diff, Δ Diff and the stable Signal (the
 *   hysteresis-confirmed one) in 5 / 10 / 15 minute buckets, newest first, lakhs.
 * The spot / future, ATM and max pain (whole chain, as Trade) are the summary cards right above (SummaryStripV2).
 *
 * Owner bug report 13 Nov (the Terminal showed only the Net Diff table's per-strike Dominance, no signal and no
 * 5 / 10 / 15 choice):
 * - the banner's "Signal" line: the stable OI Diff signal of the latest reading - Trade's compute_oi_signal_with_hysteresis
 *   and its labels (BULLISH (Strong) / BULLISH (Weakening) / BEARISH (Strong) / BEARISH (Weakening) / NEUTRAL, Trade's
 *   emoji left out), coloured as Trade's History table colours them (a weakening side in the OTHER side's colour);
 * - the "View interval" choice (Trade: 5 / 10 / 15 minutes, 5 first) stays in sight above the History table; a choice
 *   opens the table. The Net Diff table's per-strike Dominance column is Trade's own and stays as it is.
 * Owner order 13 Nov: the "Alert me on opposite OI moves" bell beside the View interval (./OppositeOiAlertV2.tsx); with it
 * on, a History row whose window closed with an opposite call / put OI move says so under its Signal.
 * Owner 14 Nov (the Terminal's chain panel, `collapsible`): the banner starts as ONE line - Trade's signal (else its
 * message) and its PCR line, as they are - with Show / Hide; the choice is remembered on this browser. Shown, the
 * banner is exactly as above.
 */
export type Interval = 5 | 10 | 15;

export interface TradeBannerState { banner: OiBannerResponse | null; history: OiHistoryResponse | null; loaded: boolean }

/** The banner and the history together; again every minute only while the market is open. */
export function useTradeBanner(underlying: string, interval: Interval): TradeBannerState {
  const market = useMarketStatus();
  const nse = market.status?.sessions?.NSE;
  const statusOpen = nse ? nse.is_open : null;
  const [state, setState] = useState<TradeBannerState>({ banner: null, history: null, loaded: false });
  const [tick, setTick] = useState(0);
  const shown = useRef(underlying);
  useEffect(() => {
    let live = true;
    if (shown.current !== underlying) {           // another symbol: never show the old one's reading
      shown.current = underlying;
      setState({ banner: null, history: null, loaded: false });
    }
    Promise.allSettled([api.oiBanner(underlying), api.oiHistory(underlying, interval)]).then(([b, h]) => {
      if (!live) return;
      setState((prev) => ({ banner: b.status === "fulfilled" ? b.value : prev.banner, history: h.status === "fulfilled" ? h.value : prev.history,
        loaded: true }));
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

export default function TradeOiBannerV2({ symbol, live, collapsible = false }: { symbol: string; live: LiveChain | null;
  /** Owner 14 Nov (the Terminal's chain panel): one summary line until opened; the choice is remembered on this browser. */
  collapsible?: boolean }) {
  const [interval, setMinutes] = useState<Interval>(5);
  const state = useTradeBanner(symbol, interval);
  const [shown, setShown] = useShownBlock("oi-banner", false);
  return <TradeOiBannerView symbol={symbol} live={live} state={state} interval={interval} onInterval={setMinutes}
    bell={<OppositeOiAlertV2 symbol={symbol} />} collapse={collapsible ? { shown, onShown: setShown } : undefined} />;
}

/** Owner 14 Nov: the collapsed banner's one line - Trade's own signal (else its message) and its PCR line, as they are. */
export function bannerSummary(l: BannerLines): string {
  return `${l.signal ? `Signal: ${l.signal}` : l.message} · ${l.pcr}`;
}

const BORDER: Record<BannerTone, string> = { up: "border-l-up", down: "border-l-down", warn: "border-l-warn", neutral: "border-l-border" };
const TINT: Record<BannerTone, string> = { up: "bg-up-tint", down: "bg-down-tint", warn: "bg-warn-tint", neutral: "bg-surface-1" };
const TEXT = { up: "text-up", down: "text-down", warn: "text-warn", muted: "text-fg-muted" } as const;

export interface BannerLines {
  tone: BannerTone;
  message: string;
  /** Trade's stable OI Diff signal of the latest reading (null before the day's first reading). */
  signal: string | null;
  signalTone: "up" | "down" | "muted";
  classes: string;
  pcr: string;
  pcrZone: string | null;
  pcrTone: "up" | "down" | "warn" | null;
  expiry: string | null;
  expiryNear: boolean;
  readingAt: string | null;
  /** The day's first reading (Trade's caption: it shows "Not enough data", the reading needs history). */
  firstReading: boolean;
}

/** What the banner shows, line by line (pure). */
export function bannerLines(symbol: string, banner: OiBannerResponse | null, live: LiveChain | null, today: string): BannerLines {
  const b = banner?.banner ?? null;
  const closed = banner?.market_open === false;
  const tone = closed || !b ? "neutral" : bannerTone(b.direction);
  const message = closed ? "Market closed" : b ? (b.fact_message ?? "No clear movement") : `No OI reading of ${symbol} yet today`;
  const classes = closed || !b ? "Put: Not enough data · Call: Not enough data" : `Put: ${classText(b.put_class)} · Call: ${classText(b.call_class)}`;
  const pcr = live?.summary.pcr_oi_atm ?? null;
  const zone = pcrZone(pcr);
  const expiry = live?.expiry ?? null;
  const dte = calendarDte(expiry, today);
  const signal = b?.signal || null;
  return {
    tone, message, signal, signalTone: signalTone(signal), classes,
    pcr: pcr === null ? "PCR (ATM ± 6): not available" : `PCR (ATM ± 6): ${formatNumber(pcr, 2)}`,
    pcrZone: zone ? PCR_ZONE_LABEL[zone] : null, pcrTone: zone ? PCR_ZONE_TONE[zone] : null,
    expiry: expiryLine(expiry, dte), expiryNear: dte !== null && dte <= 1,
    readingAt: b ? formatTimeIST(b.data_as_of) : null,
    firstReading: !closed && (!b || b.first_of_day),
  };
}

/** Trade's "View Interval" choice (5 / 10 / 15 minutes, 5 first): in sight above the History table (owner 13 Nov). */
export const INTERVALS = [5, 10, 15] as const;

export function IntervalPicker({ id, interval, onPick }: { id: string; interval: Interval; onPick: (n: Interval) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-space-2" data-oi-interval>
      <span className="text-t12 font-medium text-fg-muted" id={id}>View interval</span>
      <div role="radiogroup" aria-labelledby={id} className="flex gap-space-2">
        {INTERVALS.map((n) => (
          <button key={n} type="button" role="radio" aria-checked={interval === n} onClick={() => onPick(n)} data-oi-interval-option={n}
            className={cx("inline-flex min-h-11 items-center rounded-pill border px-space-3 text-t13 md:min-h-8",
              interval === n ? "border-brand bg-brand-tint font-semibold text-fg" : "border-border text-fg-muted hover:text-fg")}>
            {n} min
          </button>
        ))}
      </div>
    </div>
  );
}

/** The banner for data already in hand (pure: no reads, no timers) - tests and screenshots render it directly. */
export function TradeOiBannerView({ symbol, live, state, interval, onInterval, now = Date.now(), bell, collapse }: {
  symbol: string; live: LiveChain | null; state: TradeBannerState; interval: Interval; onInterval: (i: Interval) => void; now?: number;
  bell?: ReactNode;
  /** Owner 14 Nov: with it, a one-line summary toggles the banner (its content unchanged when shown). */
  collapse?: { shown: boolean; onShown: (shown: boolean) => void };
}) {
  const l = bannerLines(symbol, state.banner, live, todayIst(now));
  const rows = state.history?.rows ?? [];
  const [open, setOpen] = useState(false);
  const intervalId = useId();
  const toggle = collapse && (
    <button type="button" aria-expanded={collapse.shown} onClick={() => collapse.onShown(!collapse.shown)} data-banner-toggle
      data-banner-tone={collapse.shown ? undefined : l.tone}
      className={cx("flex min-h-11 w-full items-center justify-between gap-space-2 rounded-panel border border-l-4 border-subtle px-space-2 text-left text-t12 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8",
        BORDER[l.tone], collapse.shown ? "bg-surface-1" : TINT[l.tone])}>
      {collapse.shown
        ? <span className="font-medium text-fg-muted">OI banner and history</span>
        : <span className="min-w-0 truncate font-semibold text-fg" data-banner-summary title={bannerSummary(l)}>
            {l.signal ? <>Signal: <span className={TEXT[l.signalTone]}>{l.signal}</span></> : l.message} · {l.pcr}
          </span>}
      <span className="inline-flex shrink-0 items-center gap-space-1 font-medium text-brand">
        {collapse.shown ? "Hide" : "Show"}
        <ChevronDown size={14} aria-hidden className={cx("transition-transform duration-fast", collapse.shown && "rotate-180")} />
      </span>
    </button>
  );
  if (collapse && !collapse.shown) {
    return <section aria-label="OI banner" data-block="oi-banner" data-collapsed className="space-y-space-2">{toggle}</section>;
  }
  return (
    <section aria-label="OI banner" data-block="oi-banner" className="space-y-space-2">
      {toggle}
      <div data-banner-tone={l.tone} className={cx("rounded-panel border border-l-4 border-subtle px-space-3 py-space-3 md:px-space-4", BORDER[l.tone], TINT[l.tone])}>
        <div className="text-t16 font-semibold text-fg" data-banner-message>{l.message}</div>
        {l.signal && <div className="mt-space-1 text-t14 font-semibold text-fg" data-banner-signal>
          Signal: <span className={TEXT[l.signalTone]} data-banner-signal-label>{l.signal}</span>
        </div>}
        <div className="mt-space-1 text-t12 text-fg-muted" data-banner-classes>{l.classes}</div>
        <div className="mt-space-1 text-t14 font-semibold text-fg" data-banner-pcr>
          {l.pcr}{l.pcrZone && <> <span aria-hidden>-</span> <span className={l.pcrTone ? TEXT[l.pcrTone] : undefined}>{l.pcrZone}</span></>}
        </div>
        {l.expiry && <div className={cx("mt-space-1 text-t13 font-semibold", l.expiryNear ? "text-down" : "text-fg")} data-banner-expiry>{l.expiry}</div>}
        <div className="mt-space-1 text-t12 text-fg-muted" data-banner-time>{l.readingAt ? `OI reading at ${l.readingAt}` : "No OI reading yet"}</div>
      </div>
      {l.firstReading && <p className="text-t12 text-fg-muted" data-banner-caption>The first reading of the day shows "Not enough data": the reading needs history.</p>}
      <IntervalPicker id={intervalId} interval={interval} onPick={(n) => { onInterval(n); setOpen(true); }} />
      {bell}
      <details className="rounded-panel border border-subtle bg-surface-1" data-oi-history-trade open={open}
        onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
        <summary className="flex min-h-11 cursor-pointer items-center px-space-3 text-t14 font-semibold text-fg md:min-h-9">OI history (Signal)</summary>
        <div className="space-y-space-2 px-space-3 pb-space-3">
          {rows.length === 0 ? (
            <p className="text-t13 text-fg-muted">No OI reading is stored for today yet.</p>
          ) : (
            <>
            {/* Owner 13 Nov: the unit once above the table; the cells carry the bare figures. */}
            <p className="text-right text-t12 text-fg-muted" data-oi-unit>(OI in lakhs)</p>
            <div className="max-h-[22rem] overflow-auto" data-oi-history-trade-scroll>
              <table className="w-full min-w-[34rem] whitespace-nowrap text-t12" aria-label={`${symbol} OI history`}>
                <thead className="sticky top-0 bg-surface-1 text-fg-muted">
                  <tr>{["Time", "Total Call OI", "Total Put OI", "Diff", "Δ Diff", "Signal"].map((h) => (
                    <th key={h} scope="col" className={cx("px-space-1 py-space-1 font-medium", h === "Time" || h === "Signal" ? "text-left" : "text-right")}>{h}</th>
                  ))}</tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.slot} className="border-t border-subtle font-tabular font-semibold" data-history-slot={r.slot}>
                      <td className="px-space-1 py-space-1 text-left text-fg">{formatTimeIST(r.bucket ?? r.slot, { suffix: false })}</td>
                      <td className="px-space-1 py-space-1 text-right text-fg">{formatLakhs(r.total_call_oi, { unit: false })}</td>
                      <td className="px-space-1 py-space-1 text-right text-fg">{formatLakhs(r.total_put_oi, { unit: false })}</td>
                      <td className={cx("px-space-1 py-space-1 text-right", r.diff > 0 ? "text-up" : r.diff < 0 ? "text-down" : "text-fg")}>{formatLakhs(r.diff, { signed: true, unit: false })}</td>
                      <td className={cx("px-space-1 py-space-1 text-right", r.delta_diff > 0 ? "text-up" : r.delta_diff < 0 ? "text-down" : "text-fg")}>{formatLakhs(r.delta_diff, { signed: true, unit: false })}</td>
                      <td className={cx("px-space-1 py-space-1 text-left", TEXT[signalTone(r.signal)])} data-history-signal>{r.signal}
                        {r.opposite && <div className="text-t12 font-semibold text-warn" data-history-opposite>
                          Opposite OI move: Call {formatLakhs(r.opposite.call_change, { signed: true, unit: false })} · Put {formatLakhs(r.opposite.put_change, { signed: true, unit: false })}
                        </div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            </>
          )}
          <p className="text-t12 text-fg-muted">Diff = Total Put OI − Total Call OI over ATM ± 6 (a level). Δ Diff = the change from the
            reading before. "Weakening": the side is not confirmed yet (a change of side needs three readings in a row and a real
            opposite move of call and put OI).</p>
        </div>
      </details>
    </section>
  );
}
