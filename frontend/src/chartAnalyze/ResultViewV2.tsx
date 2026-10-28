import { Bell, Check, Download, FileDown, Share2, Trash2, X } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "../components/primitives";
import { CANNOT_ALERT, alertCondition } from "./alerts";
import AnnotatedChartV2, { StructureSketch } from "./AnnotatedChartV2";
import type { AlertTarget } from "./AlertDialogV2";
import { PRINT_HIDE } from "./printResult";
import { guardResult } from "./signalLanguage";
import StrikesSectionV2, { type StrikesState } from "./StrikesSectionV2";
import type { AnalysisResult, PriceRef } from "./types";

/**
 * Analyze a chart: the result screen (owner brief, blocks 1-8). "What the method sees and the conditions" - educational,
 * never a buy / sell call; there is no order button anywhere on it, and the disclaimer closes every result.
 * Image only: the label sits at the top, and every price carries "≈" (read from the picture).
 * Every text the engine wrote passes the signal-language check (signalLanguage.ts) before it is shown. The option
 * strikes block shows the user's own settings only, flagged "From your settings". PDF: the browser's print dialog.
 * `shared` (PR-A4): the structure-only copy a share link opens - no name, no price, no date, no picture (the drawing over
 * a plain background), and no buttons; the server has already removed all of it (backend share.py).
 */

const card = "rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5";

function Section({ title, children, id }: { title: string; children: ReactNode; id: string }) {
  return (
    <section aria-labelledby={id} className={card} data-block={id}>
      <h3 id={id} className="mb-space-3 text-t15 font-semibold text-fg">{title}</h3>
      {children}
    </section>
  );
}

export function price(value: number | null | undefined, approximate: boolean): string {
  if (value == null) return "";
  const text = value.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  return approximate ? `≈ ${text}` : text;
}

function band(low: number | null, high: number | null, approximate: boolean): string {
  if (low == null && high == null) return "-";
  if (low == null || high == null || low === high) return price(low ?? high, approximate);
  return `${price(low, approximate)} – ${price(high, false)}`;
}

function Ref({ label, value, approximate }: { label: string; value: PriceRef; approximate: boolean }) {
  if (!value.text && value.price == null) return null;
  return (
    <div className="flex flex-col gap-space-1 border-t border-subtle py-space-2 first:border-t-0 sm:flex-row sm:gap-space-3">
      <dt className="w-32 shrink-0 text-t12 font-medium text-fg-muted">{label}</dt>
      <dd className="text-t14 text-fg">{value.text}{value.price != null && <span className="ml-space-2 font-tabular text-fg-muted">{price(value.price, approximate)}</span>}</dd>
    </div>
  );
}

const SIDE_TEXT = { bull_put: "Bull put side", bear_call: "Bear call side", none: "No side" } as const;
const TREND_TEXT: Record<string, string> = { up: "Up", down: "Down", sideways: "Sideways", unclear: "Unclear" };
const PHASE_TEXT: Record<string, string> = { pullback: "Pullback in progress", handover: "Handover", commitment: "Commitment", trending: "Trending",
  range: "Range", unclear: "Unclear" };

export function statusText(r: AnalysisResult): string {
  if (r.setup.status === "formed") return "Formed";
  if (r.setup.status === "forming") return `Forming — waiting for ${r.setup.waiting_for ?? "the condition below"}`;
  return "No setup";
}

export const SHARED_NOTE = "Shared by a member of your organisation. The chart's name, its prices, its dates and the picture are left out: this copy shows the structure only.";

export const FALLBACK_DISCLAIMER = "Educational only. This is not investment advice or a recommendation to buy or sell, and ATP places no order from it.";

export interface ResultViewProps {
  result: AnalysisResult;
  /** The item's own label and disclaimer: shown when the result lacks them, so they are always on screen. */
  itemLabel?: string | null;
  itemDisclaimer?: string | null;
  /** Set when the stored image is gone (retention): said in place of the drawing. */
  imageNote?: string | null;
  imageSrc: string | null;
  onAlert?: (target: AlertTarget) => void;
  onExport?: () => void;
  onDelete?: () => void;
  /** "Share with my organisation" (flag chart_analyze_share); `shareLive` marks a live link. */
  onShare?: () => void;
  shareLive?: boolean;
  /** The structure-only copy a share link opens: no buttons, the drawing without the picture. */
  shared?: boolean;
  /** "Download PDF": the browser's print dialog on the result block (printResult.ts). */
  onPdf?: () => void;
  /** The "Option strikes · from your settings" block (null: not shown). */
  strikes?: StrikesState | null;
  onStrikesSymbol?: (symbol: string) => void;
  exporting?: boolean;
  deleting?: boolean;
}

