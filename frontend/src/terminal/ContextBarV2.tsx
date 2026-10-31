import { CalendarDays, ShieldCheck } from "lucide-react";
import { useId, useMemo, type ReactNode } from "react";
import { usePolled, dataOf } from "../dashboard/useDashboardData";
import { vixBand } from "../dashboard/model";
import { api } from "../api/client";
import { INDEX_SYMBOLS, STOCK_SYMBOLS, daysText, expiryComboOptions, formatStrike, symbolOptions } from "../optionChain/model";
import { prevCloseOf, spotChange } from "../optionChain/spotChange";
import type { OptionChainData } from "../optionChain/useOptionChain";
import { Combobox } from "../ui/Combobox";
import { formatNumber, formatPrice } from "../ui/format";
import type { GlossaryKey } from "../ui/glossary";
import { TermHint } from "../ui/TermHint";
import { LevelChip } from "../ui/LevelChip";
import { Signed } from "../ui/numbersFirst";
import { cx } from "../components/primitives/cx";
import type { TerminalSpot } from "./spot";

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
export function contextFigures(chain: OptionChainData, spot: TerminalSpot | null, prevClose: number | null, vix: number | null) {
  const s = chain.live?.summary ?? null;
  const ltp = spot && spot.source !== "chain" ? spot.ltp : s?.spot ?? null;
  return {
    spot: ltp,
    moved: spotChange(ltp, prevClose),
    pcr: s?.pcr_oi ?? null,
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

export function ContextBarV2({ symbol, onSymbol, chain, spot, prevClose, vix, mode, compact = false }: {
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
}) {
  const symbolId = useId();
  const expiryId = useId();
  const f = contextFigures(chain, spot, prevClose, vix);
  const extra = useMemo(() => (new Set<string>([...INDEX_SYMBOLS, ...STOCK_SYMBOLS]).has(symbol) ? null : symbol), [symbol]);
  const expiries = chain.expiries?.expiries.length ? chain.expiries.expiries : chain.expiry ? [{ date: chain.expiry, kind: "weekly" as const }] : [];
  const paper = mode.label.toUpperCase() === "PAPER";
  return (
    <section aria-label="Symbol and expiry" data-block="terminal-context" data-symbol={symbol} data-expiry={chain.expiry ?? ""}
      className="relative flex flex-wrap items-end gap-x-space-4 gap-y-space-2 rounded-panel border border-border border-l-4 bg-surface-1 px-space-3 py-space-2"
      style={{ borderLeftColor: LINK_COLOUR }}>
      <div className={cx("grid min-w-0 grid-cols-2 gap-space-2", compact ? "w-full" : "w-[26rem] max-w-full")}>
        <div className="min-w-0">
          <label htmlFor={symbolId} className="mb-space-1 block text-t12 font-medium text-fg-muted">Symbol</label>
          <Combobox id={symbolId} label="Symbol" value={symbol} onChange={onSymbol} searchable className="w-full"
            options={symbolOptions(extra)} dataAttrs={{ "data-terminal": "symbol" }} />
        </div>
        <div className="min-w-0">
          <label htmlFor={expiryId} className="mb-space-1 flex items-center gap-space-1 text-t12 font-medium text-fg-muted">
            <CalendarDays size={12} aria-hidden />Expiry
          </label>
          <Combobox id={expiryId} label="Expiry" value={chain.expiry ?? ""} disabled={expiries.length === 0} className="w-full"
            placeholder={chain.loading ? "Loading…" : "Nearest"} onChange={chain.setExpiry} options={expiryComboOptions(expiries)}
            dataAttrs={{ "data-terminal": "expiry" }} />
        </div>
      </div>
      <dl className="flex min-w-0 flex-wrap items-end gap-x-space-4 gap-y-space-1 text-t12" data-context-figures>
        <Figure name="spot" label="Spot">
          <span className="text-t16 font-semibold text-fg">{formatPrice(f.spot)}</span>
          {f.moved && <span className="ml-space-1"><Signed value={f.moved.pct} kind="pct" /></span>}
        </Figure>
        <Figure name="vix" label="India VIX" term="india-vix">
          {f.vix ? <span className="inline-flex items-center gap-space-1"><span className="font-semibold text-fg">{formatNumber(f.vixValue, 2)}</span><LevelChip scale="vix" level={f.vix.level} /></span> : "–"}
        </Figure>
        <Figure name="pcr" label="PCR" term="pcr">{formatNumber(f.pcr, 2)}</Figure>
        <Figure name="atm" label="ATM" term="atm">{formatStrike(f.atm)}</Figure>
        <Figure name="dte" label="Expiry in">{daysText(f.dte)}</Figure>
      </dl>
      <span title={mode.title} data-terminal="mode" data-mode={paper ? "paper" : "live"}
        className={cx("inline-flex items-center gap-space-1 rounded-full border px-space-3 text-t12 font-bold tracking-wide",
          // On a phone the badge sits top-right (beside the Expiry label), so it never takes a row of its own.
          compact ? "absolute right-space-3 top-space-2 min-h-6" : "ml-auto min-h-9 self-center",
          paper ? "border-warn/40 bg-warn-tint text-warn" : "border-down/40 text-down")}>
        <ShieldCheck size={14} aria-hidden />{paper ? "PAPER" : "LIVE"}
      </span>
    </section>
  );
}

function Figure({ name, label, children, term }: { name: string; label: string; children: ReactNode; term?: GlossaryKey }) {
  return (
    <div className="min-w-0" data-figure={name}>
      <dt className="flex items-center gap-space-1 text-fg-muted">{label}{term && <TermHint term={term} floating />}</dt>
      <dd className="font-tabular text-t14 font-semibold text-fg">{children}</dd>
    </div>
  );
}
