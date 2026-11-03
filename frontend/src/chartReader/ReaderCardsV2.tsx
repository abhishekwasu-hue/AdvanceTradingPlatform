import { X } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "../components/primitives";
import { guardDeep, guardText } from "../chartAnalyze/signalLanguage";
import { Chip, type ChipTone } from "../ui/Chip";
import { HelpPopover } from "../ui/HelpPopover";
import { formatNumber, formatPrice } from "../ui/format";
import { TrendChip } from "../ui/TrendChip";
import type { AbhiRead, BookRead, ConditionStatus, ReaderResult, ReaderStrategy, Tri } from "./types";

/**
 * M86 W3: the chart reader's cards in the Analyze-a-chart panel (docs/design/M86_PLAN.md "UI (W3)"). "Abhi's method"
 * is the default card; every book-strategy read is its OWN card, titled after its book, never merged into Abhi's card
 * and never a filter or gate. The cards show only what the API returns: numbers and chips first, the evidence behind
 * (?). Every text is guarded on render (no call words), as the result screen does.
 */

export type ReadState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "done"; result: ReaderResult }
  | { phase: "failed"; message: string };

export const card = "rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5";

export const LOADING_TEXT = "Reading the chart… this can take up to a minute.";
export const REFUSED_TEXT = "The AI declined to read this chart.";
export const ERROR_TEXT = "The read did not finish. Please try again.";
export const TWO_CHART_NOTE = "Some conditions of this strategy need a second chart, which was not available for this read.";

/** A cost in US dollars: "$0.07"; under a cent "< $0.01"; nothing spent "$0.00". */
export function usd(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v) || v <= 0) return "$0.00";
  if (v < 0.01) return "< $0.01";
  return `$${formatNumber(v, 2)}`;
}

/** "est. $0.07 per read". */
export function estimateText(v: number | null | undefined): string {
  return `est. ${usd(v)} per read`;
}

/** The book card's title: "<author>, <book> (<edition>) strategy"; the strategy's name when no book is given. */
export function bookTitle(s: Pick<ReaderStrategy, "book" | "author" | "edition" | "name"> | undefined, fallback = "Book"): string {
  if (!s) return `${fallback} strategy`;
  const book = s.book?.trim();
  if (!book) return `${s.name || fallback} strategy`;
  const author = s.author?.trim();
  const withAuthor = author && !book.toLowerCase().includes(author.toLowerCase()) ? `${author}, ${book}` : book;
  const edition = s.edition?.trim();
  return `${withAuthor}${edition && !withAuthor.includes(edition) ? ` (${edition})` : ""} strategy`;
}

export function range(low: number | null, high: number | null): string {
  if (low == null && high == null) return "Not read";
  if (low == null || high == null || low === high) return formatPrice(low ?? high);
  return `${formatPrice(Math.min(low, high))} – ${formatPrice(Math.max(low, high))}`;
}

const TRI_WORD: Record<Tri, string> = { yes: "Yes", no: "No", unclear: "Unclear" };
const TRI_MARK: Record<Tri, { tone: ChipTone; mark: string }> = {
  yes: { tone: "up", mark: "✓" }, no: { tone: "down", mark: "✕" }, unclear: { tone: "neutral", mark: "◆" },
};
const STATUS_WORD: Record<ConditionStatus, string> = { met: "Met", not_met: "Not met", unclear: "Unclear" };
const STATUS_MARK: Record<ConditionStatus, { tone: ChipTone; mark: string }> = {
  met: { tone: "up", mark: "✓" }, not_met: { tone: "down", mark: "✕" }, unclear: { tone: "neutral", mark: "◆" },
};

function TriChip({ value, name, word }: { value: Tri; name: string; word?: string }) {
  const m = TRI_MARK[value] ?? TRI_MARK.unclear;
  return <Chip tone={m.tone} variant="meaning" icon={m.mark} name={`${name}-${value}`}>{word ?? TRI_WORD[value] ?? "Unclear"}</Chip>;
}

function YesNoChip({ value, yes, no, name }: { value: boolean; yes: string; no: string; name: string }) {
  return <TriChip value={value ? "yes" : "no"} name={name} word={value ? yes : no} />;
}

/** One (?) with the evidence, when there is any. */
function Evidence({ title, text }: { title: string; text: string | null | undefined }) {
  if (!text?.trim()) return null;
  return <HelpPopover title={title}><p>{guardText(text)}</p></HelpPopover>;
}

