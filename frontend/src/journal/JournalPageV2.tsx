import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useAddress } from "../inplace/address";
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
import { ImportTradesSheetV2 } from "./ImportTradesV2";
import { ManualTradeSheetV2 } from "./ManualTradeV2";
import { OwnListsV2 } from "./OwnListsV2";
import { journalTarget } from "./recall";
import { MyStudiesV2 } from "../study/MyStudiesV2";
import SimilarSetupsV2 from "./SimilarSetupsV2";
import { DailyCardV2, MonthlyV2, PeriodSwitchV2, WeeklyExtrasV2, shiftDay, shiftMonth, type Load, type ReviewPeriod } from "./ReviewsV2";
import { PlansV2 } from "./PlansV2";
import { ProofV2 } from "./ProofV2";
import { DeliveryV2, TodayTabV2 } from "./TodayTabV2";
import type { DailyCard, GoalProgress, JournalFilters, JournalPlan, JournalToday, MonthlyReview, ProofPage, WaitingCheckin, WeeklyExtras } from "./types";
import { WaitingCheckinsV2 } from "./WaitingCheckinsV2";
import { WeeklyReviewV2, type WeekState } from "./WeeklyReviewV2";

/** Today's IST calendar day as YYYY-MM-DD. */
export function istToday(now: Date = new Date()): string {
  return now.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}

/**
 * M62 JN-3 / JN-4 (`ui_v2`): the Journal page - the list first (with its filters and the CSV / PDF export), the entry
 * editor as a sheet, "Add a trade done outside ATP", the statistics and the weekly review. No page-top help: the (?)
 * on a figure says how it is worked out. Behind `discipline_engine` like the rest of the journal (503 while it is off).
 */

type Tab = "today" | "trades" | "review" | "proof" | "plans" | "stats" | "studies" | "settings";
const PAGE = 200;
const OFF_TEXT = "The journal is not turned on for your account yet.";

const isOff = (e: unknown) => e instanceof ApiError && e.status === 503;

/** "2 trades added, 1 closed. 3 fills matched your ATP trades." - or "No new trades from your broker." */
export function syncText(added: number, closed: number, matched: number): string {
  const parts: string[] = [];
  if (added > 0) parts.push(`${added} ${added === 1 ? "trade" : "trades"} added`);
  if (closed > 0) parts.push(`${closed} closed`);
  const head = parts.length > 0 ? `${parts.join(", ")}.` : "No new trades from your broker.";
  return matched > 0 ? `${head} ${matched} matched your ATP trades.` : head;
}

