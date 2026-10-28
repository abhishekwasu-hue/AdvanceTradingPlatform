import { ArrowDownRight, ArrowUpRight, ChartCandlestick, Layers, Plus, SlidersHorizontal } from "lucide-react";
import { Suspense, lazy, useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import ContractNoteCard from "../components/ContractNoteCard";
import PaperAccountCard from "../components/PaperAccountCard";
import PaperTicketCard from "../components/PaperTicketCard";
import { CardV2, ChipV2, EmptyStateV2, LoadFailedV2, LoadingRowsV2, toneClass } from "../dashboard/CardV2";
import { useColourV2 } from "../ui/colourV2";
import { DirectionChip } from "../ui/DirectionChip";
import { signTone } from "../dashboard/model";
import { dataOf, usePolled, type Loadable } from "../dashboard/useDashboardData";
import {
  addSide, adjustHref, contractsApi, countdown, groupRows, groupTitle, isFnoPosition, known, positionItem, positionTarget, type ContractFacts, type GroupedLegs,
  type PositionGroup,
} from "../fno/contracts";
import { CaptureLineV2, CompareButtonV2, StructureAlertV2 } from "../fno/PositionInsightsV2";
import { posLeg, type PosLeg } from "../fno/positionInsights";
import { canExitPart, canReverse } from "../fno/positionActions";
import type { CockpitUnit } from "../fno/cockpit";
import type { Side } from "../fno/api";
import type { TicketTarget } from "../fno/ticketModel";
import { itemKey } from "../chart/watchlistsV2";
import DecisionToolsCard from "../progression/DecisionToolsCard";
import ExperienceCard from "../progression/ExperienceCard";
import type { PageProps } from "../routes";
import { chartsPath } from "../shell/shellRoutes";
import type { PositionGreeks, TradeRecord } from "../types";
import { BrokerGateView, CONNECT_BROKER_PATH, SIGN_IN_PATH, useBrokerLogin } from "../ui/BrokerGate";
import { useBrokerSession } from "../ui/brokerSession";
import { EMPTY, formatINR, formatNumber, formatPct, formatPrice, formatSignedINR, formatTimeIST } from "../ui/format";
import { PageIntro } from "../ui/PageIntro";
import { PageFootnote } from "../ui/numbersFirst";
import { TermHint } from "../ui/TermHint";
import { useFeatures } from "../ui/useFeatures";
import { liveHiddenFrom } from "../settings/useLiveHidden";
import PageDishV2 from "../growth/PageDishV2";
import {
  closedRows, closedText, exitConfirmText, failedText, heldRows, holdingTiles, openCaption, type ClosedRow, type HeldRow,
} from "./model";
import { BUTTON_SECONDARY, FIELD, NoteV2, PageTitleV2, TileV2, TilesV2 } from "./PartsV2";
import { PracticeFrame } from "../fno/OrderTicketV2";
import { AccountFilterV2, accountLabels, inAccount } from "./accountViews";
import { SettlementWarningsV2 } from "../fno/SmartExecutionV2";
import IncompleteOrdersV2 from "../fno/IncompleteOrdersV2";
import { CombinedExitBarV2, ExitShortSheetV2, useCombinedExits } from "../fno/CombinedExitV2";
import { exitParam } from "../fno/combinedExit";
import { GroupExitSheetV2 } from "../fno/GroupExitsV2";
import type { GroupExitAsk } from "../fno/groupExits";

// N1 F3 (flag portfolio_risk): the portfolio risk card is its own chunk, loaded only while the flag is on.
const PortfolioRiskCard = lazy(() => import("../portfolio/PortfolioRiskCard"));
// FNO-4: the ONE order ticket (PAPER), loaded only when Add is tapped on an F&O position.
const OrderTicketV2 = lazy(() => import("../fno/OrderTicketV2"));
// 8b-g: "Compare a change", loaded only when it is opened.
const AdjustCompareV2 = lazy(() => import("../fno/CompareV2").then((m) => ({ default: m.AdjustCompareV2 })));
// 8b slice 3: the F&O cockpit (read only) and the auto journal drafts, loaded only while F&O PAPER trading is on.
// 8b slice 4: the cockpit as a desk (chart + chain + basket beside it on a desktop; a quick switch on a phone).
const CockpitDeskV2 = lazy(() => import("../fno/CockpitDeskV2"));
const JournalDraftsV2 = lazy(() => import("../fno/JournalDraftsV2"));

const REFRESH_MS = 30_000;
const every = (ms: number) => () => ms;
const CLOSED_SHOWN = 10;

type Notice = { kind: "done" | "info" | "warn"; text: string } | null;

/**
 * UI2-9a (UI v2 proposal §2 row 14, §8, §9, §13 UI-11): My positions - "What do I hold and how is it doing?". Shown only
 * while `ui_v2` is on (the v2 shell picks it for /positions, src/shell/pagesV2.ts); with the flag off today's
 * PositionsPage renders exactly as before.
 *
 * Top to bottom: the summary tiles (open positions, open P&L, booked today, risk at the stops), the open positions as a
 * list (no table, so nothing scrolls sideways on a phone), the option Greeks when an option is held, the closed trades,
 * then the paper account and its tools (today's cards, unchanged, each behind its own flag).
 *
 * Exits (ADR-0004): "Exit now" is a neutral button (UI-11) that keeps today's handler and confirm exactly, is never
 * disabled by this page, and stays reachable in every broker state. This page shows the broker states itself (it is in
 * OWN_BROKER_STATES): while no broker session is usable (or still being checked) the gate says what is missing, and below
 * it the trader's own open positions - their real records at the entry price, with no price or P&L columns and nothing
 * made up - keep their exit buttons, and the paper account keeps "Pause new PAPER entries". The
 * page places no entry order of its own and the AI never places one (ADR-0006).
 */
export default function PositionsV2(_props: PageProps = {}) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const features = useFeatures();
  // Z3: "Exit now" exists only with approval-only entries or the PAPER account on (otherwise exits run automatically).
  const exitNowOn = features.on("approval_only_entries") || features.on("paper_account");
  const portfolioRiskOn = features.on("portfolio_risk");
  const canTrade = !!user && user.role !== "VIEWER";
  const liveHidden = liveHiddenFrom(features);

  const { state: broker, refresh: refreshBroker } = useBrokerSession();
  const connected = broker.kind === "connected";
  const signedIn = !!user;
  const [positions, reloadPositions] = usePolled(api.listPositions, every(REFRESH_MS), signedIn);
  const [trades, reloadTrades] = usePolled(api.listTrades, every(REFRESH_MS * 2), signedIn && connected);
  const [exposure, reloadExposure] = usePolled(() => api.portfolioExposure(true), every(REFRESH_MS), signedIn && connected);
  const reload = () => { reloadPositions(); reloadTrades(); reloadExposure(); };
  const login = useBrokerLogin(() => { refreshBroker(); reload(); });
  const [notice, setNotice] = useState<Notice>(null);
  // M45 MB-7 (flags `ui_v2` + `multi_account`): the simulated PAPER account of each row and a filter by account.
  const accountsOn = features.on("multi_account");
  const [account, setAccount] = useState<string | null>(null);

  const list = dataOf(positions);
  const rows = useMemo(() => heldRows(list ?? [], dataOf(exposure)), [list, exposure]);

  // Z3 "Exit now": closes a PAPER position at once, at the live price or the last price seen - never a typed price.
  // LIVE positions are closed in the broker app. Today's handler and confirm, unchanged (UI-11).
  async function exitNow(p: TradeRecord) {
    if (!window.confirm(exitConfirmText(p))) return;
    try {
      const result = await api.exitNow(p.id);
      const first = result.closed[0];
      setNotice({ kind: "done", text: `${p.symbol}: ${closedText(first?.exit_price, first?.pnl, first?.exit_reason)}` });
      reload();
    } catch (e) {
      setNotice({ kind: "warn", text: failedText(`${p.symbol} was not closed.`, e) });
    }
  }

  async function checkPrice(p: TradeRecord, price: number) {
    try {
      const result = await api.markPrice(p.id, price);
      setNotice(result.closed
        ? { kind: "done", text: `${p.symbol}: ${closedText(result.exit_price, result.pnl, result.exit_reason)}` }
        : { kind: "info", text: `${p.symbol} is still open: ${formatPrice(price)} does not reach its stop or target.` });
      if (result.closed) reload();
    } catch (e) {
      setNotice({ kind: "warn", text: failedText(`We could not check the price of ${p.symbol}.`, e) });
    }
  }

  const byId = new Map((list ?? []).map((p) => [p.id, p]));
  const labels = accountsOn ? accountLabels(list) : [];
  const chosen = account !== null && labels.includes(account) ? account : null;
  const shownRows = chosen === null ? rows : rows.filter((r) => inAccount(byId.get(r.id), chosen));
  const accountFilter = <AccountFilterV2 labels={labels} value={chosen} onChange={setAccount} />;
  // FNO-4 (owner order section 8): Add / Adjust on F&O PAPER positions and the legs placed together, with the PAPER
  // account on (the ticket's own routes answer 404 without it). Exit stays the handler above, never blocked here.
  const fnoOn = features.on("paper_account") && canTrade;
  const fno = useFnoPositions(list, fnoOn && signedIn);
  const [ticket, setTicket] = useState<{ target: TicketTarget; side: Side } | null>(null);
  // 8b slice 2: "Tell me when" (an alert of the existing alert rules, `screener_v2`) and "Compare a change" - information only.
  const [compare, setCompare] = useState<CompareTarget | null>(null);
  // TT-8: "Exit this leg" / "Exit the whole group" on the legs of one basket - exits, never disabled here (ADR-0004).
  const [groupExit, setGroupExit] = useState<GroupExitAsk | null>(null);
  const groupSheet = (
    <GroupExitSheetV2 ask={groupExit} onClose={() => setGroupExit(null)}
      onDone={(text, ok) => { setGroupExit(null); setNotice({ kind: ok ? "done" : "warn", text }); reload(); }} />
  );
  // CX6: "Set combined stop-loss" and the active combined exits as chips (PAPER account on; the routes answer 404 otherwise).
  const combined = useCombinedExits(fnoOn && signedIn);
  // CX-F2: the CRITICAL notice's "Exit short now" opens /positions?exit=<id>: one confirm, then that position's own exit
  // (never blocked, ADR-0004). Shown in every broker state, like every exit on this page.
  const location = useLocation();
  const exitId = exitParam(location.search);
  const [shortAsk, setShortAsk] = useState<TradeRecord | null>(null);
  const [asked, setAsked] = useState<number | null>(null);
  useEffect(() => {
    if (exitId === null || asked === exitId || !canTrade || positions.status !== "ok") return;
    setAsked(exitId);
    const p = (list ?? []).find((x) => x.id === exitId);
    if (p) setShortAsk(p);
    else setNotice({ kind: "info", text: "That position is already closed." });
  }, [exitId, asked, canTrade, positions.status, list]);
  const shortSheet = (
    <ExitShortSheetV2 trade={shortAsk} onClose={() => setShortAsk(null)}
      onDone={(text, ok) => { setShortAsk(null); setNotice({ kind: ok ? "done" : "warn", text }); reload(); }} />
  );
  const actions: RowActions = {
    exitNowOn, canTrade, onExit: exitNow, onCheck: checkPrice, accounts: accountsOn,
    group: fnoOn ? { onLeg: (p, hedge) => setGroupExit({ kind: "leg", tradeId: p.id, symbol: p.symbol, hedge, warning: null }),
      onGroup: (tradeId, title, legs) => setGroupExit({ kind: "group", tradeId, title, legs }) } : undefined,
    fno: fnoOn ? { facts: fno.facts, onAdd: (target, side) => setTicket({ target, side }), alertsOn: features.on("screener_v2"), onCompare: setCompare } : undefined,
  };

  if (!connected) {
    return (
      <div className="mx-auto w-full space-y-space-4 md:space-y-space-6" data-page="positions-v2">
        <PageTitleV2 title="My positions" subtitle="What you hold now and how it is doing." />
        <div>
          <BrokerGateView state={broker} onConnect={() => navigate(CONNECT_BROKER_PATH)} onSignIn={() => navigate(SIGN_IN_PATH)}
            onLogin={login.login} onRetry={refreshBroker} busy={login.busy} error={login.error} codeStep={login.codeStep} onSubmitCode={login.submitCode}
            positionsShown={signedIn} />
        </div>
        {signedIn && (
          // Shown whatever the broker state - even while the broker check is still running or never answers - so the
          // trader's own paper positions keep their exits and the paper account keeps its pause (ADR-0004).
          <div className="space-y-space-4">
            {notice && <NoteV2 kind={notice.kind} testId="action">{notice.text}</NoteV2>}
            <CardV2 title="Your open positions" count={list?.length} testId="exits">
              {positions.status === "loading" ? <LoadingRowsV2 /> : positions.status === "error" ? <LoadFailedV2 what="your positions" /> : rows.length === 0 ? (
                <EmptyStateV2 text="All positions are closed." />
              ) : (
                <>
                  <p className="mb-space-3 text-t13 text-fg-muted">
                    Live prices and profit or loss show once your broker is connected.{exitNowOn && canTrade ? " You can still exit a paper position here." : ""}
                  </p>
                  {accountFilter}
                  <PositionList rows={shownRows} byId={byId} actions={actions} prices={false} groups={fno.groups} />
                  {groupSheet}
                </>
              )}
            </CardV2>
            <PaperAccountCard canTrade={canTrade} />
          </div>
        )}
        {shortSheet}
      </div>
    );
  }

  const tiles = holdingTiles(list, dataOf(exposure));
  // 8b-i: the F&O cockpit's lines - legs placed together as one, the rest one by one (F&O PAPER positions only).
  const cockpit = fnoOn ? cockpitUnits(rows, byId, actions, fno.groups) : [];
  const hasOptions = (list ?? []).some((p) => p.instrument_kind === "OPTION");

  return (
    <div className="mx-auto w-full space-y-space-4 md:space-y-space-6" data-page="positions-v2">
      <PageIntro id="positions" lines={["Open a position's chart, or exit a paper position at the current price."]} />
      <PageTitleV2 title="My positions" subtitle="What you hold now and how it is doing."
        action={!!list?.length && list.every((p) => p.mode === "PAPER") ? <PracticeFrame className="self-start md:self-auto" /> : undefined} />

      <section aria-label="Summary" data-block="summary" data-tour="positions-summary">
        {positions.status === "loading" ? <LoadingRowsV2 rows={2} /> : (
          <TilesV2>
            <TileV2 testId="open" label="Open positions" value={positions.status === "ok" ? String(tiles.open) : EMPTY} sub={positions.status === "ok" ? openCaption(tiles, liveHidden) : undefined} />
            <TileV2 testId="open-pnl" label="Open P&L" hint="open-pnl" value={formatSignedINR(tiles.openPnl)} tone={signTone(tiles.openPnl)}
              sub={tiles.open === 0 ? undefined : tiles.openPnl === null ? "waiting for live prices" : "at live prices"} />
            <TileV2 testId="booked" label="Booked today" hint="booked-pnl" value={formatSignedINR(tiles.booked)} tone={signTone(tiles.booked)}
              sub={tiles.booked === null ? undefined : "closed trades since the open"} />
            <TileV2 testId="risk" label="Risk at stops" hint="risk-at-stops" value={formatINR(tiles.riskAtStops)}
              sub={tiles.riskPct === null ? undefined : `${formatNumber(tiles.riskPct, 1)}% of capital`} />
          </TilesV2>
        )}
      </section>

      {/* TM Part 7 (flag smart_execution): stock F&O that may settle by delivery, flagged ahead (information only). */}
      {features.on("smart_execution") && signedIn && <SettlementWarningsV2 />}
      {/* SE-7 (flag smart_execution): a multi-leg order that stopped half way - his three taps and his rule's countdown. */}
      {features.on("smart_execution") && signedIn && <IncompleteOrdersV2 />}

      {/* MO4 slice 4: the page's ready-made dish (ui_v2 + growth_v2): his risk in plain words, what is coming up. */}
      {features.on("growth_v2") && <PageDishV2 page="positions" />}

      {notice && <NoteV2 kind={notice.kind} testId="action">{notice.text}</NoteV2>}

      {cockpit.length > 0 && (
        <Suspense fallback={null}>
          <CockpitDeskV2 units={cockpit} alertsOn={features.on("screener_v2")} />
        </Suspense>
      )}

      <CardV2 title="Open positions" count={list?.length} testId="positions" tour="positions-list">
        {positions.status === "loading" ? <LoadingRowsV2 /> : positions.status === "error" ? <LoadFailedV2 what="your positions" /> : rows.length === 0 ? (
          <EmptyStateV2 text="All positions are closed. Find a setup or let Autopilot trade for you." action={{ label: "Find setups", to: "/screener" }} />
        ) : (
          <>
            {fnoOn && (
              <div className="mb-space-3">
                <CombinedExitBarV2 positions={list ?? []} facts={fno.facts} exits={combined.list}
                  onChanged={() => { combined.reload(); reload(); }} onNote={(text, ok) => setNotice({ kind: ok ? "done" : "warn", text })} />
              </div>
            )}
            {accountFilter}
            <PositionList rows={shownRows} byId={byId} actions={actions} groups={fno.groups} />
            {groupSheet}
          </>
        )}
      </CardV2>

      {shortSheet}
      {ticket && (
        <Suspense fallback={null}>
          <OrderTicketV2 open target={ticket.target} side={ticket.side} onClose={() => setTicket(null)} onPlaced={() => reload()} />
        </Suspense>
      )}
      {compare && (
        <Suspense fallback={null}>
          <AdjustCompareV2 open title={compare.title} legs={compare.legs} adjust={compare.adjust} onClose={() => setCompare(null)} />
        </Suspense>
      )}

      {hasOptions && <GreeksCardV2 symbolOf={(id) => byId.get(id)?.symbol} />}

      <ClosedTradesV2 trades={trades} onSaved={reloadTrades} />

      {fnoOn && (
        <Suspense fallback={null}>
          <JournalDraftsV2 refreshKey={list?.length ?? 0} />
        </Suspense>
      )}

      <section aria-labelledby="paper-tools" className="space-y-space-4" data-block="tools">
        <h2 id="paper-tools" data-tour="positions-tools" className="text-t15 font-semibold text-fg">Paper account and tools</h2>
        <PaperAccountCard canTrade={canTrade} />
        <ExperienceCard canTrade={canTrade} />
        {portfolioRiskOn && <Suspense fallback={null}><PortfolioRiskCard /></Suspense>}
        <DecisionToolsCard />
        <PaperTicketCard canTrade={canTrade} onPlaced={reload} />
        {canTrade && <ContractNoteCard onApplied={reload} />}
      </section>

      {/* UX-11 U5: the page's one footer line (the cards no longer repeat it). UX-11b: no "Phase 0" jargon. */}
      <PageFootnote />
    </div>
  );
}

