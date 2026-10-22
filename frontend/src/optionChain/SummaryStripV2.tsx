import type { ReactNode } from "react";
import { formatDateIST, formatNumber, formatPrice } from "../ui/format";
import type { GlossaryKey } from "../ui/glossary";
import { TermHint } from "../ui/TermHint";
import { daysText, formatIv, formatStrike } from "./model";
import type { ChainSummary } from "./types";

/**
 * OC1: the summary cards above the chain - spot (live), futures, ATM strike, PCR (OI), max pain, ATM IV and days to
 * expiry, every one from the server's summary (backend app/option_chain/chain_view.py). A phone shows them as a strip
 * that swipes sideways; from the md breakpoint they sit in one row. A value the broker did not give is "–".
 */
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
  const cards = summaryCards(summary, expiry);
  return (
    <section aria-label="Summary" data-block="chain-summary" data-tour="oc-summary"
      className="-mx-space-4 flex snap-x snap-mandatory gap-space-3 overflow-x-auto px-space-4 pb-space-1 md:mx-0 md:grid md:grid-cols-4 md:gap-space-3 xl:grid-cols-7 md:overflow-visible md:px-0">
      {cards.map((c) => (
        <div key={c.key} data-summary={c.key}
          className="min-w-[9.5rem] shrink-0 snap-start rounded-panel border border-subtle bg-surface-1 p-space-3 shadow-card md:min-w-0 md:p-space-4">
          <div className="flex items-center gap-space-1 text-t12 font-medium text-fg-muted">
            <span className="truncate">{c.label}</span>
            {c.term && <TermHint term={c.term} floating align={c.key === "dte" ? "end" : "start"} />}
          </div>
          <Value loading={loading}>{c.value}</Value>
          {/* The caption line keeps its height when empty, so every card is the same size. */}
          <div className="mt-space-1 truncate text-t12 text-fg-muted">{!loading && c.caption ? c.caption : " "}</div>
        </div>
      ))}
    </section>
  );
}
