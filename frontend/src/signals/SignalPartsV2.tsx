import { AlarmClock, Check, ChartCandlestick, Clock, Ellipsis, TriangleAlert } from "lucide-react";
import { Suspense, lazy, useId, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import type { LevelLine } from "../chart/chartCore";
import type { TimeframeId } from "../chart/timeframes";
import { ApiError } from "../api/errors";
import StepUpDialog, { isStepUpError } from "../components/StepUpDialog";
import { cx } from "../components/primitives";
import { snoozeChoices } from "../proposals/snooze";
import { chartTabPath } from "../shell/shellRoutes";
import type { AiAction } from "../types";
import { formatChange, formatDateIST, formatINR, formatLots, formatNumber, formatPrice, formatSignedINR, formatTimeIST } from "../ui/format";
import { glossary, type GlossaryKey } from "../ui/glossary";
import {
  canDecide, decisionFailure, decisionMessage, directionLabel, effectiveStatus, levelDelta, riskSentences, sizeAndLoss, sourceCaption, statusLabel,
  type FeedTab, type SignalFeedItem,
} from "./feed";
import { LEVEL_LABELS, reasonWords, setupWords } from "./signalText";

/**
 * UI2-3 (UI v2 proposal §7, §8): the Signals inbox's building blocks - the status chip, Long / Short, the level tiles,
 * the levels chart (UI2-4e: the v2 chart with the levels marked), the six key figures, the risk sentences and the decision buttons. Colour carries meaning only:
 * green / red for Long / Short and the user's own exit levels (UX3: those levels sit in the "Your levels" fold), brand for "Waiting" and the one primary action, neutral
 * outlines for every other status.
 */

/** "7 Oct" - the day of a signal, without the year (History). */
export function signalDay(at: string): string {
  return formatDateIST(at).replace(/ \d{4}$/, "");
}

export function signalTime(at: string, tab: FeedTab): string {
  const time = formatTimeIST(at, { suffix: false });
  return tab === "history" ? `${signalDay(at)}, ${time}` : time;
}

export function StatusChip({ item, now, short = false }: { item: SignalFeedItem; now: Date; short?: boolean }) {
  const status = effectiveStatus(item, now);
  const label = short && status === "waiting" ? "Waiting" : statusLabel(item, now);
  if (status === "waiting") {
    return (
      <span data-status={status} className="inline-flex shrink-0 items-center gap-space-1 whitespace-nowrap rounded-pill bg-brand/10 px-space-2 py-0.5 text-t12 font-semibold text-brand">
        <Clock size={13} aria-hidden />{label}
      </span>
    );
  }
  return (
    <span data-status={status} className="inline-flex shrink-0 items-center gap-space-1 whitespace-nowrap rounded-pill border border-border px-space-2 py-0.5 text-t12 font-medium text-fg-muted">
      {status === "snoozed" && <AlarmClock size={13} aria-hidden />}{label}
    </span>
  );
}

export function Direction({ value, className }: { value: SignalFeedItem["direction"]; className?: string }) {
  const label = directionLabel(value);
  if (!label) return null;
  return <span className={cx("font-semibold", value === "LONG" ? "text-up" : "text-down", className)}>{label}</span>;
}

/** A "?" next to a term: its one-line meaning from the glossary (§9 rule 1). A 20 px mark with a 44 px touch area. */
export function TermTip({ term }: { term: GlossaryKey }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const entry = glossary(term);
  return (
    <span className="relative inline-flex">
      <button type="button" aria-label={`What is ${entry.term}?`} aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}
        className="relative inline-flex h-5 w-5 items-center justify-center rounded-pill border border-border text-xs font-semibold text-fg-muted after:absolute after:-inset-3 after:content-[''] hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        ?
      </button>
      {open && (
        <span id={id} role="note" className="absolute left-0 top-6 z-10 w-56 rounded-panel border border-subtle bg-surface-1 p-space-3 text-t12 font-normal text-fg shadow-raised">
          {entry.definition}
        </span>
      )}
    </span>
  );
}

/** The user's own three levels in a row of tiles, with neutral labels (LEVEL_LABELS). UX3 fix 10: only inside the
 * "Your levels" fold, never in the signal text itself. */