/** A labelled row: the label on the left (muted), the value (chips, numbers) beside it; wraps on a phone. */
function Row({ label, children, testid }: { label: string; children: ReactNode; testid?: string }) {
  return (
    <div className="flex min-h-11 flex-wrap items-center gap-x-space-3 gap-y-space-1 py-space-1" data-row={testid}>
      <span className="w-full text-t12 font-medium text-fg-muted sm:w-40 sm:shrink-0">{label}</span>
      <span className="flex min-w-0 flex-wrap items-center gap-space-2">{children}</span>
    </div>
  );
}

function Sentence({ label, text, testid }: { label: string; text: string | null | undefined; testid: string }) {
  if (!text?.trim()) return null;
  return (
    <div className="py-space-1" data-row={testid}>
      <p className="text-t12 font-medium text-fg-muted">{label}</p>
      <p className="mt-space-1 break-words text-t14 text-fg">{guardText(text)}</p>
    </div>
  );
}

function CannotRead({ text }: { text: string | null | undefined }) {
  if (!text?.trim()) return null;
  return (
    <p className="mt-space-2 rounded-control bg-warn-tint p-space-3 text-t14 text-fg" data-testid="reader-cannot-read">
      <span className="font-semibold">Could not read: </span>{guardText(text)}
    </p>
  );
}

/** The header chips: label (AI only / Advanced analysis (code+AI)), cached, and the symbol read. */
function ResultChips({ result, symbol }: { result: ReaderResult; symbol?: string }) {
  return (
    <>
      {symbol ? <Chip name="reader-symbol">{symbol}</Chip> : null}
      <Chip tone="ai" name="reader-label">{guardText(result.label)}</Chip>
      {result.cached ? <Chip tone="info" name="reader-cached">Cached</Chip> : null}
    </>
  );
}

/** The cost after a read (a cached read costs nothing) and the cleaning counts behind (?). */
function CostLine({ result }: { result: ReaderResult }) {
  const removed = result.removed?.length ?? 0;
  const flagged = result.time_flags?.length ?? 0;
  return (
    <div className="mt-space-3 flex flex-wrap items-center gap-space-2 border-t border-subtle pt-space-2 text-t12 text-fg-muted" data-testid="reader-cost">
      <span>Cost <span className="font-tabular text-fg">{usd(result.cached ? 0 : result.cost_usd)}</span>{result.cached ? " · cached" : ""}</span>
      <HelpPopover title="About this read">
        <p data-testid="reader-about">Sentences removed: <span className="font-tabular">{removed}</span> · Time words flagged: <span className="font-tabular">{flagged}</span></p>
        <p>Sentences that read as advice are removed before the read is shown; time words are flagged.</p>
      </HelpPopover>
    </div>
  );
}

/** Loading, refused, error and failed (the call itself) in plain words; null for a good read. */
function NotOk({ state, onRetry }: { state: ReadState; onRetry?: () => void }) {
  if (state.phase === "loading") return <p className="mt-space-3 text-t14 text-fg-muted" role="status" data-testid="reader-loading">{LOADING_TEXT}</p>;
  let headline: string | null = null;
  let detail: string | null = null;
  if (state.phase === "failed") { headline = ERROR_TEXT; detail = state.message; }
  if (state.phase === "done" && state.result.status !== "ok") {
    headline = state.result.status === "refused" ? REFUSED_TEXT : ERROR_TEXT;
    detail = state.result.error ? guardText(state.result.error) : null;
  }
  if (state.phase === "done" && state.result.status === "ok" && !state.result.read) headline = ERROR_TEXT;
  if (!headline) return null;
  const refused = state.phase === "done" && state.result.status === "refused";
  return (
    <div role="alert" className="mt-space-3 rounded-control bg-warn-tint p-space-3 text-t14 text-fg" data-testid={refused ? "reader-refused" : "reader-error"}>
      <p className="font-semibold">{headline}</p>
      {detail && detail !== headline ? <p className="mt-space-1 text-t12 text-fg-muted">{detail}</p> : null}
      {onRetry ? <Button className="mt-space-2 h-11 md:h-9" onClick={onRetry}>Try again</Button> : null}
    </div>
  );
}

function okRead<R>(state: ReadState): { result: ReaderResult; read: R } | null {
  if (state.phase !== "done" || state.result.status !== "ok" || !state.result.read) return null;
  const result = guardDeep(state.result);
  return { result, read: result.read as R };
}