export interface RowActions {
  exitNowOn: boolean;
  canTrade: boolean;
  onExit: (p: TradeRecord) => void;
  onCheck: (p: TradeRecord, price: number) => void;
  /** M45 MB-7 (`multi_account`): show the row's simulated PAPER account as a badge. Absent: the row is exactly as before. */
  accounts?: boolean;
  /** TT-8: the legs of one basket - "Exit this leg" (`hedge`: it protects an open sold leg, so the sheet warns first) and
   * "Exit the whole group". Absent: grouped legs keep "Exit now" exactly as before. */
  group?: { onLeg: (p: TradeRecord, hedge: boolean) => void; onGroup: (tradeId: number, title: string, legs: number) => void };
  /** FNO-4: Add / Adjust on an F&O PAPER position (absent: the row is exactly as before). 8b slice 2: the premium kept,
   * "Tell me when" (`alertsOn`: the alert rules' flag) and "Compare a change" - information only, never in place of Exit. */
  fno?: {
    facts: ReadonlyMap<string, ContractFacts>; onAdd: (target: TicketTarget, side: Side) => void; alertsOn?: boolean; onCompare?: (target: CompareTarget) => void;
  };
  /** Terminal T2 (TT-9): "Exit part" (some of the lots) and "Reverse" on a PAPER position (absent: the row is as before).
   *  Exit part is an exit: never disabled here (ADR-0004). */
  part?: { onPart: (p: TradeRecord) => void; onReverse: (p: TradeRecord) => void };
}

