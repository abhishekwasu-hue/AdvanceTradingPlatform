import { CircleHelp, ListChecks, Sparkles } from "lucide-react";
import { Link } from "react-router";
import { AnalysisTableView, MiniChart } from "../../optionChain/AnalysisTabsV2";
import { useCopilotT } from "../i18n";
import { stampText, workedLine, type ChatAnswer } from "./model";

/**
 * One Copilot answer: the short answer first, then the data as sections of plain lines, inline tables and small charts
 * (the option chain analyses' own components), "What I don't know", "How I worked this out" (every tool called, its time
 * stamp and the numbers it returned) and the one-line disclaimer. A refusal shows its plain sentence and what to ask
 * instead. Every word and number comes from the server.
 */
export function AnswerV2({ answer, onAsk }: { answer: ChatAnswer; onAsk?: (q: string) => void }) {
  const t = useCopilotT();
  return (
    <article className="space-y-space-3" data-answer={answer.job} data-source={answer.source} aria-label={answer.title}>
      <div className="space-y-space-1">
        {answer.summary.map((line, i) => <p key={i} className="text-t15 text-fg">{line}</p>)}
      </div>
      {answer.notes.map((n) => <p key={n} className="text-t13 text-fg-muted" data-note="chart-reader">{n}</p>)}
      {answer.action && (
        <Link to={answer.action.to} className="inline-flex min-h-11 items-center text-t14 font-semibold text-brand hover:underline md:min-h-0">
          {answer.action.label}
        </Link>
      )}
      {answer.sections.length > 0 && (
        <div className="grid gap-space-3 md:grid-cols-2">
          {answer.sections.map((s) => (
            <section key={s.title} className="min-w-0 rounded-control border border-subtle bg-surface-2 p-space-3" data-section={s.title}>
              <h3 className="text-t13 font-semibold text-fg">{s.title}</h3>
              <ul className="mt-space-1 space-y-space-1">
                {s.lines.map((line, i) => <li key={i} className="text-t14 text-fg">{line}</li>)}
              </ul>
            </section>
          ))}
        </div>
      )}
      {answer.blocks.map((b, i) => (
        <div key={i} className="min-w-0 rounded-control border border-subtle p-space-3" data-block={b.type}>
          {b.type === "chart" ? <MiniChart chart={b.chart} title={b.title} /> : <AnalysisTableView table={b.table} />}
        </div>
      ))}
      {answer.unknowns.length > 0 && (
        <section className="rounded-control border border-subtle bg-surface-1 p-space-3" data-unknowns>
          <h3 className="flex items-center gap-space-1 text-t13 font-semibold text-fg"><CircleHelp size={14} aria-hidden />{t("chat.unknown")}</h3>
          <ul className="mt-space-1 space-y-space-1">
            {answer.unknowns.map((u, i) => <li key={i} className="text-t14 text-fg-muted">{u}</li>)}
          </ul>
        </section>
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
      {answer.job !== "refuse" && answer.job !== "help" && (
        <details className="rounded-control border border-subtle p-space-3" data-worked>
          <summary className="flex min-h-11 cursor-pointer items-center gap-space-1 text-t13 font-semibold text-fg md:min-h-0">
            <ListChecks size={14} aria-hidden />{t("chat.worked")}
          </summary>
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
        </details>
      )}
      <p className="text-t12 text-fg-muted" data-disclaimer>{answer.disclaimer}</p>
    </article>
  );
}

export default AnswerV2;
