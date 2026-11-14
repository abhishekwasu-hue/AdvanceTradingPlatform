import { ShieldCheck } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { usePolled, dataOf } from "../dashboard/useDashboardData";
import { vixBand } from "../dashboard/model";
import { api } from "../api/client";
import { INDEX_SYMBOLS, STOCK_SYMBOLS, tradingDaysText, expiryComboOptions, formatStrike, symbolOptions } from "../optionChain/model";
import { prevCloseOf, spotChange } from "../optionChain/spotChange";
import type { OptionChainData } from "../optionChain/useOptionChain";
import { Combobox } from "../ui/Combobox";
import { formatNumber, formatPrice, formatTimeIST } from "../ui/format";
import type { GlossaryKey } from "../ui/glossary";
import { TermHint } from "../ui/TermHint";
import { LevelChip } from "../ui/LevelChip";
import { Signed } from "../ui/numbersFirst";
import { cx } from "../components/primitives/cx";
import { spotAsOf, type TerminalSpot } from "./spot";

/**
 * TERM-2 step 1 (owner 30 Oct, docs/design/TERM2.md): the Terminal's context bar - ONE active symbol + expiry for every
 * panel, and the figures a trader checks before an order: the spot with its Signed change, India VIX's band, PCR, days to
 * expiry, and one PAPER badge.
 *
 * - Symbol: the option chain page's own picker and list (indices + F&O stocks); a symbol outside the list (a saved
 *   layout, a deep link) stays first.
 * - Expiry: the chain page's own `useOptionChain` - its default is the nearest expiry, on an expiry day the next one
 *   (the user's chain setting). Changing the symbol or expiry here changes the chart, chain, order and basket together.
 * - The link colour (MultiChartV2's group A) marks the bar and every panel it drives.
 * - Nothing here is a suggestion: figures only, from the chain read and the Today strip.
 */
const STRIP_MS = 60_000;

export const LINK_COLOUR = "rgb(var(--chart-ov-1))";

/** The figures the bar shows, from the chain, the spot on screen and the Today strip. Pure. */
export function contextFigures(chain: OptionChainData, spot: TerminalSpot | null, prevClose: number | null, vix: number | null, now: number = Date.now()) {
  const s = chain.live?.summary ?? null;
  // Owner bug 13 Nov: the page's ONE spot (the newest of tick / LTP / chain - terminal/spot.ts), the chain's spot card shows
  // the same; "as of <time>" once it is not current, so an old number never reads as live.
  const ltp = spot ? spot.ltp : s?.spot ?? null;
  const asOf = spotAsOf(spot ?? (s?.spot ? { ltp: s.spot, at: chain.live?.data_as_of ?? chain.live?.fetched_at ?? null, source: "chain" } : null),
    chain.live?.market_open, now);
  return {
    spot: ltp,
    spotAsOf: asOf,
    moved: spotChange(ltp, prevClose),
    pcr: s?.pcr_oi_atm ?? null,                    // M96: Trade's PCR over ATM ± 6 (the chain banner's, one definition)
    dte: s?.days_to_expiry ?? null,
    atm: s?.atm_strike ?? null,
    vix: vixBand(vix),
    vixValue: vix,
  };
}

/** India VIX and the underlying's previous close from the Today strip (one read a minute). */
export function useStripFigures(symbol: string, enabled: boolean): { prevClose: number | null; vix: number | null } {
  const [strip] = usePolled(api.marketStrip, () => STRIP_MS, enabled);
  const data = dataOf(strip) ?? null;
  return useMemo(() => {
    const v = data?.indices.find((i) => i.key === "INDIA VIX")?.ltp;
    return { prevClose: prevCloseOf(data, symbol), vix: typeof v === "number" && v > 0 ? v : null };
  }, [data, symbol]);
}