// --- Abhi's method -----------------------------------------------------------------------------------------------------

const TREND_KEY = { up: "UP", down: "DOWN", sideways: "SIDEWAYS" } as const;

export function AbhiReadBody({ read }: { read: AbhiRead }) {
  const dir = String(read.trend?.direction ?? "unclear").toLowerCase();
  const trend = TREND_KEY[dir as keyof typeof TREND_KEY];
  return (
    <div className="mt-space-3 divide-y divide-subtle" data-testid="abhi-read">
      <Row label="Trend" testid="trend">
        {trend ? <TrendChip trend={trend} /> : <Chip variant="meaning" icon="◆" name="trend-unclear">Unclear</Chip>}
        <Evidence title="Trend" text={read.trend?.evidence} />
      </Row>
      <Row label="Areas" testid="areas">
        {read.areas?.length ? (
          <ul className="flex w-full flex-col gap-space-1">
            {read.areas.map((a, i) => (
              <li key={i} className="flex min-h-11 flex-wrap items-center gap-space-2" data-area={a.side}>
                <Chip tone={a.side === "buyer" ? "up" : "down"} variant="meaning" icon={a.side === "buyer" ? "▲" : "▼"} name={`area-${a.side}`}>
                  {a.side === "buyer" ? "Buyer area" : "Seller area"}
                </Chip>
                <span className="font-tabular text-t14 font-semibold text-fg">{range(a.price_low, a.price_high)}</span>
                {a.chart ? <span className="text-t12 text-fg-muted">{a.chart}</span> : null}
                <Evidence title={a.side === "buyer" ? "Buyer area" : "Seller area"} text={a.evidence} />
              </li>
            ))}
          </ul>
        ) : <span className="text-t14 text-fg-muted">None read</span>}
      </Row>
      <Row label="Correction" testid="correction">
        <YesNoChip value={Boolean(read.correction?.present)} yes="Present" no="Not present" name="correction" />
        <span className="text-t12 text-fg-muted">Momentum weakening</span>
        <TriChip value={read.correction?.momentum_weakening ?? "unclear"} name="momentum" />
        <Evidence title="Correction" text={read.correction?.evidence} />
      </Row>
      <Row label="Commitment candle" testid="commitment">
        <YesNoChip value={Boolean(read.commitment_candle?.present)} yes="Seen" no="Not seen" name="commitment" />
        <Evidence title="Commitment candle" text={read.commitment_candle?.evidence} />
      </Row>
      <Sentence label="Area of interest" text={read.area_of_interest_statement} testid="area-of-interest" />
      <Sentence label="Watch next" text={read.watch_next} testid="watch-next" />
      <CannotRead text={read.cannot_read} />
    </div>
  );
}

export function AbhiCard({ state, estimateUsd, symbol, onRead, disabled, disabledReason }: {
  state: ReadState; estimateUsd: number | null; symbol?: string; onRead: () => void; disabled?: boolean; disabledReason?: string;
}) {
  const ok = okRead<AbhiRead>(state);
  const loading = state.phase === "loading";
  return (
    <section className={card} aria-labelledby="reader-abhi-title" data-card="chart-reader-abhi" data-testid="reader-abhi">
      <div className="flex flex-wrap items-center gap-space-2">
        <h3 id="reader-abhi-title" className="mr-auto text-t15 font-semibold text-fg">Abhi&apos;s method</h3>
        {state.phase === "done" ? <ResultChips result={state.result} symbol={symbol} /> : null}
      </div>
      {ok ? <AbhiReadBody read={ok.read} /> : <NotOk state={state} onRetry={state.phase === "idle" || loading ? undefined : onRead} />}
      {state.phase === "done" ? <CostLine result={guardDeep(state.result)} /> : null}
      {(state.phase === "idle" || ok) && (
        <div className="mt-space-3 flex flex-col gap-space-2 sm:flex-row sm:items-center">
          <Button variant="primary" className="h-11 w-full sm:w-auto md:h-9" disabled={disabled} reason={disabledReason} onClick={onRead} data-testid="reader-abhi-read">
            {ok ? "Read again" : "Read with Abhi's method"}
          </Button>
          <span className="font-tabular text-t12 text-fg-muted" data-testid="reader-estimate">{estimateText(estimateUsd)}</span>
        </div>
      )}
    </section>
  );
}

