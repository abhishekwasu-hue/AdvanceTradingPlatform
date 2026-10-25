import { Check, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { EMPTY, formatChange, formatPct, formatPrice, formatSignedINR } from "../ui/format";
import { useBigText } from "../theme";
import { TermHint } from "../ui/TermHint";
import { useColourV2 } from "../ui/colourV2";
import { Sparkline } from "../ui/Sparkline";
import { CARD, toneClass } from "./CardV2";
import { HERO_CARD } from "../ui/accents";
import { indexCaption, signTone, vixNote, type TodayPnl } from "./model";
import type { Loadable } from "./useDashboardData";
import type { MarketStrip, StripIndex } from "./types";

/**
 * UI2-2 (UI v2 proposal §5 block 3): the market strip - NIFTY 50, BANK NIFTY and INDIA VIX (last price, change, %
 * change) from GET /api/market/strip, one OI line per index (desktop), the market's state, the broker chip and today's
 * P&L with its booked / open split. A remembered level (market memory, before a broker login) says when it was taken;
 * no price at all is "–", never 0.
 * Colour v2 (flag `colour_v2`): each index carries a small line of its real 5-minute closes (`sparks`, from
 * indexSparks.ts; none without them) in the colour of its change, and today's P&L sits on a light tint of its sign;
 * on a phone the P&L leads the strip (CV-4).
 */

export interface MarketStripProps {
  strip: Loadable<MarketStrip>;
  oi: Record<string, string>;
  marketText: string | null;
  broker: { text: string; tone: "neutral" | "warn"; connected: boolean } | null;
  pnl: TodayPnl;
  nowMs: number;
  /** Colour v2: each index's closes in its last session, by key (left out or empty: no line). */
  sparks?: Record<string, number[]>;
}

function Change({ index }: { index: StripIndex }) {
  const tone = toneClass(signTone(index.change ?? index.change_pct));
  if (index.ltp === null) return <span className="text-t13 text-fg-muted">{EMPTY}</span>;
  return (
    <span className={`font-tabular text-t13 font-medium ${tone}`}>
      {formatChange(index.change)}{" "}
      <span className="block sm:inline">({formatPct(index.change_pct)})</span>
    </span>
  );
}

function IndexCell({ index, oi, nowMs, spark }: { index: StripIndex; oi?: string; nowMs: number; spark?: number[] }) {
  const caption = indexCaption(index, nowMs);
  const tone = signTone(index.change ?? index.change_pct);
  const note = index.key === "INDIA VIX" ? vixNote(index.ltp) : oi;
  return (
    <div className="min-w-0" data-index={index.key}>
      <div className="flex items-center gap-space-1 text-t12 font-medium uppercase text-fg-muted">
        <span className="truncate">{index.label}</span>
      </div>
      <div className="mt-space-1 font-tabular text-t18 font-semibold text-fg md:text-t20">{formatPrice(index.ltp)}</div>
      <Change index={index} />
      {spark && spark.length > 1 && (
        <Sparkline values={spark} tone={tone === "up" ? "up" : tone === "down" ? "down" : "neutral"} className="mt-space-1 h-7 w-full max-w-[8rem]" />
      )}
      {caption && <div className="mt-space-1 text-t12 text-fg-muted">{caption}</div>}
      {note && <div className="mt-space-1 hidden text-t12 text-fg-muted md:block">{note}</div>}
    </div>
  );
}

function StatusChip({ children, tone }: { children: ReactNode; tone: "neutral" | "warn" }) {
  return (
    <span className={`inline-flex min-h-7 items-center gap-space-1 rounded-pill px-space-3 text-t13 font-medium ${tone === "warn" ? "bg-warn-tint text-warn" : "bg-surface-2 text-fg"}`}>
      {children}
    </span>
  );
}

export default function MarketStripV2({ strip, oi, marketText, broker, pnl, nowMs, sparks }: MarketStripProps) {
  const data = strip.status === "ok" ? strip.data : null;
  const total = pnl.total;
  // Item 1: the one-row desktop strip only at the Normal text size; at Large / Extra large it stays stacked (indices,
  // then the chips, then the P&L), and on a phone the two indices share a row with VIX below.
  const big = useBigText();
  const L = (cls: string) => (big ? "" : cls);
  const cell = (n: number) => (n === 2 ? (big ? "col-span-2 sm:col-span-1" : "col-span-2 min-[380px]:col-span-1") : "");
  const colour = useColourV2();
  const sign = signTone(total);
  // Colour v2: the P&L on a light tint of its sign (none at zero or while unknown).
  const pnlTint = !colour || (sign !== "up" && sign !== "down") ? "" : `rounded-control px-space-3 py-space-2 ${sign === "up" ? "bg-up-tint" : "bg-down-tint"}${big ? "" : " xl:rounded-none xl:rounded-r-panel"}`;
  // CV-4 (owner, 24 Oct): on a phone the P&L comes first in the strip, above the indices and their sparklines, so it is
  // on screen when Today opens (390 x 844); from md up the order is as before.
  const pnlFirst = colour ? " max-md:order-first" : "";
  // UI-H CA2: the Today hero card - the strip - wears the blue -> violet gradient outline (src/ui/accents.ts).
  return (
    <section aria-label="Market" data-block="strip" data-tour="dashboard-strip" className={`${colour ? HERO_CARD : CARD} grid gap-space-4 p-space-4 md:p-space-5 ${L("xl:grid-cols-[repeat(3,minmax(0,1fr))_auto_auto] xl:gap-0 xl:p-0")}`}>
      {/* Item 1: on the narrowest phones (under 380 px; any phone at the larger text sizes) the two indices share a row
          and VIX takes the next one, so the prices stay large without spilling out of their cells. */}
      <div className={`grid grid-cols-2 gap-space-3 ${big ? "sm:grid-cols-3" : "min-[380px]:grid-cols-[minmax(0,1.2fr)_minmax(0,1.2fr)_minmax(0,0.8fr)] xl:contents"}`}>
        {data ? data.indices.map((i, n) => (
          <div key={i.key} className={`${L("xl:border-r xl:border-subtle xl:p-space-5")} ${cell(n)}`}>
            <IndexCell index={i} oi={oi[i.key]} nowMs={nowMs} spark={colour ? sparks?.[i.key] : undefined} />
          </div>
        )) : ["NIFTY 50", "BANK NIFTY", "INDIA VIX"].map((label, n) => (
          <div key={label} className={`min-w-0 ${L("xl:border-r xl:border-subtle xl:p-space-5")} ${cell(n)}`}>
            <div className="text-t12 font-medium uppercase text-fg-muted">{label}</div>
            {strip.status === "loading"
              ? <div className="mt-space-2 h-6 w-20 animate-pulse rounded-control bg-surface-2" />
              : <div className="mt-space-1 font-tabular text-t18 font-semibold text-fg md:text-t20">{EMPTY}</div>}
          </div>
        ))}
      </div>
      {strip.status === "error" && <p className={`text-t13 text-fg-muted ${L("xl:order-last xl:col-span-5 xl:border-t xl:border-subtle xl:px-space-5 xl:py-space-3")}`}>We could not load the market prices. We will try again in a moment.</p>}
      <div className={`flex flex-wrap items-center gap-space-2 border-t border-subtle pt-space-4 ${L("xl:flex-col xl:items-stretch xl:justify-center xl:border-r xl:border-t-0 xl:p-space-5")}`}>
        {marketText && <StatusChip tone="neutral"><span aria-hidden className="h-1.5 w-1.5 rounded-pill bg-fg" />{marketText}</StatusChip>}
        {broker && (
          <StatusChip tone={broker.tone}>
            {broker.tone === "warn" ? <TriangleAlert size={14} aria-hidden /> : <Check size={14} aria-hidden />}
            {broker.text}
          </StatusChip>
        )}
      </div>
      <div data-pnl data-pnl-tint={pnlTint ? sign : undefined} className={`flex items-center justify-between gap-space-3 ${L("xl:flex-col xl:items-end xl:justify-center xl:p-space-5 xl:text-right")}${pnlTint ? ` ${pnlTint}` : ""}${pnlFirst}`}>
        <div className={`flex shrink-0 items-center gap-space-1 whitespace-nowrap text-t14 text-fg-muted ${L("xl:text-t13")}`}>
          Today&apos;s P&amp;L <TermHint term="todays-pnl" align="end" />
        </div>
        <div className="text-right">
          <div className={`font-tabular text-t20 font-semibold md:text-t24 ${toneClass(signTone(total))}`}>{formatSignedINR(total)}</div>
          {pnl.booked !== null && (
            <div className="text-t12 text-fg-muted">
              <span className="whitespace-nowrap">{formatSignedINR(pnl.booked)} booked</span> ·{" "}
              <span className="whitespace-nowrap">{pnl.open === null ? `open ${EMPTY}` : `${formatSignedINR(pnl.open)} open`}</span>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