export interface CompareTarget { title: string; legs: PosLeg[]; adjust: string | null }

/** The F&O PAPER legs of these rows as the 8b features read them; null unless every one is known and still trading. */
function fnoLegs(rows: readonly HeldRow[], byId: Map<number, TradeRecord>, actions: RowActions): PosLeg[] | null {
  if (!actions.fno) return null;
  const out: PosLeg[] = [];
  for (const r of rows) {
    const p = byId.get(r.id);
    if (!p || p.mode !== "PAPER" || !isFnoPosition(p)) return null;
    const leg = posLeg(p, actions.fno.facts.get(itemKey(positionItem(p))), r.ltp);
    if (!leg) return null;
    out.push(leg);
  }
  return out.length ? out : null;
}

/** 8b-i: the cockpit's lines from the rows on screen: each group of legs placed together, then each F&O PAPER position on
 * its own (an equity row, or a contract not known yet, is left out). */
function cockpitUnits(rows: readonly HeldRow[], byId: Map<number, TradeRecord>, actions: RowActions, groups: readonly PositionGroup[]): CockpitUnit[] {
  const { grouped, single } = groupRows(rows, groups);
  const out: CockpitUnit[] = [];
  for (const g of grouped) {
    const legs = fnoLegs(g.legs, byId, actions);
    if (legs) out.push({ key: g.group.key, title: groupTitle(g), legs, pnl: g.pnl });
  }
  for (const r of single) {
    const legs = fnoLegs([r], byId, actions);
    if (legs) out.push({ key: `trade:${r.id}`, title: legs[0].label, legs, pnl: r.pnl });
  }
  return out;
}