export function LevelTiles({ item }: { item: SignalFeedItem }) {
  if (item.entry === null && item.stop === null && item.target === null) return null;
  return (
    <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-panel border border-subtle bg-border-subtle">
      {([[LEVEL_LABELS.entry, item.entry], [LEVEL_LABELS.stop, item.stop], [LEVEL_LABELS.target, item.target]] as const).map(([label, value]) => (
        <div key={label} className="bg-surface-1 px-space-3 py-space-2">
          <dt className="text-t12 text-fg-muted">{label}</dt>
          <dd className="font-tabular text-t15 font-semibold text-fg">{formatPrice(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** UX3 fix 10: the user's own levels, one tap away (collapsed): the numbers his approval needs, from his strategy's
 * settings. The signal text above it stays in structure words. */
export function YourLevels({ item }: { item: SignalFeedItem }) {
  if (item.entry === null && item.stop === null && item.target === null) return null;
  return <LevelsFold><div className="px-space-3 pb-space-3"><LevelTiles item={item} /></div></LevelsFold>;
}

function LevelsFold({ children }: { children: ReactNode }) {
  return (
    <details className="group rounded-panel border border-subtle" data-your-levels>
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-space-2 px-space-3 text-t14 font-semibold text-brand [&::-webkit-details-marker]:hidden">
        <span className="whitespace-nowrap">Your levels</span>
        <span className="min-w-0 text-right text-t12 font-normal text-fg-muted">from your strategy&apos;s settings</span>
      </summary>
      {children}
    </details>
  );
}

// UI2-4e: the v2 chart, its own chunk (the chart library loads with it, not with the inbox).
const EmbeddedChartV2 = lazy(() => import("../chart/EmbeddedChartV2"));

/** The levels a signal's chart marks: Entry neutral, Stop red, Target green (as the levels drawing). Pure. */
export function signalLevels(item: Pick<SignalFeedItem, "entry" | "stop" | "target">): LevelLine[] {
  const all: [number | null, string, LevelLine["tone"]][] = [[item.target, LEVEL_LABELS.target, "up"], [item.entry, LEVEL_LABELS.entry, "neutral"], [item.stop, LEVEL_LABELS.stop, "down"]];
  return all.filter(([p]) => p !== null && Number.isFinite(p)).map(([price, title, tone]) => ({ price: price as number, title, tone }));
}

/** UI2-4e (Charts page plan §3, CHP-10): the signal's chart - the v2 chart of its symbol with Entry, Stop and Target
 * marked and the trader's chart settings. Its timeframe is the detail's, so the one "Open chart" button carries it
 * (the chart's own link is off). While its code loads, the levels drawn to scale hold its place. */
export function SignalChart({ item, timeframe, onTimeframe }: { item: SignalFeedItem; timeframe: TimeframeId; onTimeframe: (tf: TimeframeId) => void }) {
  return (
    <div data-signal-chart={item.symbol}>
      <Suspense fallback={<div aria-busy="true" className="flex min-h-[303px] items-center rounded-panel border border-subtle"><LevelsChart item={item} /></div>}>
        <EmbeddedChartV2 symbol={item.symbol} exchange={item.exchange || "NSE"} levels={signalLevels(item)}
          timeframe={timeframe} onTimeframe={onTimeframe} openLink={false} />
      </Suspense>
    </div>
  );
}

/** The level lines (Entry neutral, Stop red, Target green), drawn to scale - what the chart marks. */
export function LevelsChart({ item }: { item: SignalFeedItem }) {
  const { entry, stop, target } = item;
  const levels = [entry, stop, target].filter((v): v is number => v !== null && Number.isFinite(v));
  if (entry === null || levels.length < 2) return null;
  const lo = Math.min(...levels);
  const hi = Math.max(...levels);
  const pad = (hi - lo) * 0.18 || 1;
  const y = (p: number) => 12 + ((hi + pad - p) / (hi - lo + 2 * pad)) * 136;
  const zone = (a: number, b: number, cls: string) => <rect x={0} width={400} y={Math.min(y(a), y(b))} height={Math.abs(y(a) - y(b))} className={cls} />;
  const line = (p: number, label: string, cls: string, textCls: string) => (
    <g key={label}>
      <line x1={0} x2={400} y1={y(p)} y2={y(p)} strokeWidth={1.5} strokeDasharray="6 4" className={cls} />
      <text x={4} y={y(p) - 5} className={cx("text-xs font-medium", textCls)}>{label}</text>
      <text x={396} y={y(p) - 5} textAnchor="end" className={cx("font-tabular text-xs font-semibold", textCls)}>{formatPrice(p)}</text>
    </g>
  );
  return (
    <svg viewBox="0 0 400 160" className="h-40 w-full" role="img" data-levels-chart
      aria-label={`Your levels: ${LEVEL_LABELS.entry} ${formatPrice(entry)}${stop !== null ? `, ${LEVEL_LABELS.stop} ${formatPrice(stop)}` : ""}${target !== null ? `, ${LEVEL_LABELS.target} ${formatPrice(target)}` : ""}`}>
      {target !== null && zone(entry, target, "fill-up/10")}
      {stop !== null && zone(entry, stop, "fill-down/10")}
      {target !== null && line(target, LEVEL_LABELS.target, "stroke-up", "fill-up")}
      {line(entry, LEVEL_LABELS.entry, "stroke-fg-muted", "fill-fg")}
      {stop !== null && line(stop, LEVEL_LABELS.stop, "stroke-down", "fill-down")}
    </svg>
  );
}

function Figure({ label, value, sub, tone, term }: { label: string; value: string; sub?: string | null; tone?: "down"; term?: GlossaryKey }) {
  return (
    <div className="bg-surface-1 px-space-3 py-space-2">
      <dt className="flex items-center gap-space-1 text-t12 text-fg-muted">{label}{term && <TermTip term={term} />}</dt>
      {/* No wrapping: a sign stays with its amount ("−₹5,250.00" never breaks after the minus, item 1). */}
      <dd className={cx("whitespace-nowrap font-tabular text-t14 font-semibold", tone === "down" ? "text-down" : "text-fg")}>{value}</dd>
      {sub && <dd className="font-tabular text-t12 text-fg-muted">{sub}</dd>}
    </div>
  );
}

/** The figures the approval needs: size, most you can lose and reward : risk (from the existing risk pre-check,
 * risk_by_default; without it "–"). UX3 fix 10: the user's own three levels moved into the "Your levels" fold below
 * them (with their distance from the entry), so the signal itself reads in structure words. */
export function KeyFigures({ item, proposal }: { item: SignalFeedItem; proposal?: AiAction }) {
  const stopDelta = levelDelta(item.stop, item.entry);
  const targetDelta = levelDelta(item.target, item.entry);
  const sized = sizeAndLoss(proposal);
  const levels = item.entry !== null || item.stop !== null || item.target !== null;
  return (
    <div className="space-y-space-3">
      <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-panel border border-subtle bg-border-subtle" data-key-figures>
        <Figure label="Size" value={sized ? formatLots(sized.quantity, sized.lotSize) : "–"} term="lot" />
        <Figure label="Max loss" value={sized ? formatSignedINR(-sized.maxLoss) : "–"} tone={sized ? "down" : undefined} term="max-loss" />
        <Figure label="Reward : risk" value={item.rr !== null ? formatNumber(item.rr, 1) : "–"} term="reward-risk" />
      </dl>
      {levels && (
        <LevelsFold>
          <dl className="mx-space-3 mb-space-3 grid grid-cols-3 gap-px overflow-hidden rounded-panel border border-subtle bg-border-subtle">
            <Figure label={LEVEL_LABELS.entry} value={formatPrice(item.entry)} />
            <Figure label={LEVEL_LABELS.stop} value={formatPrice(item.stop)} sub={stopDelta !== null ? formatChange(stopDelta) : null} />
            <Figure label={LEVEL_LABELS.target} value={formatPrice(item.target)} sub={targetDelta !== null ? formatChange(targetDelta) : null} />
          </dl>
        </LevelsFold>
      )}
    </div>
  );
}

export function Reasons({ item }: { item: SignalFeedItem }) {
  const reasons = reasonWords(item.reasons);   // UX3 fix 10: structure words only
  if (reasons.length === 0) return null;
  return (
    <section>
      <h3 className="text-t15 font-semibold text-fg">Why this signal</h3>
      <ol className="mt-space-2 list-decimal space-y-space-1 pl-space-5 text-t14 text-fg">
        {reasons.map((r) => <li key={r}>{r}</li>)}
      </ol>
    </section>
  );
}

/** "Fits your risk limits": the existing risk pre-check on the waiting proposal, as sentences. */
export function RiskLines({ proposal }: { proposal?: AiAction }) {
  const lines = riskSentences(proposal);
  const ok = lines.length > 0 && !lines.some((l) => l.warn);
  return (
    <section data-risk-lines>
      <h3 className="text-t15 font-semibold text-fg">{lines.length === 0 ? "Your risk limits" : ok ? "Fits your risk limits" : "Check your risk limits"}</h3>
      {lines.length === 0 ? (
        <p className="mt-space-2 text-t14 text-fg-muted">Your limits are checked again when you approve: a trade that breaks one is not placed.</p>
      ) : (
        <ul className="mt-space-2 space-y-space-1 text-t14">
          {lines.map((l) => (
            <li key={l.text} className="flex gap-space-2">
              {l.warn ? <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" /> : <Check size={16} aria-hidden className="mt-0.5 shrink-0 text-fg" />}
              <span className="text-fg">{l.text}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** What happened to a signal that is no longer waiting, in a sentence. */
export function OutcomeLine({ item, now }: { item: SignalFeedItem; now: Date }) {
  const status = effectiveStatus(item, now);
  const text = status === "expired" ? "This signal expired before anyone decided. Nothing was placed."
    : status === "skipped" ? "Skipped. Nothing was placed."
    : status === "taken" ? (item.source.kind === "autopilot" && item.action_id === null ? "Taken by your paper bot." : "Approved.")
    : status === "not_placed" ? "Nothing was placed."
    : status === "snoozed" ? "Snoozed. It comes back to Waiting before it expires."
    : status === "info" ? (item.source.kind === "library" ? "Made with the signal generator. Nothing to decide." : "A scan match. Nothing to decide.")
    : null;
  if (!text) return null;
  return (
    <p className="text-t14 text-fg-muted" data-outcome>
      {text}{item.outcome && status !== "info" ? ` ${item.outcome}` : ""}
    </p>
  );
}

const BUTTON = "inline-flex min-h-11 items-center justify-center gap-space-2 rounded-control text-t14 font-semibold transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:cursor-not-allowed disabled:opacity-50";
const PRIMARY = `${BUTTON} px-space-4 bg-brand text-on-brand hover:bg-brand-strong`;
const OUTLINE = `${BUTTON} border border-border bg-surface-1 text-fg hover:bg-surface-2`;
const SECONDARY = `${OUTLINE} px-space-4`;
const ICON = `${OUTLINE} w-11`;

function clock(d: Date): string {
  return formatTimeIST(d, { suffix: false });
}

function failure(onError: (message: string, stale?: boolean) => void) {
  return (e: unknown) => {
    const { text, stale } = e instanceof ApiError ? decisionFailure(e.status, e.detail) : decisionFailure(null);
    onError(text, stale);
  };
}

/** The ⋯ button that opens the snooze choices (UI v2 §7: "The snooze action stays under More"). */
export function MoreButton({ item, open, controls, onToggle, disabled }: { item: SignalFeedItem; open: boolean; controls: string; onToggle: () => void; disabled?: boolean }) {
  return (
    <button type="button" className={ICON} aria-label={`More for ${item.symbol}`} aria-expanded={open} aria-controls={controls}
      disabled={disabled} onClick={onToggle}>
      <Ellipsis size={18} aria-hidden />
    </button>
  );
}

/** Snooze: 15 minutes, 1 hour or 09:00 IST - never past the expiry (the existing snooze endpoint cuts it there). */
export function SnoozeMenu({ id, item, now, onDone, onError }: {
  id: string; item: SignalFeedItem; now: Date; onDone: (message: string) => void; onError: (message: string, stale?: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const actionId = item.action_id;
  async function snooze(body: { minutes: number } | { until: string }) {
    if (actionId === null) return;
    setBusy(true);
    try { await api.aiSnoozeAction(actionId, body); onDone(decisionMessage("snooze", item)); }
    catch (e) { failure(onError)(e); }
    finally { setBusy(false); }
  }
  return (
    <div id={id} className="space-y-space-2 rounded-panel border border-subtle bg-surface-2 p-space-3" data-snooze-menu>
      <p className="text-t12 font-medium text-fg-muted">Snooze until</p>
      <div className="grid grid-cols-1 gap-space-2 min-[380px]:grid-cols-3">
        {snoozeChoices(now, item.expires_at).map((c) => (
          <button key={c.key} type="button" className={SECONDARY} disabled={busy} onClick={() => void snooze(c.body)}>
            {c.cutAtExpiry ? `It expires (${clock(c.wakesAt)})` : c.key === "15m" ? "15 minutes" : c.key === "1h" ? "1 hour" : `09:00${c.tomorrow ? " tomorrow" : ""}`}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * Approve / Skip / Open chart (and, on the phone card, More with Snooze). Every decision is the existing proposal
 * endpoint: Approve places the PAPER order at the price then (re-quoted by the server; a LIVE one asks for the
 * authenticator first, as today), Skip rejects it, Snooze hides it until later (never past its expiry). Nothing here
 * touches an exit.
 */
export function SignalActions({ item, now, compact = false, canTrade, chartTimeframe, onDone, onError }: {
  item: SignalFeedItem; now: Date; compact?: boolean; canTrade: boolean; onDone: (message: string) => void; onError: (message: string, stale?: boolean) => void;
  /** UI2-4e: the timeframe of the detail's chart, which "Open chart" opens on the Chart tab. */
  chartTimeframe?: TimeframeId;
}) {
  const [busy, setBusy] = useState<"approve" | "skip" | null>(null);
  const [stepUp, setStepUp] = useState<string | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const menuId = useId();
  const decidable = canDecide(item, now);
  const id = item.action_id;
  const chart = (
    <Link to={chartTabPath(item.symbol, item.exchange, chartTimeframe)} aria-label={compact ? `Open chart for ${item.symbol}` : undefined}
      className={compact ? ICON : `${BUTTON} px-space-2 text-brand hover:underline`}>
      {compact ? <ChartCandlestick size={18} aria-hidden /> : "Open chart"}
    </Link>
  );
  if (!decidable || id === null) return <div className="flex flex-wrap gap-space-2">{chart}</div>;
  if (!canTrade) {
    // A viewer reads the inbox; approving, skipping and snoozing need a trading role (the server's require_trader).
    return (
      <div className="space-y-space-2">
        <div className="flex flex-wrap gap-space-2">{chart}</div>
        <p className="text-t12 text-fg-muted" data-view-only>Your role can view signals but not approve or skip them. Ask your organisation owner.</p>
      </div>
    );
  }

  const failed = failure(onError);
  async function approve() {
    if (id === null) return;
    setBusy("approve");
    try { onDone(decisionMessage("approve", item, await api.aiApproveAction(id))); }
    catch (e) { if (isStepUpError(e)) setStepUp((e as ApiError).message); else failed(e); }   // the server's words, no "ApiError:"
    finally { setBusy(null); }
  }
  async function skip() {
    if (id === null) return;
    setBusy("skip");
    try { await api.aiRejectAction(id); onDone(decisionMessage("skip", item)); }
    catch (e) { failed(e); }
    finally { setBusy(null); }
  }
  const live = item.mode === "LIVE";
  // Phase 0: an approved entry is always placed as a PAPER order, also for a LIVE deployment (whose approval still asks
  // for the authenticator code) - the label never promises a live trade.
  const approveLabel = compact ? "Approve" : live ? "Approve (paper order)" : "Approve paper trade";
  return (
    <div className="space-y-space-2">
      <div className={compact ? "grid grid-cols-[1fr_auto_auto_auto] gap-space-2" : "flex flex-wrap items-center gap-space-2"} role="group" aria-label={`Decide on ${item.symbol}`}>
        <button type="button" className={PRIMARY} disabled={busy !== null} aria-busy={busy === "approve" || undefined} onClick={() => void approve()}>
          {busy === "approve" && <span className="h-3.5 w-3.5 animate-spin rounded-pill border-2 border-current border-t-transparent" aria-hidden />}
          {approveLabel}
        </button>
        <button type="button" className={SECONDARY} disabled={busy !== null} aria-busy={busy === "skip" || undefined} onClick={() => void skip()}>Skip</button>
        {chart}
        {compact && <MoreButton item={item} open={moreOpen} controls={menuId} onToggle={() => setMoreOpen((o) => !o)} disabled={busy !== null} />}
      </div>
      {compact && moreOpen && <SnoozeMenu id={menuId} item={item} now={now} onDone={onDone} onError={onError} />}
      {!compact && <p className="text-t12 text-fg-muted">{live ? "This proposal is on a LIVE deployment: approving asks for your authenticator code first, then places a paper order." : "Paper trade: practice money at the real price. Nothing reaches your broker."}</p>}
      {stepUp && <StepUpDialog reason={stepUp} onCancel={() => setStepUp(null)} onVerified={() => { setStepUp(null); void approve(); }} />}
    </div>
  );
}

/** The full detail of one signal: the desktop right column, and a phone card when it is opened. */
export function SignalDetail({ item, proposal, now, onDone, onError, compact = false, canTrade }: {
  item: SignalFeedItem; proposal?: AiAction; now: Date; compact?: boolean; canTrade: boolean;
  onDone: (message: string) => void; onError: (message: string, stale?: boolean) => void;
}) {
  const waiting = canDecide(item, now);
  const [moreOpen, setMoreOpen] = useState(false);
  const [timeframe, setTimeframe] = useState<TimeframeId>("5min");         // the charts' default (chart/timeframes.ts)
  const menuId = useId();
  const modeLabel = item.mode === "LIVE" ? "Live" : item.mode === "PAPER" ? "Paper" : null;
  if (compact) {
    return (
      <div className="space-y-space-3">
        {waiting && <CompactRisk proposal={proposal} />}
        {!waiting && <OutcomeLine item={item} now={now} />}
        <SignalActions item={item} now={now} compact canTrade={canTrade} onDone={onDone} onError={onError} />
        <YourLevels item={item} />
        {reasonWords(item.reasons).length > 0 && (
          <details className="group">
            <summary className="flex min-h-11 cursor-pointer items-center text-t14 font-semibold text-brand">Why this signal</summary>
            <ol className="list-decimal space-y-space-1 pl-space-5 text-t14 text-fg">{reasonWords(item.reasons).map((r) => <li key={r}>{r}</li>)}</ol>
          </details>
        )}
      </div>
    );
  }
  return (
    <section aria-label={`${item.symbol} in detail`} data-signal-detail={item.id}
      className="space-y-space-4 rounded-panel border border-subtle bg-surface-1 p-space-5 shadow-card">
      <div className="flex items-start gap-space-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-space-2">
            <h2 className="text-t20 font-semibold text-fg">{item.symbol}</h2>
            <Direction value={item.direction} className="text-t15" />
            <StatusChip item={item} now={now} />
          </div>
          <p className="mt-space-1 text-t14 text-fg-muted">
            {setupWords(item.setup)} · from {sourceCaption(item)}{modeLabel ? ` · ${modeLabel}` : ""}
          </p>
        </div>
        {waiting && canTrade && <MoreButton item={item} open={moreOpen} controls={menuId} onToggle={() => setMoreOpen((o) => !o)} />}
      </div>
      {waiting && canTrade && moreOpen && <SnoozeMenu id={menuId} item={item} now={now} onDone={onDone} onError={onError} />}
      <SignalChart item={item} timeframe={timeframe} onTimeframe={setTimeframe} />
      {(item.entry !== null || item.stop !== null || item.target !== null || item.rr !== null || proposal?.risk_check) && <KeyFigures item={item} proposal={proposal} />}
      <Reasons item={item} />
      {waiting ? <RiskLines proposal={proposal} /> : <OutcomeLine item={item} now={now} />}
      <SignalActions item={item} now={now} canTrade={canTrade} chartTimeframe={timeframe} onDone={onDone} onError={onError} />
    </section>
  );
}

function CompactRisk({ proposal }: { proposal?: AiAction }) {
  const lines = riskSentences(proposal);
  const sized = sizeAndLoss(proposal);
  if (lines.length === 0) return <p className="text-t14 text-fg-muted">Your limits are checked again when you approve.</p>;
  const warn = lines.find((l) => l.warn);
  return (
    <p className="flex items-start gap-space-2 text-t14 text-fg">
      {warn ? <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" /> : <Check size={16} aria-hidden className="mt-0.5 shrink-0" />}
      <span>{warn ? warn.text : `Fits your risk limits${sized ? ` · most you can lose ${formatINR(sized.maxLoss)}` : ""}`}</span>
    </p>
  );
}
