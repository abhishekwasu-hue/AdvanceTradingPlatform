import { ChevronLeft, ChevronRight } from "lucide-react";
import { CardV2, EmptyStateV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_SECONDARY } from "../trading/PartsV2";
import { chipClass } from "../ui/Chip";
import { ErrorState } from "../ui/ErrorState";
import { formatDateIST, formatNumber } from "../ui/format";
import { HelpPopover } from "../ui/HelpPopover";
import { COMPONENT_LABELS, TAG_HELP, tagLabel } from "./format";
import { PnlV2, RV2 } from "./JournalPartsV2";
import { mostTagged } from "./journalModel";
import type { DisciplineScore, EntryKind, JournalWeekV2, ScoreComponent } from "./types";

/**
 * M62 JN-3: the weekly review - the existing discipline score with a (?) on each of its components, "what you tagged
 * most" (his own tags and the detector's he did not dismiss) and the week's trades. Numbers only.
 */

export type WeekState = { status: "loading" } | { status: "error"; error: unknown } | { status: "ok"; data: JournalWeekV2 };

export function componentHelp(c: ScoreComponent): string {
  const tags = c.tags.map((t) => `${tagLabel(t)} (${(TAG_HELP[t] ?? "").replace(/\.$/, "").toLowerCase() || "a behaviour tag"})`);
  const clean = c.clean_pct == null ? "no decisions yet" : `${formatNumber(c.clean_pct, 0)}% of the week's decisions had none of them`;
  return `The share of the week's decisions with none of these tags: ${tags.join("; ")}. This week: ${clean}`
    + `${c.flagged ? `, ${formatNumber(c.flagged)} flagged` : ""}. Weight ${formatNumber(c.weight_share * 100, 0)}% of the score.`;
}

export function ScoreCardV2({ score }: { score: DisciplineScore }) {
  return (
    <CardV2 title="Discipline score" testId="discipline-score">
      <div className="flex flex-col gap-space-4 md:flex-row md:items-start">
        <div className="shrink-0">
          <p className="font-tabular text-t32 font-semibold text-fg" data-score>{score.score ?? "–"}<span className="text-t15 text-fg-muted"> / 100</span></p>
          <p className="text-t13 text-fg-muted">{score.score == null ? (score.note ?? "No trades this week") : `${formatNumber(score.decisions)} decisions${score.note ? ` ${score.note}` : ""}`}</p>
        </div>
        <ul className="min-w-0 flex-1 space-y-space-3">
          {score.components.map((c) => {
            const label = COMPONENT_LABELS[c.name] ?? c.name;
            return (
              <li key={c.name} data-component={c.name}>
                <div className="flex items-center justify-between gap-space-2 text-t14">
                  <span className="flex items-center gap-space-1 text-fg">{label}<HelpPopover title={label}>{componentHelp(c)}</HelpPopover></span>
                  <span className="font-tabular text-fg-muted">{formatNumber(c.points, 1)} / {formatNumber(c.max_points, 1)}</span>
                </div>
                <div aria-hidden className="mt-space-1 h-1.5 overflow-hidden rounded-pill bg-surface-3">
                  <div className="h-1.5 rounded-pill bg-brand" style={{ width: `${c.max_points ? (100 * c.points) / c.max_points : 0}%` }} />
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </CardV2>
  );
}

export function WeeklyReviewV2({ state, week, onWeek, onRetry, onOpen }: {
  state: WeekState; week: string; onWeek: (offset: number) => void; onRetry: () => void; onOpen: (kind: EntryKind, id: number) => void;
}) {
  const nav = (
    <div className="flex items-center gap-space-2" data-week-nav>
      <button type="button" className={BUTTON_SECONDARY} aria-label="Previous week" onClick={() => onWeek(-1)}><ChevronLeft size={16} aria-hidden /></button>
      <span className="font-tabular text-t14 text-fg">{week}</span>
      <button type="button" className={BUTTON_SECONDARY} aria-label="Next week" onClick={() => onWeek(1)}><ChevronRight size={16} aria-hidden /></button>
    </div>
  );
  if (state.status !== "ok") {
    return (
      <div className="space-y-space-4">
        {nav}
        <CardV2 title="Discipline score" testId="discipline-score">
          {state.status === "loading" ? <LoadingRowsV2 rows={3} /> : <ErrorState message={state.error} onRetry={onRetry} variant="inline" />}
        </CardV2>
      </div>
    );
  }
  const d = state.data;
  const all = [
    ...d.entries.map((e) => ({ kind: "trade" as EntryKind, id: e.trade_id, symbol: e.symbol, setup: e.plan_view?.setup ?? e.setup, at: e.entry_time,
      pnl: e.pnl, r: e.r_multiple, tags: e.tags, user_tags: e.user_tags ?? [], outside: false })),
    ...(d.manual_entries ?? []).map((m) => ({ kind: "manual" as EntryKind, id: m.id, symbol: m.symbol, setup: m.setup, at: m.entry_time, pnl: m.pnl,
      r: m.r_multiple, tags: m.tags, user_tags: m.user_tags, outside: true })),
  ].sort((a, b) => (a.at ?? "").localeCompare(b.at ?? ""));
  const top = mostTagged(all);
  return (
    <div className="space-y-space-4" data-weekly-review>
      {nav}
      <ScoreCardV2 score={d.score} />
      <CardV2 title="What you tagged most" testId="tagged-most">
        {top.length === 0 ? <EmptyStateV2 text="No tags this week." /> : (
          <ul className="space-y-space-2">
            {top.map((t) => (
              <li key={t.tag} className="flex items-center justify-between gap-space-3 text-t14" data-tag-count={t.tag}>
                <span className="text-fg">{t.label}</span>
                <span className="font-tabular text-fg-muted">{formatNumber(t.count)} {t.count === 1 ? "trade" : "trades"}</span>
              </li>
            ))}
          </ul>
        )}
      </CardV2>
      <CardV2 title="The week's trades" count={all.length} testId="week-trades">
        {all.length === 0 ? <EmptyStateV2 text="No trades this week." /> : (
          <ul className="divide-y divide-subtle">
            {all.map((e) => (
              <li key={`${e.kind}-${e.id}`}>
                <button type="button" onClick={() => onOpen(e.kind, e.id)} data-week-row={`${e.kind}-${e.id}`}
                  className="flex min-h-11 w-full items-center gap-space-3 py-space-2 text-left hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                  <span className="w-24 shrink-0 whitespace-nowrap font-tabular text-t13 text-fg-muted">{e.at ? formatDateIST(e.at) : "–"}</span>
                  <span className="min-w-0 flex-1 truncate text-t14 font-semibold text-fg">{e.symbol}
                    <span className="ml-space-2 font-normal text-fg-muted">{e.setup ?? ""}</span>
                    {e.outside && <span className={`${chipClass({ tone: "warn" })} ml-space-2`}>Outside ATP</span>}
                  </span>
                  <span className="shrink-0 text-right"><PnlV2 value={e.pnl} /></span>
                  <span className="hidden w-16 shrink-0 text-right sm:inline"><RV2 value={e.r} /></span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardV2>
    </div>
  );
}