const daysLeftOf = (facts: ContractFacts | undefined): string | null => (known(facts) ? countdown(facts.days_left) : null);

/** FNO-4: what the F&O PAPER positions are (for Add / Adjust) and which legs were placed together (one basket, one
 * structure). Read only; any failure reads as "nothing known", so the list is as before. */
export function useFnoPositions(list: TradeRecord[] | null | undefined, on: boolean) {
  const items = useMemo(() => (list ?? []).filter((p) => p.mode === "PAPER" && isFnoPosition(p)).map(positionItem), [list]);
  const key = items.map(itemKey).sort().join("|");
  const [facts, setFacts] = useState<ReadonlyMap<string, ContractFacts>>(new Map());
  useEffect(() => {
    if (!on || !key) { setFacts(new Map()); return undefined; }
    let live = true;
    contractsApi.lookup(items.slice(0, 60)).then((r) => { if (live) setFacts(new Map((r?.items ?? []).map((f) => [itemKey(f), f]))); },
      () => { if (live) setFacts(new Map()); });
    return () => { live = false; };
  }, [on, key]);                                                        // `items` is read through `key` (same set, same call)
  const [groupsState] = usePolled(contractsApi.groups, every(REFRESH_MS), on && items.length > 1);
  const groups: PositionGroup[] = on ? dataOf(groupsState)?.groups ?? [] : [];
  return { facts, groups };
}