// --- a book strategy ---------------------------------------------------------------------------------------------------

export function BookReadBody({ result, read }: { result: ReaderResult; read: BookRead }) {
  const counts = { met: 0, not_met: 0, unclear: 0 } as Record<ConditionStatus, number>;
  for (const c of read.conditions ?? []) counts[c.status in counts ? c.status : "unclear"] += 1;
  return (
    <div className="mt-space-3 divide-y divide-subtle" data-testid="book-read">
      <Row label="Side read" testid="side">
        <Chip variant="meaning" name={`side-${read.side_scored}`}>{read.side_scored === "short" ? "Short side" : "Long side"}</Chip>
      </Row>
      <Row label="Conditions" testid="conditions">
        <span className="font-tabular text-t14 text-fg" data-testid="condition-counts">
          {counts.met} met · {counts.not_met} not met · {counts.unclear} unclear
        </span>
        <ul className="flex w-full flex-col gap-space-1">
          {(read.conditions ?? []).map((c) => {
            const m = STATUS_MARK[c.status] ?? STATUS_MARK.unclear;
            return (
              <li key={c.id} className="flex min-h-11 flex-wrap items-center gap-space-2" data-condition={c.status}>
                <Chip tone={m.tone} variant="meaning" icon={m.mark} name={`condition-${c.status}`}>{STATUS_WORD[c.status] ?? "Unclear"}</Chip>
                <span className="min-w-0 break-all font-mono text-t12 text-fg">{c.id}</span>
                <Evidence title={c.id} text={c.evidence} />
              </li>
            );
          })}
        </ul>
      </Row>
      <Row label="Trigger seen" testid="trigger">
        <TriChip value={read.trigger_seen?.status ?? "unclear"} name="trigger" />
        {read.trigger_seen?.bar != null ? <span className="font-tabular text-t14 text-fg">Bar {read.trigger_seen.bar}</span> : null}
        <Evidence title="Trigger" text={read.trigger_seen?.evidence} />
      </Row>
      <Row label="Invalidated" testid="invalidated">
        <TriChip value={read.invalidated?.status ?? "unclear"} name="invalidated" />
        <Evidence title="Invalidated" text={read.invalidated?.evidence} />
      </Row>
      {read.levels?.length ? (
        <Row label="Levels" testid="levels">
          <ul className="flex w-full flex-col gap-space-1">
            {read.levels.map((l, i) => (
              <li key={i} className="flex min-h-11 flex-wrap items-center gap-space-2">
                <span className="font-tabular text-t14 font-semibold text-fg">{range(l.price_low, l.price_high)}</span>
                <span className="text-t12 text-fg-muted">{l.kind}{l.chart ? ` · ${l.chart}` : ""}</span>
                <Evidence title={l.kind || "Level"} text={l.evidence} />
              </li>
            ))}
          </ul>
        </Row>
      ) : null}
      <Sentence label="Statement" text={read.statement} testid="statement" />
      {result.second_chart_missing ? (
        <p className="mt-space-2 rounded-control bg-info-tint p-space-3 text-t14 text-fg" data-testid="reader-two-chart-note">{TWO_CHART_NOTE}</p>
      ) : null}
      <CannotRead text={read.cannot_read} />
    </div>
  );
}

export function BookCard({ title, state, symbol, onClose, onRetry }: {
  title: string; state: ReadState; symbol?: string; onClose: () => void; onRetry: () => void;
}) {
  const ok = okRead<BookRead>(state);
  return (
    <section className={card} aria-label={title} data-card="chart-reader-book" data-testid="reader-book">
      <div className="flex items-start gap-space-2">
        <h3 className="min-w-0 flex-1 break-words pt-space-2 text-t15 font-semibold text-fg">{title}</h3>
        <button type="button" onClick={onClose} aria-label={`Close ${title}`}
          className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          <X size={18} aria-hidden />
        </button>
      </div>
      {state.phase === "done" ? <div className="mt-space-1 flex flex-wrap items-center gap-space-2"><ResultChips result={state.result} symbol={symbol} /></div> : null}
      {ok ? <BookReadBody result={ok.result} read={ok.read} /> : <NotOk state={state} onRetry={state.phase === "loading" ? undefined : onRetry} />}
      {state.phase === "done" ? <CostLine result={guardDeep(state.result)} /> : null}
    </section>
  );
}
