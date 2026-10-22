import { useState, type ReactNode } from "react";
import { formatDateIST, formatNumber, formatPrice } from "../ui/format";
import type { GlossaryKey } from "../ui/glossary";
import { TermHint } from "../ui/TermHint";
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

interface Card { key: string; label: string; term?: GlossaryKey; value: string; caption?: string }

export function summaryCards(s: ChainSummary | null, expiry: string | null): Card[] {
  const future = s?.futures ?? null;
  return [
    { key: "spot", label: "Spot", value: formatPrice(s?.spot) },
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

function Value({ loading, children }: { loading: boolean; children: ReactNode }) {
  if (loading) return <div className="mt-space-2 h-6 w-20 animate-pulse rounded-control bg-surface-2" />;
  return <div className="mt-space-1 font-tabular text-t18 font-semibold text-fg md:text-t20">{children}</div>;
}

export default function SummaryStripV2({ summary, expiry, loading }: { summary: ChainSummary | null; expiry: string | null; loading: boolean }) {
  const all = summaryCards(summary, expiry);
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
          <Value loading={loading}>{c.value}</Value>
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
