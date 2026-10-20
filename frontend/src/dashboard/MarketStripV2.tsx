import { Check, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { EMPTY, formatChange, formatPct, formatPrice, formatSignedINR } from "../ui/format";
import { TermHint } from "../ui/TermHint";
import { CARD, toneClass } from "./CardV2";
import { indexCaption, signTone, vixNote, type TodayPnl } from "./model";
import type { Loadable } from "./useDashboardData";
import type { MarketStrip, StripIndex } from "./types";

/**
 * UI2-2 (UI v2 proposal §5 block 3): the market strip - NIFTY 50, BANK NIFTY and INDIA VIX (last price, change, %
 * change) from GET /api/market/strip, one OI line per index (desktop), the market's state, the broker chip and today's
 * P&L with its booked / open split. A remembered level (market memory, before a broker login) says when it was taken;
 * no price at all is "–", never 0.
 */

export interface MarketStripProps {
  strip: Loadable<MarketStrip>;
  oi: Record<string, string>;
  marketText: string | null;
  broker: { text: string; tone: "neutral" | "warn"; connected: boolean } | null;
  pnl: TodayPnl;
  nowMs: number;
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

function IndexCell({ index, oi, nowMs }: { index: StripIndex; oi?: string; nowMs: number }) {
  const caption = indexCaption(index, nowMs);
  const note = index.key === "INDIA VIX" ? vixNote(index.ltp) : oi;
  return (
    <div className="min-w-0" data-index={index.key}>
      <div className="flex items-center gap-space-1 text-t12 font-medium uppercase text-fg-muted">
        <span className="truncate">{index.label}</span>
      </div>
      <div className="mt-space-1 font-tabular text-t18 font-semibold text-fg md:text-t20">{formatPrice(index.ltp)}</div>
      <Change index={index} />
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

export default function MarketStripV2({ strip, oi, marketText, broker, pnl, nowMs }: MarketStripProps) {
  const data = strip.status === "ok" ? strip.data : null;
  const total = pnl.total;
  return (
    <section aria-label="Market" data-block="strip" className={`${CARD} grid gap-space-4 p-space-4 md:p-space-5 xl:grid-cols-[repeat(3,minmax(0,1fr))_auto_auto] xl:gap-0 xl:p-0`}>
      <div className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1.2fr)_minmax(0,0.8fr)] gap-space-3 xl:contents">
        {data ? data.indices.map((i) => (
          <div key={i.key} className="xl:border-r xl:border-subtle xl:p-space-5">
            <IndexCell index={i} oi={oi[i.key]} nowMs={nowMs} />
          </div>
        )) : ["NIFTY 50", "BANK NIFTY", "INDIA VIX"].map((label) => (
          <div key={label} className="min-w-0 xl:border-r xl:border-subtle xl:p-space-5">
            <div className="text-t12 font-medium uppercase text-fg-muted">{label}</div>
            {strip.status === "loading"
              ? <div className="mt-space-2 h-6 w-20 animate-pulse rounded-control bg-surface-2" />
              : <div className="mt-space-1 font-tabular text-t18 font-semibold text-fg md:text-t20">{EMPTY}</div>}
          </div>
        ))}
      </div>
      {strip.status === "error" && <p className="text-t13 text-fg-muted xl:order-last xl:col-span-5 xl:border-t xl:border-subtle xl:px-space-5 xl:py-space-3">We could not load the market prices. We will try again in a moment.</p>}
      <div className="flex flex-wrap items-center gap-space-2 border-t border-subtle pt-space-4 xl:flex-col xl:items-stretch xl:justify-center xl:border-r xl:border-t-0 xl:p-space-5">
        {marketText && <StatusChip tone="neutral"><span aria-hidden className="h-1.5 w-1.5 rounded-pill bg-fg" />{marketText}</StatusChip>}
        {broker && (
          <StatusChip tone={broker.tone}>
            {broker.tone === "warn" ? <TriangleAlert size={14} aria-hidden /> : <Check size={14} aria-hidden />}
            {broker.text}
          </StatusChip>
        )}
      </div>
      <div className="flex items-center justify-between gap-space-3 xl:flex-col xl:items-end xl:justify-center xl:p-space-5 xl:text-right">
        <div className="flex shrink-0 items-center gap-space-1 whitespace-nowrap text-t14 text-fg-muted xl:text-t13">
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