export function ContextBarV2({ symbol, onSymbol, chain, spot, prevClose, vix, mode, compact = false, tools }: {
  symbol: string;
  onSymbol: (symbol: string) => void;
  chain: OptionChainData;
  spot: TerminalSpot | null;
  prevClose: number | null;
  vix: number | null;
  /** The account's mode from the Terminal status ("PAPER" in Phase 0); shown once, here. */
  mode: { label: string; title: string };
  /** Phone and tablet: the figures wrap under the pickers. */
  compact?: boolean;
  /** The layout controls, on the same row before the PAPER badge (from 1024 px; owner 14 Nov). */
  tools?: ReactNode;
}) {
  const f = contextFigures(chain, spot, prevClose, vix);
  const extra = useMemo(() => (new Set<string>([...INDEX_SYMBOLS, ...STOCK_SYMBOLS]).has(symbol) ? null : symbol), [symbol]);
  const expiries = chain.expiries?.expiries.length ? chain.expiries.expiries : chain.expiry ? [{ date: chain.expiry, kind: "weekly" as const }] : [];
  const expiryOptions = expiryComboOptions(expiries);
  const paper = mode.label.toUpperCase() === "PAPER";
  // Owner 14 Nov ("space waste hot aahe, important panel khali dablet"): one compact strip - no stacked "Symbol" / "Expiry"
  // labels (each picker names itself to a screen reader and in its tooltip), little vertical padding, and the layout
  // controls (`tools`) on the same row, so the panels start as high as they can.
  return (
    <section aria-label="Symbol and expiry" data-block="terminal-context" data-symbol={symbol} data-expiry={chain.expiry ?? ""}
      className={cx("relative flex flex-wrap items-center gap-x-space-2 gap-y-space-2 rounded-panel border border-border border-l-4 bg-surface-1 px-space-3",
        compact ? "pb-space-2 pt-space-3" : "py-space-1")}
      style={{ borderLeftColor: LINK_COLOUR }}>
      <div className={compact ? "grid w-full min-w-0 grid-cols-2 gap-space-2" : "flex min-w-0 items-center gap-space-2"}>
        <div className="min-w-0" title="Symbol">
          <Combobox label="Symbol" value={symbol} onChange={onSymbol} searchable className={compact ? "w-full" : undefined}
            options={symbolOptions(extra)} dataAttrs={{ "data-terminal": "symbol" }} />
        </div>
        <div className="min-w-0" title="Expiry">
          <Combobox label="Expiry" value={chain.expiry ?? ""} disabled={expiries.length === 0} className={compact ? "w-full" : undefined}
            placeholder={chain.loading ? "Loading…" : "Nearest expiry"} onChange={chain.setExpiry} options={expiryOptions}
            dataAttrs={{ "data-terminal": "expiry" }} />
        </div>
      </div>
      {/* Owner 14 Nov: each figure on one line (its name small and muted before it), wrapping to a second line beside the
          pickers instead of a label stacked over every value - the strip keeps the pickers' height. */}
      {/* With the layout controls: one row from 1400 px; narrower, the pickers, the controls and PAPER keep the first row and
          the figures take the full second row (never squeezed under the controls). */}
      <dl className={cx("flex min-w-0 flex-1 flex-wrap items-center gap-x-space-3 gap-y-px text-t12",
        tools ? "order-last basis-full min-[1400px]:order-none min-[1400px]:basis-[28rem]" : "basis-[28rem]")} data-context-figures>
        <Figure name="spot" label="Spot">
          <span className="text-t16 font-semibold text-fg">{formatPrice(f.spot)}</span>
          {f.moved && <span className="ml-space-1"><Signed value={f.moved.pct} kind="pct" /></span>}
          {f.spotAsOf && <span className="ml-space-1 text-t12 font-normal text-fg-muted" data-spot-as-of>as of {formatTimeIST(f.spotAsOf)}</span>}
        </Figure>
        <Figure name="vix" label="India VIX" term="india-vix">
          {f.vix ? <span className="inline-flex items-center gap-space-1"><span className="font-semibold text-fg">{formatNumber(f.vixValue, 2)}</span><LevelChip scale="vix" level={f.vix.level} /></span> : "–"}
        </Figure>
        <Figure name="pcr" label="PCR" term="pcr">{formatNumber(f.pcr, 2)}</Figure>
        <Figure name="atm" label="ATM" term="atm">{formatStrike(f.atm)}</Figure>
        <Figure name="dte" label="Expiry in"><DaysLeft text={tradingDaysText(f.dte)} /></Figure>
      </dl>
      {tools && <div className="ml-auto flex min-w-0 items-center" data-context-tools>{tools}</div>}
      <span title={mode.title} data-terminal="mode" data-mode={paper ? "paper" : "live"}
        className={cx("inline-flex items-center gap-space-1 rounded-full border px-space-2 text-t12 font-bold tracking-wide",
          // On a phone the badge sits on the bar's top edge (right), so it never takes a row of its own; the solid surface
          // under the tint keeps the edge line from showing through.
          compact ? "absolute -top-space-3 right-space-3 min-h-6 bg-surface-1" : cx("relative min-h-9", !tools && "ml-auto"),
          paper ? "border-warn/40 text-warn" : "border-down/40 text-down")}>
        <span aria-hidden className={cx("pointer-events-none absolute inset-0 rounded-full", paper && "bg-warn-tint")} />
        {compact && <ShieldCheck size={14} aria-hidden className="relative" />}<span className="relative">{paper ? "PAPER" : "LIVE"}</span>
      </span>
    </section>
  );
}

/** "7 trading days": the number at the bar's figure size, its unit small and muted, so the bar keeps one row on a
 * 1440 desktop (the PAPER badge wrapped to a second row and pushed the panels down, main red 13 Nov). */
function DaysLeft({ text }: { text: string }) {
  const m = /^(\d+)( trading days?)$/.exec(text);
  if (!m) return <>{text}</>;
  return <>{m[1]}<span className="text-t12 font-normal text-fg-muted">{m[2]}</span></>;
}

function Figure({ name, label, children, term }: { name: string; label: string; children: ReactNode; term?: GlossaryKey }) {
  return (
    <div className="flex min-w-0 items-center gap-space-1 whitespace-nowrap" data-figure={name}>
      <dt className="flex items-center gap-space-1 text-fg-muted">{label}{term && <TermHint term={term} floating />}</dt>
      <dd className="font-tabular text-t14 font-semibold text-fg">{children}</dd>
    </div>
  );
}