/** TT-8: a basket's legs get "Exit this leg" / "Exit the whole group" (a strategy structure keeps "Exit now", which closes
 * it whole). */
function groupExits(group: PositionGroup, actions: RowActions): boolean {
  return group.kind === "basket" && !!actions.group && actions.canTrade;
}

/** `prices: false` (no broker session): no LTP or P&L columns at all - an entry-price row with nothing made up.
 * FNO-4: legs placed together show together under one line with their combined P&L; every leg keeps its own Exit. */
export function PositionList({ rows, byId, actions, prices = true, groups = [] }: {
  rows: HeldRow[]; byId: Map<number, TradeRecord>; actions: RowActions; prices?: boolean; groups?: PositionGroup[];
}) {
  const { grouped, single } = groupRows(rows, groups);
  const row = (r: HeldRow, inGroup = false, group?: PositionGroup) => {
    const p = byId.get(r.id);
    const groupLeg = group && groupExits(group, actions) ? { hedge: (group.hedge_ids ?? []).includes(r.id) } : undefined;
    return p ? <PositionRowV2 key={r.id} row={r} position={p} actions={actions} prices={prices} inGroup={inGroup} groupLeg={groupLeg} /> : null;
  };
  return (
    <div className="space-y-space-3">
      {grouped.map((g) => (
        <section key={g.group.key} aria-label={groupTitle(g)} data-group={g.group.kind}
          className="rounded-control border border-border px-space-3 py-space-2">
          <div className="flex flex-wrap items-center justify-between gap-space-2 border-b border-subtle pb-space-2">
            <span className="inline-flex items-center gap-space-2 text-t14 font-semibold text-fg">
              <Layers size={16} aria-hidden className="text-fg-muted" />{groupTitle(g)}
            </span>
            <span className="flex flex-wrap items-center gap-space-3">
              {prices && (
                <span className="text-right" data-col="group-pnl">
                  <span className="mr-space-2 text-t12 text-fg-muted">Combined P&amp;L</span>
                  <span className={`font-tabular text-t15 font-semibold ${toneClass(signTone(g.pnl))}`}>{g.pnl === null ? EMPTY : formatSignedINR(g.pnl)}</span>
                </span>
              )}
              {groupExits(g.group, actions) && (
                // TT-8: closes every leg, the sold legs first. A neutral button, never disabled (ADR-0004).
                <button type="button" onClick={() => actions.group?.onGroup(g.legs[0].id, groupTitle(g), g.legs.length)} data-testid="exit-group"
                  className={BUTTON_SECONDARY}>
                  Exit the whole group
                </button>
              )}
            </span>
          </div>
          {prices && <GroupInsights group={g} byId={byId} actions={actions} />}
          <ul className="divide-y divide-subtle">{g.legs.map((r) => row(r, true, g.group))}</ul>
        </section>
      ))}
      {single.length > 0 && <ul className="divide-y divide-subtle">{single.map((r) => row(r))}</ul>}
    </div>
  );
}

