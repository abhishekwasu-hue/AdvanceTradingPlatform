import { useState, type ReactNode } from "react";
import type { TerminalSpot } from "../terminal/spot";
import { formatDateIST, formatNumber, formatPrice, formatTimeIST } from "../ui/format";
import type { GlossaryKey } from "../ui/glossary";
import { TermHint } from "../ui/TermHint";
import { flashClass, useTickFlash } from "../ui/useTickFlash";
import { daysText, formatIv, formatStrike } from "./model";
import type { ChainSummary } from "./types";

/**
 * OC1: the summary cards above the chain - spot (live), futures, ATM strike, PCR (OI), max pain, ATM IV and days to
 * expiry, every one from the server's summary (backend app/option_chain/chain_view.py). From the md breakpoint they sit
 * in one row. A value the broker did not give is "–".
 *
 * UX3 (UX audit P05, U9 "no sideways scrolling"): a phone shows the four a trader reads first (PHONE_FIRST: spot, ATM,
 * PCR, max pain) as two rows of two, and the other three under "More" - nothing scrolls sideways any more.
 */
/** From md the cards keep the server's order (literal classes, so the stylesheet has them). */
const DESKTOP_ORDER = ["md:order-1", "md:order-2", "md:order-3", "md:order-4", "md:order-5", "md:order-6", "md:order-7"] as const;

/** The cards a phone shows before "More". */
export const PHONE_FIRST: ReadonlySet<string> = new Set(["spot", "atm", "pcr", "max-pain"]);

interface Card { key: string; label: string; term?: GlossaryKey; value: string; caption?: string; /** UX-7: the number behind `value`, for the tick flash. */ num?: number | null }

/**
 * Owner 25 Oct (T1-3 on the Option chain page): with a live price (the tick of his own connection, else the chart's
 * LTP read - src/terminal/spot.ts) the spot card shows THAT value and its time, the same as the chart; only without one
 * does the chain's own spot (or the last close) stand.
 */
export function summaryCards(s: ChainSummary | null, expiry: string | null, spot: TerminalSpot | null = null): Card[] {
  const future = s?.futures ?? null;
  const live = spot && spot.source !== "chain" ? spot : null;
  return [
    // B2b: a closed market's spot from our own last index close says so.
    live
      ? { key: "spot", label: "Spot", value: formatPrice(live.ltp), num: live.ltp, caption: live.at ? `as of ${formatTimeIST(live.at)}` : undefined }
      : { key: "spot", label: s?.spot_source === "last_close" ? "Spot: last close" : "Spot", value: formatPrice(s?.spot), num: s?.spot ?? null },
    { key: "futures", label: "Futures", value: formatPrice(future?.ltp),
      caption: future ? `Expires ${formatDateIST(future.expiry).replace(/ \d{4}$/, "")}` : undefined },
    { key: "atm", label: "ATM strike", term: "atm", value: formatStrike(s?.atm_strike) },
    { key: "pcr", label: "PCR (OI)", term: "pcr", value: formatNumber(s?.pcr_oi, 2) },
    { key: "max-pain", label: "Max pain", term: "max-pain", value: formatStrike(s?.max_pain) },
    { key: "atm-iv", label: "ATM IV", term: "iv", value: formatIv(s?.atm_iv) },
    { key: "dte", label: "Days to expiry", term: "expiry", value: daysText(s?.days_to_expiry),
      caption: expiry ? formatDateIST(expiry) : undefined },
  ];
}

function Value({ loading, num, children }: { loading: boolean; num?: number | null; children: ReactNode }) {
  // UX-7: the spot card's number shows a short up / down tint when the price changes.
  const flash = useTickFlash(loading ? null : num);
  if (loading) return <div className="mt-space-2 h-6 w-20 animate-pulse rounded-control bg-surface-2" />;
  return <div data-flash={flash ?? undefined} className={`-mx-space-1 mt-space-1 rounded-control px-space-1 font-tabular text-t18 font-semibold text-fg transition-colors duration-base md:text-t20 ${flashClass(flash)}`}>{children}</div>;
}

export default function SummaryStripV2({ summary, expiry, loading, spot = null }: {
  summary: ChainSummary | null; expiry: string | null; loading: boolean;
  /** The page's one spot (the chart's too, T1-3); without it the chain's own. */
  spot?: TerminalSpot | null;
}) {
  const all = summaryCards(summary, expiry, spot);
  const [more, setMore] = useState(false);
  // Phone order: the four first cards, then the rest; from md the server's order (one row).
  const cards = [...all.filter((c) => PHONE_FIRST.has(c.key)), ...all.filter((c) => !PHONE_FIRST.has(c.key))];
  return (
    <section aria-label="Summary" data-block="chain-summary" data-tour="oc-summary"
      className="grid grid-cols-2 gap-space-3 md:grid-cols-4 xl:grid-cols-7">
      {cards.map((c) => (
        <div key={c.key} data-summary={c.key}
          className={`min-w-0 rounded-panel border border-subtle bg-surface-1 p-space-3 shadow-card md:p-space-4 ${DESKTOP_ORDER[all.indexOf(c)] ?? ""} ${PHONE_FIRST.has(c.key) || more ? "" : "hidden md:block"}`}>
          <div className="flex items-center gap-space-1 text-t12 font-medium text-fg-muted">
            <span className="truncate">{c.label}</span>
            {c.term && <TermHint term={c.term} floating align={c.key === "dte" ? "end" : "start"} />}
          </div>
          <Value loading={loading} num={c.num}>{c.value}</Value>
          {/* The caption line keeps its height when empty, so every card is the same size. */}
          <div className="mt-space-1 truncate text-t12 text-fg-muted">{!loading && c.caption ? c.caption : " "}</div>
        </div>
      ))}
      <button type="button" onClick={() => setMore((m) => !m)} aria-expanded={more} data-summary-more
        className="order-last col-span-2 inline-flex min-h-11 items-center justify-center rounded-control text-t14 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:hidden">
        {more ? "Show less" : "More: futures, IV, days to expiry"}
      </button>
    </section>
  );
}
