import type { LadderCriterion, LadderEvidence as Evidence } from "../types";
import { disciplineWeeksNote, kellyLine, pct, ratio, rupees, ruinLine, shortWeek } from "./evidence";
import { useProgressionT } from "./i18n";

/**
 * N2-1 (flag `ladder_evidence`): the "Your figures" block of the progression ladder card. Every figure comes from the
 * server, computed from the user's own closed PAPER trades. Information only: there is no control here, and nothing
 * feeds sizing (that stays with the P1-d risk cap).
 */
export default function LadderEvidence({ evidence }: { evidence: Evidence }) {
  const t = useProgressionT();
  const ev = evidence;
  return (
    <section className="space-y-2 border-t border-border pt-2" data-testid="ladder-evidence" aria-label={t("evidence.title")}>
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
        <span className="font-semibold text-fg">{t("evidence.title")}</span>
        <span className="text-fg-muted">{t("evidence.counts", { trades: ev.trades, wins: ev.wins, losses: ev.losses, flat: ev.flat })}</span>
      </div>
      {!ev.enough ? (
        <p className="text-xs text-fg-muted" data-testid="evidence-too-few">{t("evidence.tooFew", { count: ev.trades, needed: ev.min_trades })}</p>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
            <Figure label={t("evidence.winRate")} value={pct(ev.win_rate)} />
            <Figure label={t("evidence.expectancy")} value={rupees(ev.expectancy, true)}
                    tone={ev.expectancy == null ? undefined : ev.expectancy > 0 ? "up" : ev.expectancy < 0 ? "down" : undefined} />
            <Figure label={t("evidence.profitFactor")} value={ev.profit_factor == null ? t("evidence.noLoss") : ratio(ev.profit_factor)} />
            <Figure label={t("evidence.payoffRatio")} value={ratio(ev.payoff_ratio)}
                    title={t("evidence.avgWinLoss", { win: rupees(ev.avg_win), loss: rupees(ev.avg_loss) })} />
          </dl>
          <div className="space-y-1 text-xs" data-testid="evidence-kelly">
            <p><span className="font-medium text-fg">{t("evidence.kelly.label")}: </span><span className="text-fg">{kellyLine(ev, t)}</span></p>
            <p className="text-[11px] text-fg-muted">{t("evidence.kelly.caveat")}</p>
          </div>
          <div className="space-y-1 text-xs" data-testid="evidence-ruin">
            <p><span className="font-medium text-fg">{t("evidence.ruin.label")}: </span><span className="text-fg">{ruinLine(ev, t)}</span></p>
            <p className="text-[11px] text-fg-muted">{t("evidence.ruin.caveat")}</p>
          </div>
        </>
      )}
      <p className="text-[11px] text-fg-muted">{t("evidence.basis")}</p>
    </section>
  );
}

function Figure({ label, value, tone, title }: { label: string; value: string; tone?: "up" | "down"; title?: string }) {
  return (
    <div title={title}>
      <dt className="text-fg-muted">{label}</dt>
      <dd className={`font-medium tabular-nums ${tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-fg"}`}>{value}</dd>
    </div>
  );
}

/** N2-1: the discipline criterion's weekly scores (oldest first) and how many weeks are still missing. */
export function DisciplineWeeks({ criterion }: { criterion: LadderCriterion }) {
  const t = useProgressionT();
  const note = disciplineWeeksNote(criterion, t);
  const weeks = criterion.weeks ?? [];
  if (!weeks.length && !note) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1 text-[11px]" data-testid="discipline-weeks">
      {weeks.length > 0 && (
        <ul className="flex flex-wrap gap-1" aria-label={t("discipline.weeksLabel")}>
          {weeks.map((w) => (
            <li key={w.week} title={t("discipline.weekScore", { week: w.week, score: w.score })}
                className="rounded bg-surface-2 px-1.5 py-0.5 tabular-nums text-fg">
              {shortWeek(w.week)} · {w.score}
            </li>
          ))}
        </ul>
      )}
      {note && <span className="text-fg-muted">{note}</span>}
    </div>
  );
}