/** 8b slice 2 on legs placed together: the premium kept (a net premium received), "Tell me when" on the group's own
 * breakevens and short strikes, and "Compare a change". Shown only when every leg is a known F&O PAPER contract. */
function GroupInsights({ group, byId, actions }: { group: GroupedLegs<HeldRow>; byId: Map<number, TradeRecord>; actions: RowActions }) {
  const legs = fnoLegs(group.legs, byId, actions);
  if (!legs || !actions.fno) return null;
  const firstTrade = byId.get(legs[0].id);
  const facts = firstTrade ? actions.fno.facts.get(itemKey(positionItem(firstTrade))) : undefined;
  const onCompare = actions.fno.onCompare;
  return (
    <div className="mb-space-3 border-b border-subtle pb-space-3" data-group-insights>
      <CaptureLineV2 legs={legs} daysLeft={daysLeftOf(facts)} />
      <div className="mt-space-2 flex flex-wrap items-center gap-space-2">
        {actions.fno.alertsOn && actions.canTrade && <StructureAlertV2 legs={legs} />}
        {onCompare && <CompareButtonV2 onOpen={() => onCompare({ title: groupTitle(group), legs, adjust: adjustHref(facts) })} />}
      </div>
    </div>
  );
}

export function PositionRowV2({ row, position, actions, prices = true, inGroup = false, groupLeg }: {
  row: HeldRow; position: TradeRecord; actions: RowActions; prices?: boolean; inGroup?: boolean;
  /** TT-8: a leg of one basket - "Exit this leg" in place of "Exit now" (`hedge`: it protects an open sold leg). */
  groupLeg?: { hedge: boolean };
}) {
  const tone = toneClass(signTone(row.pnl));
  const showExit = !groupLeg && actions.exitNowOn && actions.canTrade && position.mode === "PAPER";
  // FNO-4: Add (the ticket on the same contract and side, PAPER) and Adjust (the chain of its expiry) - only for an F&O
  // PAPER position whose contract is known and still trading. Neither is ever shown in place of Exit.
  const facts = actions.fno && position.mode === "PAPER" && isFnoPosition(position) ? actions.fno.facts.get(itemKey(positionItem(position))) : undefined;
  const target = actions.fno ? positionTarget(position, facts, row.ltp) : null;
  const adjust = actions.fno ? adjustHref(facts) : null;
  // 8b slice 2 on a position of its own (legs placed together carry them on the group): the premium kept and "Tell me when".
  const own = !inGroup && prices ? fnoLegs([row], new Map([[position.id, position]]), actions) : null;
  const colour = useColourV2();          // colour v2: Long / Short as chips
  return (
    <li className="py-space-3 first:pt-0 last:pb-0" data-position={row.id}>
      <div className="flex items-start gap-space-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-space-2">
            <span className="text-t15 font-semibold text-fg">{row.symbol}</span>
            {colour ? <DirectionChip side={row.side === "Buy" ? "long" : "short"} /> : (
              <span className={`inline-flex items-center gap-px text-t13 font-medium ${row.side === "Buy" ? "text-up" : "text-down"}`}>
                {row.side === "Buy" ? <ArrowUpRight size={14} aria-hidden /> : <ArrowDownRight size={14} aria-hidden />}{row.side === "Buy" ? "Long" : "Short"}
              </span>
            )}
            <ChipV2 kind={row.mode === "Live" ? "solid" : "outline"}>{row.mode}</ChipV2>
            {row.structure && <ChipV2>{row.structure}</ChipV2>}
            {actions.accounts && position.account_label && (
              <span data-testid="account-badge" title="Practice account"><ChipV2 kind="brand">{position.account_label}</ChipV2></span>
            )}
          </div>
          <p className="mt-px text-t13 text-fg-muted">{row.detail}</p>
          <p className="mt-px font-tabular text-t13 text-fg">
            {row.levelsOn && <span className="text-fg-muted">{row.levelsOn} </span>}
            {row.levels.map((l, i) => (
              <span key={l.label}>{i > 0 && <span className="text-fg-muted"> · </span>}<span className="text-fg-muted">{l.label} </span><span className={toneClass(l.tone)}>{l.value}</span></span>
            ))}
          </p>
          {row.structureNote && <p className="mt-px text-t12 text-fg-muted">{row.structureNote}</p>}
          {row.strategy && <p className="mt-px text-t12 text-fg-muted">{row.strategy}</p>}
          {own && <CaptureLineV2 legs={own} daysLeft={daysLeftOf(facts)} />}
        </div>
        {prices && (
          <>
            <div className="hidden w-28 shrink-0 text-right sm:block" data-col="ltp">
              <div className="flex items-center justify-end gap-space-1 text-t12 text-fg-muted">LTP</div>
              <div className="font-tabular text-t15 font-medium text-fg">{formatPrice(row.ltp)}</div>
            </div>
            <div className="w-28 shrink-0 text-right" data-col="pnl">
              <div className="text-t12 text-fg-muted">P&amp;L</div>
              <div className={`font-tabular text-t15 font-semibold ${tone}`}>{formatSignedINR(row.pnl)}</div>
              <div className={`font-tabular text-t13 ${tone}`}>{formatPct(row.pnlPct)}</div>
            </div>
          </>
        )}
      </div>
      <div className="mt-space-2 flex flex-wrap items-center gap-space-2">
        {showExit && (
          // UI-11: a neutral button; today's confirm runs before anything is sent. Never disabled here (ADR-0004).
          <button type="button" onClick={() => actions.onExit(position)} data-testid="exit-now" className={BUTTON_SECONDARY}>
            Exit now
          </button>
        )}
        {groupLeg && (
          // TT-8: this leg only; a hedge before its sold leg gets a plain warning in the sheet, never a block (ADR-0004).
          <button type="button" onClick={() => actions.group?.onLeg(position, groupLeg.hedge)} data-testid="exit-leg" className={BUTTON_SECONDARY}>
            Exit this leg
          </button>
        )}
        {actions.part && actions.canTrade && canExitPart(position) && (
          <button type="button" onClick={() => actions.part?.onPart(position)} data-testid="exit-part" className={BUTTON_SECONDARY}>
            Exit part
          </button>
        )}
        {actions.part && actions.canTrade && canReverse(position) && (
          <button type="button" onClick={() => actions.part?.onReverse(position)} data-testid="reverse" className={BUTTON_SECONDARY}>
            Reverse
          </button>
        )}
        {!actions.exitNowOn && !groupLeg && <CheckPriceV2 position={position} onCheck={actions.onCheck} />}
        {target && actions.fno && (
          <button type="button" onClick={() => actions.fno?.onAdd(target, addSide(position))} data-testid="fno-add" className={BUTTON_SECONDARY}>
            <Plus size={16} aria-hidden />Add
          </button>
        )}
        {adjust && (
          <Link to={adjust} data-testid="fno-adjust" className={BUTTON_SECONDARY} title="Roll the strike or add a hedge on the chain">
            <SlidersHorizontal size={16} aria-hidden />Adjust
          </Link>
        )}
        <Link to={chartsPath(row.chartSymbol)} className={BUTTON_SECONDARY}>
          <ChartCandlestick size={16} aria-hidden />Chart
        </Link>
        {own && actions.fno?.alertsOn && actions.canTrade && <StructureAlertV2 legs={own} />}
        {actions.exitNowOn && position.mode === "LIVE" && <span className="text-t12 text-fg-muted">Close a live position in your broker&apos;s app.</span>}
      </div>
    </li>
  );
}

