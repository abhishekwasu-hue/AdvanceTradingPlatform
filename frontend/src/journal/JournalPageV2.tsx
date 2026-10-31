import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ApiError, errorSentence } from "../api/errors";
import { useToast } from "../components/Toast";
import { CardV2, EmptyStateV2 } from "../dashboard/CardV2";
import DisciplineCardV2 from "../growth/DisciplineCardV2";
import PageDishV2 from "../growth/PageDishV2";
import { PageHeader } from "../ui/PageHeader";
import { PageTabs } from "../ui/PageTabs";
import { journalApi as defaultApi, type JournalApi } from "./api";
import { EntrySheetV2, type EntryTarget } from "./EntrySheetV2";
import { isoWeekOf, shiftWeek } from "./format";
import { ExportButtonsV2, JournalListV2, type ListState } from "./JournalListV2";
import { JournalStatsV2, type StatsState } from "./JournalStatsV2";
import { saveBlob } from "./journalModel";
import { ManualTradeSheetV2 } from "./ManualTradeV2";
import { OwnListsV2 } from "./OwnListsV2";
import { journalTarget } from "./recall";
import SimilarSetupsV2 from "./SimilarSetupsV2";
import type { JournalFilters } from "./types";
import { WeeklyReviewV2, type WeekState } from "./WeeklyReviewV2";

/**
 * M62 JN-3 / JN-4 (`ui_v2`): the Journal page - the list first (with its filters and the CSV / PDF export), the entry
 * editor as a sheet, "Add a trade done outside ATP", the statistics and the weekly review. No page-top help: the (?)
 * on a figure says how it is worked out. Behind `discipline_engine` like the rest of the journal (503 while it is off).
 */

type Tab = "trades" | "stats" | "review" | "settings";
const PAGE = 200;
const OFF_TEXT = "The journal is not turned on for your account yet.";

const isOff = (e: unknown) => e instanceof ApiError && e.status === 503;

export default function JournalPageV2({ api = defaultApi, shareCard = false, recallOn = false, settings }: {
  api?: JournalApi; shareCard?: boolean; recallOn?: boolean; settings?: ReactNode;
}) {
  const toast = useToast();
  const [target0] = useState(() => (typeof window === "undefined" ? { week: null, tradeId: null } : journalTarget(window.location.search, window.location.hash)));
  const [tab, setTab] = useState<Tab>(target0.week ? "review" : "trades");
  const [filters, setFilters] = useState<JournalFilters>({});
  const [limit, setLimit] = useState(PAGE);
  const [list, setList] = useState<ListState>({ status: "loading" });
  const [stats, setStats] = useState<StatsState>({ status: "loading" });
  const [week, setWeek] = useState<string>(target0.week ?? isoWeekOf(new Date()));
  const [weekState, setWeekState] = useState<WeekState>({ status: "loading" });
  const [round, setRound] = useState(0);
  const [off, setOff] = useState(false);
  const [sheet, setSheet] = useState<EntryTarget | null>(target0.tradeId ? { kind: "trade", id: target0.tradeId } : null);
  const [manualOpen, setManualOpen] = useState(false);
  const [exporting, setExporting] = useState<"csv" | "pdf" | null>(null);
  const [customTags, setCustomTags] = useState<string[]>([]);
  const refresh = useCallback(() => setRound((r) => r + 1), []);

  const fail = useCallback((e: unknown, set: (s: { status: "error"; error: unknown }) => void) => {
    if (isOff(e)) setOff(true);
    else set({ status: "error", error: e });
  }, []);

  useEffect(() => {
    let alive = true;
    setList((s) => (s.status === "ok" ? s : { status: "loading" }));
    api.entries(filters, limit).then((data) => { if (alive) { setList({ status: "ok", data }); setOff(false); } })
      .catch((e) => { if (alive) fail(e, setList); });
    return () => { alive = false; };
  }, [api, filters, limit, round, fail]);

  useEffect(() => {
    if (tab !== "stats") return undefined;
    let alive = true;
    setStats({ status: "loading" });
    api.stats(filters).then((data) => { if (alive) setStats({ status: "ok", data }); }).catch((e) => { if (alive) fail(e, setStats); });
    return () => { alive = false; };
  }, [api, tab, filters, round, fail]);

  useEffect(() => {
    if (tab !== "review") return undefined;
    let alive = true;
    setWeekState({ status: "loading" });
    api.weekV2(week).then((data) => { if (alive) setWeekState({ status: "ok", data }); }).catch((e) => { if (alive) fail(e, setWeekState); });
    return () => { alive = false; };
  }, [api, tab, week, round, fail]);

  useEffect(() => {
    let alive = true;
    api.settings().then((d) => { if (alive) setCustomTags(d.settings.custom_tags ?? []); }).catch(() => { /* the list shows its own error */ });
    return () => { alive = false; };
  }, [api]);

  async function exportAs(format: "csv" | "pdf") {
    setExporting(format);
    try {
      const { blob, filename } = await api.exportFile(format, filters);
      saveBlob(blob, filename);
    } catch (e) { toast.error(errorSentence(e)); } finally { setExporting(null); }
  }

  const header = <PageHeader title="Journal" section="journal" />;
  if (off) {
    return <div className="space-y-space-4">{header}<CardV2 title="Journal" testId="journal-off"><EmptyStateV2 text={OFF_TEXT} /></CardV2></div>;
  }
  const changeFilters = (f: JournalFilters) => { setFilters(f); setLimit(PAGE); };
  return (
    <div className="space-y-space-4" data-journal-v2>
      {header}
      {shareCard && <PageDishV2 page="journal" />}
      {shareCard && <DisciplineCardV2 />}
      <PageTabs label="Journal" value={tab} onChange={setTab} idPrefix="journal"
        items={[{ key: "trades", label: "Trades" }, { key: "stats", label: "Stats" }, { key: "review", label: "Weekly review", short: "Week" },
          { key: "settings", label: "Settings" }]} />
      <div role="tabpanel" id={`journal-panel-${tab}`} aria-labelledby={`journal-tab-${tab}`}>
        {tab === "trades" && (
          <JournalListV2 state={list} filters={filters} onFilters={changeFilters} api={api} onRetry={refresh}
            onOpen={(r) => setSheet({ kind: r.kind, id: r.id })} onAddManual={() => setManualOpen(true)} onMore={() => setLimit((l) => l + PAGE)}
            exportButtons={<ExportButtonsV2 onExport={exportAs} busy={exporting} />} />
        )}
        {tab === "stats" && <JournalStatsV2 state={stats} onRetry={refresh} />}
        {tab === "review" && (
          <WeeklyReviewV2 state={weekState} week={week} onWeek={(o) => setWeek((w) => shiftWeek(w, o))} onRetry={refresh}
            onOpen={(kind, id) => setSheet({ kind, id })} />
        )}
        {tab === "settings" && (
          <div className="space-y-space-4">
            <OwnListsV2 api={api} onSaved={(d) => setCustomTags(d.settings.custom_tags ?? [])} />
            {settings}
          </div>
        )}
      </div>
      <EntrySheetV2 target={sheet} api={api} customTags={customTags} onClose={() => setSheet(null)} onChanged={refresh}
        extra={recallOn ? (e) => (e.kind === "trade" ? <SimilarSetupsV2 kind="trade" id={e.id} /> : null) : undefined} />
      <ManualTradeSheetV2 open={manualOpen} api={api} onClose={() => setManualOpen(false)} onSaved={(e) => { refresh(); setSheet({ kind: "manual", id: e.id }); }} />
    </div>
  );
}
