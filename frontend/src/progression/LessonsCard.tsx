import { useEffect, useState } from "react";
import { journalApi } from "../journal/api";
import type { JournalLessons, LessonCard } from "../journal/types";
import { pathFor } from "../routes";
import { useLearnT } from "./i18n";

/**
 * N2-11 (flag `journal_lessons`): "learn from your journal" - each behaviour tag on the trader's own losing trades with
 * its counts and net P&L, linked to the method-library lesson (N2-10). Own data only; it states facts and links a
 * lesson, never what to do. Hidden while the flag (or the discipline engine) is off.
 */
export default function LessonsCard() {
  const [data, setData] = useState<JournalLessons | null>(null);
  useEffect(() => { journalApi.lessons().then(setData).catch(() => setData(null)); }, []);
  if (!data) return null;
  return <LessonsView data={data} />;
}

export function rupees(n: number): string {
  return `${n < 0 ? "-" : ""}₹${Math.abs(Math.round(n)).toLocaleString("en-IN")}`;
}

export function LessonsView({ data }: { data: JournalLessons }) {
  const t = useLearnT();
  return (
    <section className="mb-4 space-y-2 rounded-xl border border-border bg-surface-1 p-4 text-xs" aria-label={t("lessons.title")} data-testid="journal-lessons">
      <h2 className="text-sm font-semibold text-fg">{t("lessons.title")}</h2>
      <p className="text-fg-muted">{t("lessons.intro")}</p>
      {!data.cards.length ? <p className="text-fg-muted">{t("lessons.none", { weeks: data.weeks })}</p> : (
        <ul className="grid gap-2 sm:grid-cols-2">
          {data.cards.map((c: LessonCard) => (
            <li key={c.tag} className="space-y-1 rounded-lg border border-border p-2" data-testid={`lesson-${c.tag}`}>
              <p className="font-semibold text-fg">{t(`tags.${c.tag}`, { defaultValue: c.tag })}</p>
              <p className="text-fg">{t("lessons.card", { trades: c.trades, weeks: data.weeks, losing: c.losing_trades, pnl: rupees(c.losing_net_pnl) })}</p>
              {c.last_date && <p className="text-fg-muted">{t("lessons.last", { date: c.last_date })}</p>}
              {c.lesson && (
                <a href={`${pathFor("learn")}#learn-${c.lesson}`} className="text-info underline">
                  {t("lessons.read", { title: t(`concepts.${c.lesson}.title`) })}
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
      {data.count_unconfirmed && <p className="text-[11px] text-fg-muted">{t("lessons.unconfirmed")}</p>}
      <p className="text-[11px] text-fg-muted">{data.basis}.</p>
    </section>
  );
}
