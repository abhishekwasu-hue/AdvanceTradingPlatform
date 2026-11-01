import { CircleHelp, History, LineChart, ListChecks, Sparkles } from "lucide-react";
import { lazy, Suspense } from "react";
import { Link } from "react-router";
import { chartTabPath } from "../../shell/shellRoutes";
import { isTimeframe } from "../../chart/timeframes";
import { Details } from "../../ui/Details";
import { EMPTY, formatPrice } from "../../ui/format";
import { AnalysisTableView, MiniChart } from "../../optionChain/AnalysisTabsV2";
import { useCopilotT } from "../i18n";
import { cx } from "../../components/primitives/cx";
import {
  actionState, answerParts, answerRow, areaText, distanceText, headlineAreas, notSetUpLine, stampText, toolsUsedLine, workedLine,
  type AnswerBlock, type AnswerHeadline, type ChatAnswer,
} from "./model";
import CostChip from "../components/CostChip";
import { RichText } from "./RichText";
import { StructureCardV2 } from "./StructureCardV2";

/** CU3: the answer's ONE action (and the strategy card's second, outlined, "test it" button). */
const PRIMARY = "inline-flex min-h-11 w-full items-center justify-center py-space-2 text-center gap-space-2 rounded-control bg-brand px-space-5 text-t14 font-semibold text-on-brand transition-colors duration-fast hover:bg-brand-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface sm:w-auto";
const SECONDARY = "inline-flex min-h-11 w-full items-center justify-center gap-space-2 rounded-control border border-brand px-space-5 py-space-2 text-t14 font-semibold text-brand transition-colors duration-fast hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface sm:w-auto";
const CHIP = "inline-flex min-h-9 items-center rounded-pill border border-border bg-surface-2 px-space-3 py-space-1 text-t13 text-fg";
const TREND_TONE = { up: "bg-up-tint text-up", down: "bg-down-tint text-down", sideways: "bg-surface-2 text-fg" } as const;
const TREND_WORD = { up: "▲ Up", down: "▼ Down", sideways: "Sideways" } as const;

// CU3: the mini chart is the Charts page's own embedded chart (its own chunk: the chart library loads with it).
const EmbeddedChartV2 = lazy(() => import("../../chart/EmbeddedChartV2"));