export default function JournalPageV2({ api = defaultApi, shareCard = false, recallOn = false, acceptedOn = false, studiesOn = false, settings }: {
  api?: JournalApi; shareCard?: boolean; recallOn?: boolean; settings?: ReactNode;
  /** M77 SX-7 (flag study_export): a "My studies" tab - his saved Copilot studies, in place. */
  studiesOn?: boolean;
  /** M76 MP-5 (flag trade_warnings): the weekly review shows the check-card lines he pressed Place with. */
  acceptedOn?: boolean;
}) {
  const toast = useToast();
  // M75 NAV-5a: the router's address (the journal in an in-place panel has its own).
  const here = useAddress();
  const [target0] = useState(() => journalTarget(here.search, here.hash));
  // P2-8 J6: Today first; a link to one trade opens Trades, a link to a week opens Reviews
  const [tab, setTab] = useState<Tab>(target0.week ? "review" : target0.tradeId ? "trades" : "today");
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
  const [importOpen, setImportOpen] = useState(false);
  const [exporting, setExporting] = useState<"csv" | "pdf" | null>(null);
  const [syncing, setSyncing] = useState(false);
  // P2-8 J2 / J3: the check-in prompts and the day / week / month reviews
  const [waiting, setWaiting] = useState<WaitingCheckin[]>([]);
  const [period, setPeriod] = useState<ReviewPeriod>("week");
  const [day, setDay] = useState(() => istToday());
  const [month, setMonth] = useState(() => istToday().slice(0, 7));
  const [daily, setDaily] = useState<Load<DailyCard>>({ status: "loading" });
  const [extras, setExtras] = useState<Load<WeeklyExtras>>({ status: "loading" });
  const [monthly, setMonthly] = useState<Load<MonthlyReview>>({ status: "loading" });
  // P2-8 J4 / J5 / J6: My proof, his plans and the Today tab
  const [todayState, setTodayState] = useState<Load<JournalToday>>({ status: "loading" });
  const [proofState, setProofState] = useState<Load<ProofPage>>({ status: "loading" });
  const [plansState, setPlansState] = useState<Load<{ plans: JournalPlan[]; goals: GoalProgress[] }>>({ status: "loading" });
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
    api.waitingCheckins().then((d) => { if (alive) setWaiting(d.waiting); }).catch(() => { /* intentional: no prompt card */ });
    return () => { alive = false; };
  }, [api, round]);

  useEffect(() => {
    if (tab !== "review") return undefined;
    let alive = true;
    if (period === "day") {
      setDaily({ status: "loading" });
      api.daily(day).then((data) => { if (alive) setDaily({ status: "ok", data }); }).catch((e) => { if (alive) fail(e, setDaily); });
    } else if (period === "week") {
      setExtras({ status: "loading" });
      api.weeklyExtras(week).then((data) => { if (alive) setExtras({ status: "ok", data }); }).catch((e) => { if (alive) fail(e, setExtras); });
    } else {
      setMonthly({ status: "loading" });
      api.monthly(month).then((data) => { if (alive) setMonthly({ status: "ok", data }); }).catch((e) => { if (alive) fail(e, setMonthly); });
    }
    return () => { alive = false; };
  }, [api, tab, period, day, week, month, round, fail]);

  useEffect(() => {
    let alive = true;
    if (tab === "today") {
      api.today().then((data) => { if (alive) setTodayState({ status: "ok", data }); }).catch((e) => { if (alive) fail(e, setTodayState); });
      api.daily(istToday()).then((data) => { if (alive) setDaily({ status: "ok", data }); }).catch((e) => { if (alive) fail(e, setDaily); });
    } else if (tab === "proof") {
      api.proof().then((data) => { if (alive) setProofState({ status: "ok", data }); }).catch((e) => { if (alive) fail(e, setProofState); });
    } else if (tab === "plans") {
      api.plans().then((data) => { if (alive) setPlansState({ status: "ok", data }); }).catch((e) => { if (alive) fail(e, setPlansState); });
    }
    return () => { alive = false; };
  }, [api, tab, round, fail]);

  async function act(work: () => Promise<unknown>, done?: string): Promise<boolean> {
    try {
      await work();
      if (done) toast.success(done);
      refresh();
      return true;
    } catch (e) { toast.error(errorSentence(e)); return false; }
  }

  async function saveAnswer(periodLabel: string, text: string) {
    try {
      await api.saveReflection(periodLabel, text);
      toast.success("Your answer is saved.");
    } catch (e) { toast.error(errorSentence(e)); }
  }

  async function dismissWaiting(w: WaitingCheckin) {
    setWaiting((all) => all.filter((x) => !(x.kind === w.kind && x.id === w.id)));
    try { await api.dismissCheckin(w.kind, w.id, w.moment); } catch (e) { toast.error(errorSentence(e)); }
  }

  useEffect(() => {
    let alive = true;
    api.settings().then((d) => { if (alive) setCustomTags(d.settings.custom_tags ?? []); }).catch(() => { /* intentional: custom tags stay empty; the list shows its own error */ });
    return () => { alive = false; };
  }, [api]);

  async function exportAs(format: "csv" | "pdf") {
    setExporting(format);
    try {
      const { blob, filename } = await api.exportFile(format, filters);
      saveBlob(blob, filename);
    } catch (e) { toast.error(errorSentence(e)); } finally { setExporting(null); }
  }

  // P2-8 J1-1: today's trades from his own broker sessions into the journal (read-only; nothing is journalled twice)
  async function syncBroker() {
    setSyncing(true);
    try {
      const r = await api.brokerSync();
      if (r.messages.length > 0 && r.added === 0 && r.closed === 0) toast.info(r.messages[0]);
      else toast.success(syncText(r.added, r.closed, r.matched));
      if (r.added > 0 || r.closed > 0) refresh();
    } catch (e) { toast.error(errorSentence(e)); } finally { setSyncing(false); }
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
      <div data-tour="journal-tabs">
      <PageTabs label="Journal" value={tab} onChange={setTab} idPrefix="journal"
        items={[{ key: "today", label: "Today" }, { key: "trades", label: "Trades" }, { key: "review", label: "Reviews", short: "Review" },
          { key: "proof", label: "My proof", short: "Proof" }, { key: "plans", label: "Plans" }, { key: "stats", label: "Stats" },
          ...(studiesOn ? [{ key: "studies" as const, label: "My studies", short: "Studies" }] : []), { key: "settings", label: "Settings" }]} />
      </div>
      <div role="tabpanel" id={`journal-panel-${tab}`} aria-labelledby={`journal-tab-${tab}`}>
        {tab === "today" && (
          <div className="space-y-space-4">
            <TodayTabV2 state={todayState} onRetry={refresh} onOpen={(kind, id) => setSheet({ kind, id })} />
            <WaitingCheckinsV2 items={waiting} onOpen={(kind, id) => setSheet({ kind, id })} onDismiss={dismissWaiting} />
            <DailyCardV2 state={daily} day={istToday()} onStep={(by) => { setDay(shiftDay(istToday(), by)); setPeriod("day"); setTab("review"); }}
              onRetry={refresh} onOpen={(kind, id) => setSheet({ kind, id })} />
          </div>
        )}
        {tab === "proof" && (
          <ProofV2 state={proofState} onRetry={refresh} onOpen={(kind, id) => setSheet({ kind, id })}
            onPin={(item) => { void act(() => api.pinProof(item.key, !item.pinned)); }}
            onAdd={(text, on) => act(() => api.addProof(text, on), "Added to My proof.")}
            onRemove={async (item) => { if (item.id != null) await act(() => api.removeProof(item.id as number), "Removed."); }} />
        )}
        {tab === "plans" && (
          <PlansV2 state={plansState} onRetry={refresh} onAdd={(body) => act(() => api.addPlan(body), "Saved.")}
            onToggle={(p) => { void act(() => api.changePlan(p.id, { active: !p.active })); }}
            onRemove={async (p) => { await act(() => api.removePlan(p.id), "Removed."); }} />
        )}
        {tab === "trades" && <WaitingCheckinsV2 items={waiting} onOpen={(kind, id) => setSheet({ kind, id })} onDismiss={dismissWaiting} />}
        {tab === "trades" && (
          <JournalListV2 state={list} filters={filters} onFilters={changeFilters} api={api} onRetry={refresh}
            onOpen={(r) => setSheet({ kind: r.kind, id: r.id })} onAddManual={() => setManualOpen(true)} onImport={() => setImportOpen(true)}
            onSyncBroker={syncBroker} syncing={syncing} onMore={() => setLimit((l) => l + PAGE)}
            exportButtons={<ExportButtonsV2 onExport={exportAs} busy={exporting} />} />
        )}
        {tab === "stats" && <JournalStatsV2 state={stats} onRetry={refresh} />}
        {tab === "review" && (
          <div className="space-y-space-4">
            <PeriodSwitchV2 value={period} onChange={setPeriod} />
            {period === "day" && <DailyCardV2 state={daily} day={day} onStep={(by) => setDay((d) => shiftDay(d, by))} onRetry={refresh}
              onOpen={(kind, id) => setSheet({ kind, id })} />}
            {period === "week" && (
              <>
                <WeeklyReviewV2 state={weekState} week={week} onWeek={(o) => setWeek((w) => shiftWeek(w, o))} onRetry={refresh}
                  onOpen={(kind, id) => setSheet({ kind, id })} acceptedOn={acceptedOn} />
                <WeeklyExtrasV2 state={extras} onRetry={refresh} onOpen={(kind, id) => setSheet({ kind, id })} onAnswer={saveAnswer} />
              </>
            )}
            {period === "month" && <MonthlyV2 state={monthly} month={month} onStep={(by) => setMonth((m) => shiftMonth(m, by))} onRetry={refresh}
              onAnswer={saveAnswer} />}
          </div>
        )}
        {tab === "studies" && studiesOn && <MyStudiesV2 />}
        {tab === "settings" && (
          <div className="space-y-space-4">
            <OwnListsV2 api={api} onSaved={(d) => setCustomTags(d.settings.custom_tags ?? [])} />
            <DeliveryV2 api={api} />
            {settings}
          </div>
        )}
      </div>
      <EntrySheetV2 target={sheet} api={api} customTags={customTags} onClose={() => setSheet(null)} onChanged={refresh}
        extra={recallOn ? (e) => (e.kind === "trade" ? <SimilarSetupsV2 kind="trade" id={e.id} /> : null) : undefined} />
      <ImportTradesSheetV2 open={importOpen} api={api} onClose={() => setImportOpen(false)} onImported={refresh} />
      <ManualTradeSheetV2 open={manualOpen} api={api} onClose={() => setManualOpen(false)} onSaved={(e) => { refresh(); setSheet({ kind: "manual", id: e.id }); }} />
    </div>
  );
}
