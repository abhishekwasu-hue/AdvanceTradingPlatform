import { Clock, Eye, EyeOff, Layers, ShieldAlert, ShieldCheck, Wifi, WifiOff } from "lucide-react";
import { cx } from "../components/primitives/cx";
import { toneClass } from "../dashboard/CardV2";
import { statusSummary, type CombinedExit } from "../fno/combinedExit";
import type { TerminalStatus } from "./api";
import { feedChip, marginNote, modeChip, money, openCaption, signTone, type Tone } from "./statusModel";

/**
 * Trading Terminal T1: the fixed status bar. Desktop: today's MTM, the open positions' MTM, the funds available, the
 * margin used (always labelled an estimate in practice mode, TT-19), the price feed, the mode chip (shown, never
 * changed), the kill switch's state and the privacy mask. Phone (`compact`): MTM, funds and the mode, with the mask
 * button. Stateless: the page reads the figures and keeps the mask (per device).
 * CX6: the active combined exit (`combined`) as a chip with the distance to trigger, and the CX-F1 "stale price" mark.
 */
const ITEM = "flex min-w-0 flex-col";
const LABEL = "text-t12 text-fg-muted";
const VALUE = "whitespace-nowrap font-tabular text-t15 font-semibold";
const tone = (t: Tone) => (t === "neutral" ? "text-fg" : toneClass(t));

export function StatusBarV2({ status, failed, masked, onMask, compact = false, combined = [], showMode = true, showFeed = true }: {
  status: TerminalStatus | null; failed: boolean; masked: boolean; onMask: () => void; compact?: boolean;
  combined?: readonly CombinedExit[];
  /** TERM-2 step 1: false where the context bar already shows the one PAPER badge (one badge per screen). */
  showMode?: boolean;
  /** TERM-2 step 2 / CT-1 C9: false where the shell's feed dot and the chart's live chip already say it (one freshness mark). */
  showFeed?: boolean;
}) {
  const mode = modeChip(status);
  const cx6 = statusSummary(combined, masked);
  const combinedChip = cx6 && (
    <span data-status="combined-exit" data-stale={cx6.stale ? "" : undefined} title={cx6.title}
      className={cx("inline-flex min-w-0 items-center gap-space-1 truncate text-t13 font-medium", cx6.stale ? "text-warn" : "text-fg",
        compact && "order-last w-full")}>
      {cx6.stale ? <Clock size={14} aria-hidden className="shrink-0" /> : <Layers size={14} aria-hidden className="shrink-0" />}
      <span className="truncate">{cx6.text}{cx6.stale ? " · stale price" : ""}</span>
    </span>
  );
  const maskButton = (
    <button type="button" onClick={onMask} aria-pressed={masked} data-status="mask"
      aria-label={masked ? "Show amounts" : "Hide amounts"} title={masked ? "Show amounts" : "Hide amounts on this device"}
      className="ml-auto inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-control border border-border text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand lg:min-h-9 lg:min-w-9">
      {masked ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}
    </button>
  );
  const modeChipEl = (
    <span title={mode.title} data-status="mode"
      className="inline-flex shrink-0 items-center gap-space-1 rounded-pill border border-warn bg-warn-tint px-space-2 py-px text-t13 font-semibold text-warn">
      <ShieldCheck size={14} aria-hidden />{mode.label}
    </span>
  );
  const shell = cx("flex items-center border-t border-subtle bg-surface-1 px-space-4 py-space-2 shadow-raised",
    compact ? "sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-[3] -mx-space-4 gap-space-3" : "sticky bottom-0 z-[3] gap-space-4 rounded-panel border");

  if (!status) {
    return (
      <section aria-label="Status bar" data-block="terminal-status" data-state={failed ? "error" : "loading"} className={shell}>
        <p className="min-w-0 flex-1 text-t13 text-fg-muted" role="status">
          {failed ? "Your figures could not be read. We will try again in a moment." : "Loading your figures…"}
        </p>
        {showMode && modeChipEl}
        {maskButton}
      </section>
    );
  }

  const today = signTone(status.today.mtm);
  if (compact) {
    return (
      <section aria-label="Status bar" data-block="terminal-status" data-compact="" className={cx(shell, cx6 && "flex-wrap gap-y-space-1")}>
        <div className={ITEM} data-status="today" title={status.today.note}>
          <span className={LABEL}>Today</span>
          <span className={cx(VALUE, masked ? "text-fg" : tone(today))}>{money(status.today.mtm, masked, true)}</span>
        </div>
        <div className={ITEM} data-status="funds" title={status.funds.note}>
          <span className={LABEL}>Funds</span>
          <span className={cx(VALUE, "text-fg")}>{money(status.funds.available, masked)}</span>
        </div>
        {combinedChip}
        {showMode && modeChipEl}
        {maskButton}
      </section>
    );
  }

  const open = signTone(status.open.mtm);
  const feed = feedChip(status.feed.state);
  const caption = openCaption(status);
  return (
    <section aria-label="Status bar" data-block="terminal-status" className={cx(shell, "flex-wrap gap-y-space-2")}>
      <div className={ITEM} data-status="today" title={status.today.note}>
        <span className={LABEL}>Today&apos;s MTM</span>
        <span className={cx(VALUE, masked ? "text-fg" : tone(today))}>{money(status.today.mtm, masked, true)}</span>
      </div>
      <div className={ITEM} data-status="open">
        <span className={LABEL}>Open MTM{caption ? ` · ${caption}` : ""}</span>
        <span className={cx(VALUE, masked ? "text-fg" : tone(open))}>{status.open.count === 0 ? money(0, masked, true) : money(status.open.mtm, masked, true)}</span>
      </div>
      <div className={ITEM} data-status="funds" title={status.funds.note}>
        <span className={LABEL}>Funds available</span>
        <span className={cx(VALUE, "text-fg")}>{money(status.funds.available, masked)}</span>
      </div>
      <div className={ITEM} data-status="margin" title={`${marginNote(status)} ${status.margin.note}`}>
        <span className={LABEL}>Margin used <span className="text-fg-muted">(estimate)</span></span>
        <span className={cx(VALUE, "text-fg")}>{money(status.margin.used, masked)}</span>
      </div>
      {showFeed && <span data-status="feed" data-state={status.feed.state} title={status.feed.message}
        className={cx("inline-flex shrink-0 items-center gap-space-1 text-t13 font-medium", tone(feed.tone))}>
        {status.feed.state === "none" || status.feed.state === "reconnecting" || status.feed.state === "renew_needed"
          ? <WifiOff size={14} aria-hidden /> : <Wifi size={14} aria-hidden />}
        {feed.label}
      </span>}
      {showMode && modeChipEl}
      <span data-status="kill-switch" data-engaged={status.kill_switch.engaged ? "" : undefined} title={status.kill_switch.message}
        className={cx("inline-flex shrink-0 items-center gap-space-1 text-t13 font-medium", status.kill_switch.engaged ? "text-warn" : "text-fg-muted")}>
        {status.kill_switch.engaged ? <ShieldAlert size={14} aria-hidden /> : <ShieldCheck size={14} aria-hidden />}
        {status.kill_switch.engaged ? "New orders stopped" : "New orders allowed"}
      </span>
      {combinedChip}
      {maskButton}
    </section>
  );
}
