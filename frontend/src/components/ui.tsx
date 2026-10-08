import { ChevronDown, Info, type LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";

export function Card({ title, children, className = "" }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-border bg-surface-1 shadow-card p-4 ${className}`}>
      {title && (
        <div className="text-[11px] font-bold uppercase tracking-wider text-fg-muted mb-3">{title}</div>
      )}
      {children}
    </div>
  );
}

/** A card that folds to its title bar. Closed by default unless `defaultOpen`; the choice is
 * remembered per browser under `storageKey`. Children mount on first open and then stay mounted
 * (hidden when folded), so a chat or a form keeps its state. */
export function CollapsibleCard({ title, subtitle, children, storageKey, defaultOpen = false, className = "" }: {
  title: string; subtitle?: string; children: ReactNode; storageKey: string; defaultOpen?: boolean; className?: string;
}) {
  const key = `atp_card_open:${storageKey}`;
  const [open, setOpen] = useState<boolean>(() => {
    try { const v = localStorage.getItem(key); return v == null ? defaultOpen : v === "1"; } catch { return defaultOpen; }
  });
  const [mounted, setMounted] = useState(open);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) setMounted(true);
    try { localStorage.setItem(key, next ? "1" : "0"); } catch { /* storage unavailable: session only */ }
  };
  return (
    <div className={`rounded-xl border border-border bg-surface-1 shadow-card ${open ? "p-4" : "px-4 py-2.5"} ${className}`}>
      <button onClick={toggle} aria-expanded={open} className="flex w-full items-center gap-2 text-left">
        <span className="text-[11px] font-bold uppercase tracking-wider text-fg-muted">{title}</span>
        {!open && subtitle && <span className="truncate text-xs text-fg-muted/80">{subtitle}</span>}
        <ChevronDown size={14} className={`ml-auto shrink-0 text-fg-muted transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {mounted && <div className={open ? "mt-3" : "hidden"}>{children}</div>}
    </div>
  );
}

/** P1.3: a figure is coloured only when it has a sign - zero (and anything not a number) stays neutral. */
export function signTone(value: number | null | undefined, decimals = 2): "default" | "up" | "down" {
  if (value == null || !Number.isFinite(value)) return "default";
  const shown = Number(value.toFixed(decimals));
  return shown > 0 ? "up" : shown < 0 ? "down" : "default";
}

export function StatTile({
  label, value, tone = "default", icon: Icon, accentClass,
}: {
  label: string;
  value: ReactNode;
  tone?: "default" | "up" | "down";
  icon?: LucideIcon;
  /** Deprecated (P1.3: colour only for meaning) - accepted for old call sites and ignored; a tile is neutral unless
   * `tone` says up / down. */
  accentClass?: string;
}) {
  void accentClass;
  const toneClass = tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-fg";
  const iconToneClass = tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-fg-muted";
  return (
    <div className="rounded-xl border border-border bg-surface-1 shadow-card px-4 py-3">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
        {Icon && <Icon size={12} className={iconToneClass} />}
        {label}
      </div>
      <div className={`font-tabular text-2xl font-extrabold mt-0.5 ${toneClass}`}>{value}</div>
    </div>
  );
}

export function DirectionBadge({ direction }: { direction: "LONG" | "SHORT" | "NO_TRADE" }) {
  const styles =
    direction === "LONG"
      ? "bg-up/15 text-up border-up/40"
      : direction === "SHORT"
        ? "bg-down/15 text-down border-down/40"
        : "bg-surface-2 text-fg-muted border-border";
  return (
    <span className={`inline-block rounded-md border px-2 py-0.5 text-xs font-semibold tracking-wide ${styles}`}>
      {direction}
    </span>
  );
}

export function GradeBadge({ grade }: { grade: string }) {
  const tone =
    // A grade is a quality label, not a direction: the top grade is marked, the rest stay neutral (P1.3).
    grade === "A1" ? "bg-brand/10 text-brand border-brand/40" : "bg-surface-2 text-fg-muted border-border";
  return <span className={`inline-block rounded-md border px-2 py-0.5 text-xs font-semibold ${tone}`}>{grade}</span>;
}

export function DemoDataBanner() {
  return (
    <div className="mb-4 flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/[0.07] px-3 py-2.5 text-xs text-warn">
      <Info size={14} className="shrink-0 mt-0.5" />
      <span>
        Using deterministic sample OHLCV data. Every strategy, score and chart below is computed for
        real by the backend against this data; pages with a Data switch can use your broker's candles
        instead once a broker is logged in under Settings.
      </span>
    </div>
  );
}

/**
 * Master prompt section 47: every screen that shows simulated results, model-generated content
 * or a score carries the same plain statement of what it is not. `kind` picks the sentence that
 * names the specific thing on that page; the second sentence is common to all of them.
 */
export function Disclaimer({ kind }: { kind: "backtest" | "signals" | "ai" | "score" }) {
  const lead = {
    backtest:
      "Backtest results are simulated on historical data with modelled costs and fills. They do not predict future results; live fills, slippage and liquidity will differ.",
    signals:
      "Signals, grades and scores are rule-based decision support computed from price data. They are not investment advice and carry no assurance of profit.",
    ai:
      "Strategies produced by the builder or parser are generated from your description and must be reviewed and backtested before any deployment. Generated rules can be wrong.",
    score:
      "Scores and ratings summarise the inputs entered; they are analytical aids, not recommendations to buy, sell or hold any security.",
  }[kind];
  return (
    <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2/60 px-3 py-2 text-[11px] text-fg-muted">
      <Info size={13} className="shrink-0 mt-0.5" />
      <span>
        {lead} Trading in equities and derivatives involves substantial risk of loss and is not suitable for
        every investor. You remain responsible for every order placed from your account.
      </span>
    </div>
  );
}

export function ProgressBar({ pct }: { pct: number }) {
  const clamped = Math.max(0, Math.min(100, pct));
  const color = clamped >= 80 ? "bg-up" : clamped >= 60 ? "bg-warn" : "bg-down";
  return (
    <div className="h-1.5 w-full rounded-full bg-surface-2 overflow-hidden">
      <div className={`h-1.5 rounded-full transition-all ${color}`} style={{ width: `${clamped}%` }} />
    </div>
  );
}