/** Z3 (review V3): with Exit now off, a price checked by hand closes the position if it reaches the stop or target. */
function CheckPriceV2({ position, onCheck }: { position: TradeRecord; onCheck: (p: TradeRecord, price: number) => void }) {
  const [raw, setRaw] = useState("");
  const id = useId();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const price = Number(raw);
    if (!raw || Number.isNaN(price)) return;
    onCheck(position, price);
  };
  return (
    <form onSubmit={submit} className="flex items-center gap-space-2">
      <label htmlFor={id} className="sr-only">Current price of {position.symbol}</label>
      <input id={id} type="number" inputMode="decimal" placeholder="Current price" value={raw} onChange={(e) => setRaw(e.target.value)}
        className={`${FIELD} w-36`} />
      <button type="submit" className={BUTTON_SECONDARY}>Check price</button>
    </form>
  );
}

// UX-10 (UI audit H3): the method line lives in the (?) next to the title; a left-out position is named by its symbol.
function GreeksCardV2({ symbolOf }: { symbolOf: (tradeId: number) => string | undefined }) {
  const [greeks, setGreeks] = useState<PositionGreeks | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true); setError(null);
    try { setGreeks(await api.positionGreeks()); } catch (e) { setError(failedText("We could not work out the Greeks.", e)); } finally { setBusy(false); }
  }
  return (
    <CardV2 title={<>Option Greeks <TermHint term="greeks" /></>} testId="greeks">
      <div className="flex flex-col gap-space-3 sm:flex-row sm:items-center sm:justify-end">
        <button type="button" onClick={load} disabled={busy} className={BUTTON_SECONDARY}>{busy ? "Working out…" : greeks ? "Work out again" : "Work out the Greeks"}</button>
      </div>
      {error && <div className="mt-space-3"><NoteV2 kind="warn">{error}</NoteV2></div>}
      {greeks && (
        <div className="mt-space-3 space-y-space-3">
          <TilesV2>
            <TileV2 label="Net delta" value={formatNumber(greeks.net.net_delta, 2)} />
            <TileV2 label="Net gamma" value={formatNumber(greeks.net.net_gamma, 4)} />
            <TileV2 label="Net theta a day" value={formatNumber(greeks.net.net_theta, 2)} />
            <TileV2 label="Net vega" value={formatNumber(greeks.net.net_vega, 2)} />
          </TilesV2>
          <ul className="divide-y divide-subtle">
            {greeks.legs.map((l) => (
              <li key={l.trade_id} className="flex flex-wrap items-baseline justify-between gap-x-space-3 gap-y-px py-space-2 text-t13">
                <span className="font-semibold text-fg">{l.symbol}{l.leg_role ? <span className="font-normal text-fg-muted"> · {l.leg_role.toLowerCase()}</span> : null}</span>
                <span className="font-tabular text-fg-muted">
                  Qty {formatNumber(l.quantity)} · premium {formatPrice(l.premium)} · IV {formatNumber(l.implied_volatility * 100, 1)}% · delta {formatNumber(l.position_delta, 2)}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-t12 text-fg-muted">
            As of {formatTimeIST(greeks.as_of)}.{greeks.skipped.length > 0 ? ` Left out: ${greeks.skipped.map((k) => `${symbolOf(k.trade_id) ?? "one position"} (${k.reason})`).join("; ")}.` : ""}
          </p>
        </div>
      )}
    </CardV2>
  );
}

function ClosedTradesV2({ trades, onSaved }: { trades: Loadable<TradeRecord[]>; onSaved: () => void }) {
  const [all, setAll] = useState(false);
  const rows = useMemo(() => closedRows(dataOf(trades) ?? []), [trades]);
  const shown = all ? rows : rows.slice(0, CLOSED_SHOWN);
  return (
    <CardV2 title="Closed trades" count={rows.length} more={{ label: "Orders", to: "/positions/orders" }} testId="history">
      {trades.status === "loading" ? <LoadingRowsV2 /> : trades.status === "error" ? <LoadFailedV2 what="your closed trades" /> : rows.length === 0 ? (
        <EmptyStateV2 text="Closed trades show here." />
      ) : (
        <>
          <ul className="divide-y divide-subtle">
            {shown.map((r) => <ClosedRowV2 key={r.id} row={r} onSaved={onSaved} />)}
          </ul>
          {rows.length > CLOSED_SHOWN && (
            <button type="button" onClick={() => setAll((a) => !a)} className={`${BUTTON_SECONDARY} mt-space-3 w-full`}>
              {all ? "Show fewer" : `Show all ${rows.length}`}
            </button>
          )}
        </>
      )}
    </CardV2>
  );
}

function ClosedRowV2({ row, onSaved }: { row: ClosedRow; onSaved: () => void }) {
  const tone = toneClass(signTone(row.pnl));
  const colour = useColourV2();          // colour v2: Long / Short as chips
  // Today's journal edit (notes, then tags), unchanged; the list reloads instead of the whole page.
  function edit() {
    const notes = window.prompt("Journal notes", row.notes ?? "");
    if (notes === null) return;
    const tags = window.prompt("Tags (comma separated)", row.tags.join(", "));
    void api.updateTradeJournal(row.id, { notes, tags: (tags ?? "").split(",").map((t) => t.trim()).filter(Boolean) }).then(onSaved, () => undefined);
  }
  return (
    <li className="py-space-3 first:pt-0 last:pb-0" data-closed={row.id}>
      <div className="flex items-start gap-space-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-space-2">
            <span className="text-t15 font-semibold text-fg">{row.symbol}</span>
            {colour ? <DirectionChip side={row.side === "Long" ? "long" : "short"} />
              : <span className={`text-t13 font-medium ${row.side === "Long" ? "text-up" : "text-down"}`}>{row.side}</span>}
            <ChipV2 kind={row.mode === "Live" ? "solid" : "outline"}>{row.mode}</ChipV2>
          </div>
          <p className="mt-px font-tabular text-t13 text-fg-muted">{row.size} · {row.prices} · {row.when}{row.reason ? ` · ${row.reason}` : ""}</p>
          <p className="mt-px text-t12 text-fg-muted">
            Charges {row.charges} ({row.chargesActual ? "from your contract note" : "estimate"}){row.strategy ? ` · ${row.strategy}` : ""}
          </p>
          {(row.notes || row.tags.length > 0) && (
            <p className="mt-px text-t12 text-fg">
              {row.tags.map((t) => <span key={t} className="mr-space-1 inline-flex rounded-pill border border-border px-space-2 text-fg-muted">{t}</span>)}
              {row.notes && <span className="italic">{row.notes}</span>}
            </p>
          )}
        </div>
        <div className="w-28 shrink-0 text-right">
          <div className="text-t12 text-fg-muted">P&amp;L</div>
          <div className={`font-tabular text-t15 font-semibold ${tone}`}>{formatSignedINR(row.pnl, { paise: true })}</div>
          <button type="button" onClick={edit} className="mt-space-1 inline-flex min-h-11 items-center text-t13 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-0">
            {row.notes || row.tags.length ? "Edit note" : "Add a note"}
          </button>
        </div>
      </div>
    </li>
  );
}