const noop = () => undefined;

export default function ResultViewV2({ result: raw, itemLabel, itemDisclaimer, imageNote, imageSrc, onAlert = noop, onExport = noop, onDelete = noop,
  onShare, shareLive, shared = false, onPdf, strikes, onStrikesSymbol, exporting, deleting }: ResultViewProps) {
  const result = guardResult(raw);
  const approx = result.approximate_prices;
  const label = result.label || itemLabel || null;
  const disclaimer = result.disclaimer || itemDisclaimer || FALLBACK_DISCLAIMER;
  const rec = result.recognised;
  const areas = result.annotations.areas.filter((a) => a.price_low != null || a.price_high != null);
  const firstArea = areas[0] ?? null;
  const waitCondition = alertCondition(result.what_to_wait_for?.alert);
  const title = shared ? "The reading" : [rec.symbol, rec.exchange, rec.timeframe].filter(Boolean).join(" · ") || "Your chart";
  const areaTarget = (low: number | null, high: number | null, name: string): AlertTarget =>
    ({ title: `Alert on ${name}`, symbol: rec.symbol, timeframe: rec.timeframe, band: { low, high }, approximate: approx });

  return (
    <div className="space-y-space-4" data-testid="analysis-result" data-setup={result.setup.status} data-shared={shared || undefined}>
      <div className={card}>
        {label && !shared && <p className="mb-space-2 inline-flex rounded-pill bg-warn-tint px-space-3 py-space-1 text-t12 font-medium text-fg" data-testid="image-only-label">{label}</p>}
        <h2 className="text-t20 font-semibold text-fg md:text-t24">{title}</h2>
        {(rec.first_bar || rec.last_bar) && <p className="mt-space-1 text-t12 text-fg-muted">Visible range: {rec.first_bar ?? "?"} to {rec.last_bar ?? "?"}</p>}
        {/* UI-28 U1.8: no sentence under the title - the card's one disclaimer is its footer line. */}
        {shared && <p className="mt-space-2 text-t14 text-fg" data-testid="shared-note" role="note">{result.shared_note ?? SHARED_NOTE}</p>}
        {!shared && <div className="mt-space-3 flex flex-col gap-space-2 sm:flex-row sm:flex-wrap" {...{ [PRINT_HIDE]: "" }}>
          <Button className="h-11 md:h-9" icon={<Bell size={16} aria-hidden />} disabled={!firstArea}
            onClick={() => firstArea && onAlert(areaTarget(firstArea.price_low, firstArea.price_high, `${firstArea.side} area`))}>
            Set an alert on this area
          </Button>
          <Button className="h-11 md:h-9" icon={<Download size={16} aria-hidden />} loading={exporting} onClick={onExport}>Download / share the result image</Button>
          {onPdf && <Button className="h-11 md:h-9" icon={<FileDown size={16} aria-hidden />} onClick={onPdf}>Download PDF</Button>}
          {onShare && (
            <Button className="h-11 md:h-9" icon={<Share2 size={16} aria-hidden />} onClick={onShare} data-testid="share-button">
              {shareLive ? "Shared · manage link" : "Share with my organisation"}
            </Button>
          )}
          <Button className="h-11 md:h-9" variant="ghost" icon={<Trash2 size={16} aria-hidden />} loading={deleting} onClick={onDelete}>Delete</Button>
        </div>}
      </div>

      {shared ? (
        <Section title="The structure, drawn" id="ca-annotated">
          <StructureSketch width={result.image.width} height={result.image.height} annotations={result.annotations} />
        </Section>
      ) : imageSrc ? (
        <Section title="The chart, annotated" id="ca-annotated">
          <AnnotatedChartV2 src={imageSrc} width={result.image.width} height={result.image.height} annotations={result.annotations} />
        </Section>
      ) : imageNote ? (
        <Section title="The chart, annotated" id="ca-annotated">
          <p className="text-t14 text-fg-muted" role="note">{imageNote}</p>
        </Section>
      ) : null}

      <Section title="The story" id="ca-story">
        <div className="space-y-space-2 text-t14 text-fg">{result.story.map((p, i) => <p key={i}>{p}</p>)}</div>
        <dl className="mt-space-3 grid gap-space-3 sm:grid-cols-3">
          <div><dt className="text-t12 text-fg-muted">Trend side</dt><dd className="text-t14 font-semibold text-fg">{TREND_TEXT[result.trend.side] ?? result.trend.side}</dd>
            <dd className="text-t12 text-fg-muted">{result.trend.text}</dd></div>
          <div><dt className="text-t12 text-fg-muted">Now</dt><dd className="text-t14 font-semibold text-fg">{PHASE_TEXT[result.current_state.phase] ?? result.current_state.phase}</dd>
            <dd className="text-t12 text-fg-muted">{result.current_state.text}</dd></div>
          <div><dt className="text-t12 text-fg-muted">RSI divergence</dt><dd className="text-t14 font-semibold text-fg">{result.rsi.divergence === "none" ? "None" : result.rsi.divergence === "bullish" ? "Bullish" : "Bearish"}</dd>
            <dd className="text-t12 text-fg-muted">{result.rsi.text}</dd></div>
        </dl>
      </Section>

      <Section title="What the method sees" id="ca-setup">
        <div className="flex flex-wrap items-center gap-space-2">
          <span className="rounded-pill bg-surface-2 px-space-3 py-space-1 text-t12 font-semibold text-fg" data-testid="setup-status">{statusText(result)}</span>
          <span className="text-t14 text-fg">{SIDE_TEXT[result.setup.side]}</span>
          {result.setup.label && <span className="rounded-pill border border-subtle px-space-2 py-space-1 text-t12 text-fg">Label {result.setup.label}</span>}
        </div>
        <dl className="mt-space-3">
          <Ref label="Trigger" value={result.setup.trigger} approximate={approx} />
          <Ref label="Invalidation" value={result.setup.invalidation} approximate={approx} />
          {result.setup.next_areas.map((n, i) => <Ref key={i} label={i === 0 ? "Next areas" : ""} value={n} approximate={approx} />)}
        </dl>
        <p className="mt-space-2 text-t12 text-fg-muted">These are conditions the method watches, not an instruction to trade.</p>
      </Section>

      <Section title="Evidence" id="ca-evidence">
        <ul className="divide-y divide-subtle" aria-label="Evidence checklist">
          {result.evidence.map((e) => (
            <li key={e.name} className="flex items-start gap-space-3 py-space-2" data-present={e.present}>
              <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-pill ${e.present ? "bg-fg text-surface-1" : "border border-border text-fg-muted"}`}
                aria-label={e.present ? "present" : "absent"} role="img">
                {e.present ? <Check size={12} aria-hidden /> : <X size={12} aria-hidden />}
              </span>
              <div className="min-w-0"><p className="text-t14 font-medium text-fg">{e.name}</p>{e.reason && <p className="text-t12 text-fg-muted">{e.reason}</p>}</div>
            </li>
          ))}
        </ul>
        <p className="mt-space-2 text-t12 text-fg-muted">Listed, not scored.</p>
      </Section>

      <Section title="Scenarios" id="ca-scenarios">
        <dl className="space-y-space-3 text-t14">
          <div><dt className="text-t12 font-medium text-fg-muted">Primary</dt><dd className="text-fg">{result.scenarios.primary}</dd></div>
          <div><dt className="text-t12 font-medium text-fg-muted">Alternative</dt><dd className="text-fg">{result.scenarios.alternative}</dd></div>
          {(result.scenarios.primary_invalidated_at.text || result.scenarios.primary_invalidated_at.price != null) && (
            <div><dt className="text-t12 font-medium text-fg-muted">The primary is proven wrong</dt>
              <dd className="text-fg">{result.scenarios.primary_invalidated_at.text}
                {result.scenarios.primary_invalidated_at.price != null && <span className="ml-space-2 font-tabular text-fg-muted">{price(result.scenarios.primary_invalidated_at.price, approx)}</span>}</dd></div>
          )}
        </dl>
      </Section>

      {result.what_to_wait_for && (
        <Section title="What to wait for" id="ca-wait">
          <p className="text-t14 text-fg">{result.what_to_wait_for.condition}</p>
          {shared ? null : waitCondition ? (
            <Button className="mt-space-3 h-11 md:h-9" icon={<Bell size={16} aria-hidden />} {...{ [PRINT_HIDE]: "" }}
              onClick={() => onAlert({ title: "Alert me when this happens", symbol: rec.symbol, timeframe: rec.timeframe, condition: waitCondition, approximate: approx })}>
              Alert me when this happens
            </Button>
          ) : <p className="mt-space-2 text-t12 text-fg-muted">{CANNOT_ALERT}</p>}
        </Section>
      )}

      {result.htf_context.length > 0 && (
        <Section title="Higher timeframes" id="ca-htf">
          <ul className="grid gap-space-2 sm:grid-cols-2 lg:grid-cols-3">
            {result.htf_context.map((h, hi) => (
              <li key={`${h.timeframe ?? ""}-${hi}`} className="rounded-control bg-surface-2 p-space-3">
                <p className="text-t14 font-semibold text-fg">{h.timeframe || "Higher timeframe"} · {TREND_TEXT[h.trend] ?? h.trend}</p>
                {h.nearest_areas.map((a, i) => (
                  <p key={i} className="text-t12 text-fg-muted">{a.side === "seller" ? "Seller" : "Buyer"} area{shared ? "" : ` ${band(a.price_low, a.price_high, approx)}`}{a.state ? ` · ${a.state}` : ""}</p>
                ))}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {result.key_levels.length > 0 && (
        <Section title="Key levels" id="ca-levels">
          <table className="hidden w-full text-left text-t14 md:table">
            <thead><tr className="text-t12 text-fg-muted"><th className="py-space-2 font-medium">Level</th><th className="font-medium">Kind</th>
              {!shared && <th className="pr-space-6 text-right font-medium">Price</th>}<th className="font-medium">State</th>
              {!shared && <th className="sr-only" {...{ [PRINT_HIDE]: "" }}>Alert</th>}</tr></thead>
            <tbody>
              {result.key_levels.map((lv, i) => (
                <tr key={i} className="border-t border-subtle">
                  <td className="py-space-2 text-fg">{lv.name || (lv.kind === "area" ? "Area" : "Line")}</td>
                  <td className="text-fg-muted">{lv.kind === "area" ? `${lv.side === "seller" ? "Seller" : "Buyer"} area` : "Line"}</td>
                  {!shared && <td className="pr-space-6 text-right font-tabular text-fg">{band(lv.price_low, lv.price_high, approx)}</td>}
                  <td className="text-fg-muted">{lv.state ?? "-"}</td>
                  {!shared && <td className="text-right" {...{ [PRINT_HIDE]: "" }}><Button size="sm" variant="ghost" onClick={() => onAlert(areaTarget(lv.price_low, lv.price_high, lv.name || "this level"))}>Set an alert</Button></td>}
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="space-y-space-2 md:hidden">
            {result.key_levels.map((lv, i) => (
              <li key={i} className="flex items-center justify-between gap-space-3 rounded-control bg-surface-2 p-space-3">
                <div className="min-w-0">
                  <p className="text-t14 font-medium text-fg">{lv.name || (lv.kind === "area" ? "Area" : "Line")}</p>
                  <p className="text-t12 text-fg-muted">{shared ? (lv.kind === "area" ? `${lv.side === "seller" ? "Seller" : "Buyer"} area` : "Line")
                    : band(lv.price_low, lv.price_high, approx)}{lv.state ? ` · ${lv.state}` : ""}</p>
                </div>
                {!shared && <Button className="h-11" variant="ghost" {...{ [PRINT_HIDE]: "" }} aria-label={`Set an alert on ${lv.name || "this level"}`} icon={<Bell size={16} aria-hidden />}
                  onClick={() => onAlert(areaTarget(lv.price_low, lv.price_high, lv.name || "this level"))} />}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {strikes && <StrikesSectionV2 state={strikes} onSymbol={onStrikesSymbol} />}

      <p className="rounded-panel border border-subtle bg-surface-2 p-space-4 text-t12 text-fg-muted" data-testid="disclaimer" role="note">{disclaimer}</p>
    </div>
  );
}