function Sections({ sections }: { sections: ChatAnswer["sections"] }) {
  if (sections.length === 0) return null;
  return (
    <div className="grid gap-space-3 md:grid-cols-2">
      {sections.map((s) => (
        <section key={s.title} className="min-w-0 rounded-control border border-subtle bg-surface-2 p-space-3" data-section={s.title}>
          <h3 className="text-t13 font-semibold text-fg">{s.title}</h3>
          <ul className="mt-space-1 space-y-space-1">
            {s.lines.map((line, i) => {
              const row = answerRow(line);
              // UI-28 U2.4: "Label: value" lines are rows with the number right-aligned and signed; sentences stay text.
              return row ? (
                <li key={i} className="flex flex-wrap items-baseline justify-between gap-x-space-3 text-t14" data-answer-row>
                  <span className="shrink-0 text-fg-muted">{row.label}:</span>
                  <span className={cx("shrink-0 font-tabular", row.dir === "up" ? "text-up" : row.dir === "down" ? "text-down" : "text-fg")}
                    data-signed={row.dir ?? undefined}>
                    {row.dir && <span aria-hidden className="text-t12">{row.dir === "up" ? "▲" : "▼"}</span>}{row.value}
                  </span>
                </li>
              ) : <li key={i} className="text-t14 text-fg">{line}</li>;
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

function Blocks({ blocks }: { blocks: AnswerBlock[] }) {
  return <>{blocks.map((b, i) => (
    <div key={i} className="min-w-0 rounded-control border border-subtle p-space-3" data-block={b.type}>
      {b.type === "chart" ? <MiniChart chart={b.chart} title={b.title} /> : <AnalysisTableView table={b.table} />}
    </div>
  ))}</>;
}

/** CU3: the headline number row - price, trend chip, nearest area from - to, distance - before any sentence. */
export function HeadlineRow({ h }: { h: AnswerHeadline }) {
  const t = useCopilotT();
  return (
    <dl className="grid grid-cols-2 gap-space-3 md:flex md:flex-wrap md:items-end md:gap-space-6" data-headline={h.symbol}>
      <div className="min-w-0">
        <dt className="text-t12 font-medium uppercase text-fg-muted">{h.symbol} · {h.price_label}</dt>
        <dd className="font-tabular text-t20 font-semibold text-fg md:text-t24" data-headline-price>{formatPrice(h.price)}</dd>
      </div>
      <div className="min-w-0">
        <dt className="text-t12 font-medium uppercase text-fg-muted">{t("chat.trend")} · {h.timeframe}</dt>
        <dd className="mt-space-1">
          <span className={cx("inline-flex min-h-7 items-center rounded-pill px-space-3 text-t13 font-semibold", h.trend ? TREND_TONE[h.trend] : "bg-surface-2 text-fg-muted")}
            data-trend={h.trend ?? "unknown"}>{h.trend ? TREND_WORD[h.trend] : t("chat.trendUnknown")}</span>
        </dd>
      </div>
      <div className="col-span-2 min-w-0 md:col-span-1">
        <dt className="text-t12 font-medium uppercase text-fg-muted">{t("chat.nearestArea")}</dt>
        {h.area ? (
          <dd className="font-tabular text-t15 text-fg" data-headline-area={h.area.where}>
            <span className="font-semibold">{areaText(h.area, (v) => formatPrice(v))}</span>
            <span className="block text-t13 text-fg-muted">{distanceText(h.area, (v) => formatPrice(v))}</span>
          </dd>
        ) : <dd className="text-t14 text-fg-muted" data-headline-area="none">{EMPTY}</dd>}
      </div>
    </dl>
  );
}

/**
 * CU3 (owner order CP-UX): one Copilot answer as a card - the headline number row first (a chart answer: price, trend,
 * nearest area, distance), then the short answer, the mini chart, ONE action; the strategy answer as a result card (its
 * rules and settings as chips, "Values you decide", the test result, then its two buttons). "How I worked this out",
 * "What I don't know", the tools used and every theory section sit behind ONE "Details" tap, closed by default. A
 * refusal shows its plain sentence and what to ask instead. Every word and number comes from the server; the page
 * footer carries the one disclaimer (`disclaimer` shows the answer's own line where the card stands alone).
 */
export function AnswerV2({ answer, onAsk, disclaimer = true }: { answer: ChatAnswer; onAsk?: (q: string) => void; disclaimer?: boolean }) {
  const t = useCopilotT();
  const used = toolsUsedLine(answer);
  const adminNotSetUp = notSetUpLine(answer);
  const parts = answerParts(answer);
  const h = answer.headline ?? null;
  // M59 CR-1: the chart reading as a card - its free-text words (the AI wording or the rule sentence) move behind Details
  const card = answer.structure_card ?? null;
  const chart = h?.chart ?? null;
  const worked = answer.job !== "refuse" && answer.job !== "help";
  const hasDetails = worked || Boolean(card) || parts.folded.length > 0 || parts.blocksFolded.length > 0 || answer.unknowns.length > 0 || answer.notes.length > 0;
  return (
    <article className="space-y-space-3" data-answer={answer.job} data-source={answer.source} aria-label={answer.title}>
      {h && <HeadlineRow h={h} />}
      {card ? <StructureCardV2 card={card} /> : (
        <div className="space-y-space-1" data-summary>
          {answer.summary.map((line, i) => <RichText key={i} source={line} />)}
        </div>
      )}
      {chart && (
        <div data-mini-chart={chart.symbol}>
          <Suspense fallback={<div aria-busy="true" className="h-48 rounded-panel border border-subtle" />}>
            <EmbeddedChartV2 symbol={chart.symbol} exchange={chart.exchange} timeframe={isTimeframe(chart.timeframe) ? chart.timeframe : "day"}
              areas={headlineAreas(h)} openLink={false} className="h-48" />
          </Suspense>
        </div>
      )}
      {(parts.rules.length > 0 || parts.settings.length > 0) && (
        <section className="space-y-space-2" data-strategy-card>
          {parts.rules.length > 0 && (
            <div>
              <h3 className="mb-space-1 text-t13 font-semibold text-fg">{t("chat.rules")}</h3>
              <ul className="flex flex-wrap gap-space-2">{parts.rules.map((r) => <li key={r} className={CHIP} data-rule-chip>{r.replace(/\.$/, "")}</li>)}</ul>
            </div>
          )}
          {parts.settings.length > 0 && (
            <div>
              <h3 className="mb-space-1 text-t13 font-semibold text-fg">{t("chat.settings")}</h3>
              <ul className="flex flex-wrap gap-space-2">{parts.settings.map((r) => <li key={r} className={CHIP} data-setting-chip>{r.replace(/\.$/, "")}</li>)}</ul>
            </div>
          )}
        </section>
      )}
      <Sections sections={parts.shown} />
      <Blocks blocks={parts.blocksShown} />
      {(answer.action || chart || (answer.followup && onAsk)) && (
        <div className="flex flex-col gap-space-2 sm:flex-row sm:flex-wrap" data-actions>
          {answer.action ? (
            <Link to={answer.action.to} state={actionState(answer)} data-action={answer.draft ? "draft" : "page"} className={PRIMARY}>
              {answer.action.label}
            </Link>
          ) : chart ? (
            <Link to={chartTabPath(chart.symbol, chart.exchange, chart.timeframe)} data-action="chart" className={PRIMARY}>
              <LineChart size={16} aria-hidden />{t("chat.openChart")}
            </Link>
          ) : null}
          {answer.followup && onAsk && (
            <button type="button" onClick={() => onAsk(answer.followup!.question)} data-followup className={SECONDARY}>
              <History size={16} aria-hidden />{answer.followup.label}
            </button>
          )}
        </div>
      )}
      {answer.starters.length > 0 && onAsk && (
        <div className="flex flex-wrap gap-space-2">
          {answer.starters.map((s) => (
            <button key={s} type="button" onClick={() => onAsk(s)}
              className="inline-flex min-h-11 items-center rounded-pill border border-border px-space-3 text-t13 text-fg hover:bg-surface-2 md:min-h-9">
              {s}
            </button>
          ))}
        </div>
      )}
      {/* UI-28 U7.7: the cost of this answer's AI call, beside it (none for a rule-based answer). */}
      {answer.usage && answer.usage.calls > 0 && (
        <p data-answer-cost><CostChip tokens={answer.usage.tokens_input + answer.usage.tokens_output} inr={answer.usage.cost_inr} /></p>
      )}
      {hasDetails && (
        <Details id="copilot.answer" label={t("chat.details")} className="mt-0">
          {card && answer.summary.length > 0 && (
            <section className="space-y-space-1" data-summary="folded">
              {answer.summary.map((line, i) => <RichText key={i} source={line} className="text-t14 text-fg" />)}
            </section>
          )}
          <Sections sections={parts.folded} />
          <Blocks blocks={parts.blocksFolded} />
          {answer.notes.map((n) => <p key={n} className="text-t13 text-fg-muted" data-note="chart-reader">{n}</p>)}
          {answer.unknowns.length > 0 && (
            <section className="rounded-control border border-subtle bg-surface-1 p-space-3" data-unknowns>
              <h3 className="flex items-center gap-space-1 text-t13 font-semibold text-fg"><CircleHelp size={14} aria-hidden />{t("chat.unknown")}</h3>
              <ul className="mt-space-1 space-y-space-1">
                {answer.unknowns.map((u, i) => <li key={i} className="text-t14 text-fg-muted">{u}</li>)}
              </ul>
            </section>
          )}
          {worked && (
            <section className="rounded-control border border-subtle p-space-3" data-worked>
              <h3 className="flex items-center gap-space-1 text-t13 font-semibold text-fg"><ListChecks size={14} aria-hidden />{t("chat.worked")}</h3>
              {used && <p className="mt-space-2 text-t13 text-fg" data-tools-used><span className="font-semibold">{t("chat.toolsUsed")}:</span> {used}</p>}
              {answer.worked.length === 0 ? (
                <p className="mt-space-2 text-t13 text-fg-muted">{t("chat.workedNone")}</p>
              ) : (
                <ol className="mt-space-2 space-y-space-1">
                  {answer.worked.map((s, i) => (
                    <li key={i} className="text-t13 text-fg-muted" data-step={s.tool} data-ok={s.ok}>
                      {workedLine(s)}{stampText(s.as_of) ? ` · ${stampText(s.as_of)}` : ""}
                    </li>
                  ))}
                </ol>
              )}
              <p className="mt-space-2 flex items-center gap-space-1 text-t12 text-fg-muted">
                {answer.source === "ai" && <Sparkles size={12} aria-hidden />}
                {answer.source === "ai" ? t("chat.wordedAi") : t("chat.wordedRules")}
              </p>
              {answer.note && <p className="mt-space-1 text-t12 text-fg-muted">{answer.note}</p>}
              {adminNotSetUp && <p className="mt-space-1 text-t12 text-fg-muted" data-admin-not-set-up>{t("chat.notSetUpAdmin")}: {adminNotSetUp}</p>}
            </section>
          )}
        </Details>
      )}
      {disclaimer && <p className="text-t12 text-fg-muted" data-disclaimer>{answer.disclaimer}</p>}
    </article>
  );
}

export default AnswerV2;
