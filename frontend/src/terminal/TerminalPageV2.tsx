import { LayoutPanelTop, RotateCcw, Save } from "lucide-react";
import { useConfirm } from "../ui/ConfirmDialog";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { useAuth } from "../auth/AuthContext";
import { cx } from "../components/primitives/cx";
import { dataOf, usePolled, type Loadable } from "../dashboard/useDashboardData";
import { spotSymbolOf, useChainTrading } from "../fno/ChainTradingV2";
import { useBasket } from "../fno/basket";
import type { Side } from "../fno/api";
import type { TicketTarget } from "../fno/ticketModel";
import { readColumns, writeColumns, type ColumnPrefs } from "../optionChain/model";
import { TerminalChainV2 } from "./TerminalChainV2";
import { AccountPanelV2 } from "./AccountPanelV2";
import { terminalLevels } from "./chartLevels";
import type { PageProps } from "../routes";
import { NoteV2 } from "../trading/PartsV2";
import { PageIntro } from "../ui/PageIntro";
import { useFeatures } from "../ui/useFeatures";
import { NARROW_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import { useSearchParams } from "react-router";
import { useMarketStatus } from "../chart/useMarketStatus";
import { useOptionChain } from "../optionChain/useOptionChain";
import TerminalClassicV2 from "./TerminalClassicV2";
import { ContextBarV2, useStripFigures } from "./ContextBarV2";
import { modeChip } from "./statusModel";
import { defaultTerminalApi, type TerminalApi } from "./api";
import {
  CHOOSER_PANEL_IDS, DEFAULT_UNDERLYING, EMPTY_PREFS, MAX_LAYOUTS, PANELS, PRESETS, PRESET_IDS, activeKey, changed, closePanel,
  isPreset, layoutFor, nameProblem, openPanel, remove, rename, resetToPreset, saveAs, saveOver, switchTo,
  type Layout, type PanelId, type PanelPlace, type PhoneTab, type TerminalPrefs,
} from "./layoutModel";
import {
  ChartPanelV2, OrderPanelV2, OrdersPanelV2, PlaceholderPanelV2, PositionsPanelV2, useTerminalTicket, type Read,
} from "./PanelsV2";
import { PhoneTabsV2 } from "./PhoneTabsV2";
import { chartLiveOf, useTerminalSpot } from "./spot";
import BasketPanelV2 from "../fno/BasketPanelV2";
import IncompleteOrdersV2 from "../fno/IncompleteOrdersV2";
import { RollPendingV2 } from "../fno/RollV2";
import { writePending } from "../fno/roll";
import { areasByTrade, chainAreaMarks, useStrikeAreas } from "../fno/strikeArea";
import { StatusBarV2 } from "./StatusBarV2";
import { useCombinedExits } from "../fno/CombinedExitV2";
import { TerminalGridV2 } from "./TerminalGridV2";
import { readMask, writeMask } from "./statusModel";
import { Combobox } from "../ui/Combobox";

/**
 * Trading Terminal T1 (docs/owner-orders/ATP_SESSION_TRADING_TERMINAL.md T1; review TT-15..TT-19): the chart, the option
 * chain, the order panel, positions, orders and the basket on one screen. Shown only with `ui_v2` and `terminal` on.
 *
 * - Desktop (from 1024 px): ATP's own 12-column grid of panels (TerminalGridV2) the user moves, resizes, opens and
 *   closes; a ready layout to start from (Option seller - the default for a new user, T-D1 -, Quick trade, Multi-chart)
 *   and up to 10 of his own, saved on the server (prefs `terminal`), so a layout follows him to another device.
 * - Phone and tablet: the panels as tabs, one at a time (PhoneTabsV2).
 * - The status bar (StatusBarV2): his own figures from GET /api/terminal/status, the mode shown and never changed.
 *
 * Every order goes through the ONE ticket and basket (src/fno): a price on the chain opens the existing action sheet, and
 * Buy / Sell there fill the order panel (the same ticket state as the modal ticket, useOrderTicket) - PAPER in Phase 0.
 * Exits stay on the position rows (never blocked, ADR-0004). Nothing here suggests a strike, a size or a level.
 */
const STATUS_MS = 15_000;
const LIST_MS = 30_000;
const NARROW = NARROW_QUERY;
const BUTTON = "inline-flex min-h-11 shrink-0 items-center justify-center gap-space-1 rounded-control border border-border bg-surface-1 px-space-3 text-t14 font-semibold text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 md:min-h-9";

const asRead = <T,>(l: Loadable<T>): Read<T> => (l.status === "ok" ? { status: "ok", value: l.data } : l.status === "error" ? { status: "error" } : { status: "loading" });

type Note = { kind: "done" | "info" | "warn"; text: string } | null;
type Ask = { kind: "save-as" | "rename"; value: string } | null;

function useLayouts(terminal: TerminalApi) {
  const [prefs, setPrefs] = useState<TerminalPrefs>(EMPTY_PREFS);
  const [version, setVersion] = useState<number | null>(null);
  const [working, setWorking] = useState<Layout>(() => layoutFor(EMPTY_PREFS, activeKey(EMPTY_PREFS)));
  const [note, setNote] = useState<Note>(null);
  const [busy, setBusy] = useState(false);
  const loaded = useRef(false);

  const reload = useCallback(async () => {
    try {
      const r = await terminal.loadLayouts();
      setPrefs(r.value);
      setVersion(r.version);
      setWorking(layoutFor(r.value, activeKey(r.value)));
    } catch {
      setVersion(null);
      setNote({ kind: "warn", text: "Your saved layouts could not be read. The Option seller layout is shown; try again in a moment." });
    }
  }, [terminal]);

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    void reload();
  }, [reload]);

  async function persist(next: TerminalPrefs, done: string, nextWorking?: Layout): Promise<boolean> {
    if (version === null) { setNote({ kind: "warn", text: "Your layouts are not loaded yet. Try again in a moment." }); return false; }
    setBusy(true);
    try {
      const r = await terminal.saveLayouts(next, version);
      setPrefs(r.value);
      setVersion(r.version);
      setWorking(nextWorking ?? layoutFor(r.value, activeKey(r.value)));
      setNote({ kind: "done", text: done });
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await reload();
        setNote({ kind: "info", text: "Your layouts were changed on another device. They are reloaded; please try again." });
      } else {
        setNote({ kind: "warn", text: "The layout was not saved. Check your connection and try again." });
      }
      return false;
    } finally {
      setBusy(false);
    }
  }

  return { prefs, setPrefs, version, working, setWorking, note, setNote, busy, persist, key: activeKey(prefs) };
}

export default function TerminalPageV2(props: PageProps & { terminal?: TerminalApi } = {}) {
  // TERM-2: the Terminal as it was, kept at ?view=classic (Settings › Advanced) until the owner approves the rebuild.
  const [params] = useSearchParams();
  return params.get("view") === "classic" ? <TerminalClassicV2 {...props} /> : <TerminalNowV2 {...props} />;
}

function TerminalNowV2({ terminal = defaultTerminalApi }: PageProps & { terminal?: TerminalApi } = {}) {
  const { user } = useAuth();
  const features = useFeatures();
  const narrow = useMediaQuery(NARROW);
  const signedIn = !!user;
  const canTrade = signedIn && user?.role !== "VIEWER";
  const exitNowOn = features.on("approval_only_entries") || features.on("paper_account");
  const L = useLayouts(terminal);
  const { working, setWorking, prefs, key } = L;
  const underlying = working.underlying ?? DEFAULT_UNDERLYING;
  const [ask, setAsk] = useState<Ask>(null);
  const [tab, setTab] = useState<PhoneTab>("chart");
  const [masked, setMasked] = useState<boolean>(() => readMask());
  const [ticket, setTicket] = useState<{ target: TicketTarget; side: Side } | null>(null);
  const confirmBox = useConfirm();   // UI-28 U3.3: the in-app confirm (never the browser's)

  const openIds = useMemo(() => new Set(working.panels.filter((x) => x.open).map((x) => x.id)), [working.panels]);
  const wants = (id: PanelId, phoneTab: PhoneTab) => (narrow ? tab === phoneTab : openIds.has(id));

  // His own figures (TT-15): the status bar, his positions and his orders. The chain of the underlying the panels follow.
  const [statusL] = usePolled(terminal.status, () => STATUS_MS, signedIn);
  const [positionsL, reloadPositions] = usePolled(terminal.positions, () => LIST_MS, signedIn);
  const [ordersL, reloadOrders] = usePolled(terminal.orders, () => LIST_MS, signedIn);
  // CX6: his combined exits, for the positions panel's chips and the status bar (PAPER account on; 404 otherwise).
  const combined = useCombinedExits(canTrade && features.on("paper_account"));
  // T1-6: P&L on every position card - the same live-price read as My trades (portfolio exposure).
  const positionsShown = wants("positions", "positions");
  const loadExposure = useCallback(() => api.portfolioExposure(true), []);
  const [exposureL, reloadExposure] = usePolled(loadExposure, () => LIST_MS, signedIn && positionsShown);
  // TERM-2 step 1: the chain of the ONE active symbol + expiry (the chain page's own read: on an expiry day the next
  // expiry, per the user's chain setting). The context bar's PCR / DTE come from it, so it is read whenever signed in.
  const market = useMarketStatus();
  const chain = useOptionChain(underlying, market.status?.sessions?.NSE?.is_open ?? null);
  const live = chain.live;
  const strip = useStripFigures(underlying, signedIn);
  // T1-3: one spot for the chain and the chart (the same read, the same time).
  const spotSymbol = spotSymbolOf(underlying);
  const spot = useTerminalSpot({ symbol: spotSymbol, hub: features.on("data_hub"), enabled: signedIn, chain: live });
  // The chain page's own column choice (one setting for both).
  const [columns, setColumns] = useState<ColumnPrefs>(() => readColumns());
  // TERM-2 step 3: Buy / Sell fill the order panel where the layout has one open; otherwise (and on a phone) the one
  // order ticket opens as a sheet from the action sheet.
  const orderPanelShown = !narrow && openIds.has("order");
  const expiryList = chain.expiries?.expiries.length ? chain.expiries.expiries : chain.expiry ? [{ date: chain.expiry, kind: "weekly" as const }] : [];
  const trading = useChainTrading({
    symbol: underlying, live, prefs: columns, onPrefs: setColumns, expiries: expiryList,
    onTicket: orderPanelShown ? (t) => setTicket(t) : undefined,
  });
  const legs = useBasket();
  const [breakevens, setBreakevens] = useState<number[] | null>(null);
  const levels = useMemo(() => terminalLevels({ underlying, expiry: live?.expiry ?? chain.expiry, basket: legs, held: trading.held,
    breakevens, combined: combined.list, spotSymbol }), [underlying, live?.expiry, chain.expiry, legs, trading.held, breakevens, combined.list, spotSymbol]);
  const afterOrder = () => { reloadPositions(); reloadOrders(); reloadExposure(); };
  // TERM-2 step 4 (each behind its own flag, with ui_v2 + terminal; PAPER first): R1 Roll, R3 the short strikes' areas.
  const termOn = (flag: string) => features.on("ui_v2") && features.on("terminal") && features.on(flag);
  const rollOn = termOn("term_roll") && features.on("paper_account") && canTrade;
  const strikeAreas = useStrikeAreas(termOn("term_strike_area") && signedIn);
  const areaById = useMemo(() => areasByTrade(strikeAreas), [strikeAreas]);
  const areaMarks = useMemo(() => chainAreaMarks(strikeAreas, underlying, live?.expiry ?? chain.expiry ?? null), [strikeAreas, underlying, live?.expiry, chain.expiry]);
  // R1 step 2: show the one basket (its phone tab, or its panel on the grid).
  const openBasket = () => { if (narrow) setTab("basket"); else setWorking((w) => (w.panels.some((x) => x.id === "basket" && x.open) ? w : { ...w, panels: openPanel(w.panels, "basket") })); };
  const ticketState = useTerminalTicket(ticket, live && ticket && ticket.target.expiry === live.expiry ? live.rows : null, afterOrder);

  // A new underlying starts a new order: the panel never keeps a contract of another underlying.
  useEffect(() => { setTicket((t) => (t && t.target.underlying !== underlying ? null : t)); }, [underlying]);

  const toggleMask = () => setMasked((m) => { writeMask(!m); return !m; });
  const dirty = changed(prefs, key, working);
  const own = !isPreset(key) ? prefs.layouts[key] : undefined;
  const count = Object.keys(prefs.layouts).length;

  const chartSymbol = (place: PanelPlace) => place.symbol ?? spotSymbolOf(underlying);
  const title = (place: PanelPlace) => (place.id.startsWith("chart-") ? `${PANELS[place.id].label} · ${chartSymbol(place)}` : PANELS[place.id].label);
  const setPanels = (panels: PanelPlace[]) => setWorking((w) => ({ ...w, panels }));
  const setChartSymbol = (id: PanelId, symbol: string | null) => setWorking((w) => ({ ...w, panels: w.panels.map((x) => (x.id === id ? { ...x, symbol } : x)) }));

  function panel(id: PanelId, place?: PanelPlace): ReactNode {
    switch (id) {
      case "chart-1": case "chart-2": case "chart-3": case "chart-4": {
        const p = place ?? working.panels.find((x) => x.id === id) ?? { id, x: 0, y: 0, w: 6, h: 10, open: true };
        const sym = chartSymbol(p);
        // TERM-2 step 2: the main chart follows the ONE active symbol (no free-text symbol of its own) and draws his legs,
        // breakevens and own spot stops; the extra charts of a saved multi-chart layout keep their own symbol.
        const main = id === "chart-1";
        return <ChartPanelV2 symbol={main ? spotSymbol : sym} onSymbol={main || narrow ? undefined : (s) => setChartSymbol(id, s)}
          live={(main ? spotSymbol : sym).toUpperCase() === spotSymbol.toUpperCase() ? chartLiveOf(spot) : undefined}
          levels={main ? levels : undefined} />;
      }
      // TERM-2 step 2: the Option chain page's own chain (summary strip, columns menu, ChainTableV2), not a copy.
      case "chain": return <TerminalChainV2 symbol={underlying} chain={chain} spot={spot} prevClose={strip.prevClose} prefs={columns}
        onPrefs={(next) => { setColumns(next); writeColumns(next); }} trading={trading} areaMarks={areaMarks.size ? areaMarks : undefined} />;
      case "order": return <OrderPanelV2 ticket={ticket} state={ticketState} onClear={() => setTicket(null)} paperBadge={false} />;
      case "positions": {
        const list = <PositionsPanelV2 read={asRead(positionsL)} exposure={dataOf(exposureL) ?? null} canTrade={canTrade} exitNowOn={exitNowOn}
          fnoOn={features.on("paper_account")} combined={combined} onChanged={afterOrder}
          roll={rollOn ? { spot: live?.summary.spot ?? null, onBasket: openBasket } : undefined} areas={areaById.size ? areaById : undefined} />;
        // TERM-2 step 3: on the desktop grid the one bottom panel - Positions · Orders · Alerts; a phone has its own tabs.
        return narrow ? list : <AccountPanelV2 positions={list} orders={panel("orders")} counts={{ positions: dataOf(positionsL)?.length ?? 0 }} />;
      }
      case "orders": return <OrdersPanelV2 read={asRead(ordersL)} terminal={terminal} />;
      // TERM-2 step 2: the one basket (legs, payoff, max profit / loss, breakevens, PoP, margin with the hedge, his own
      // exits, one Place) - the server's one calculation, rendered once; the spread builder's own figures are gone.
      case "basket": return <BasketPanelV2 embedded open onClose={() => undefined} onPlaced={() => { afterOrder(); writePending(null); }} onNumbers={(n) => setBreakevens(n?.breakevens ?? null)}
        chain={{ symbol: underlying, live, expiries: expiryList, loadChain: (e) => api.optionChainLive(underlying, e) }} />;
      case "bots": return <PlaceholderPanelV2 kind="bots" />;
      case "payoff": return <PlaceholderPanelV2 kind="payoff" />;
      default: return null;
    }
  }

  async function pickLayout(next: string) {
    if (dirty && !(await confirmBox.ask("Switch layout?", "The changes you have not saved on this layout will be lost.", "Switch layout"))) return;
    const switched = switchTo(prefs, next);
    const shown = layoutFor(switched, activeKey(switched));
    // The layout changes at once; it is remembered on the server so another device opens on it too.
    L.setPrefs(switched);
    setWorking(shown);
    if (L.version !== null) void L.persist(switched, `${shown.name} is in use.`, shown);
  }

  async function submitAsk() {
    if (!ask) return;
    if (ask.kind === "save-as") {
      const r = saveAs(prefs, working, ask.value);
      if ("error" in r) { L.setNote({ kind: "warn", text: r.error }); return; }
      if (await L.persist(r.prefs, `Saved as "${ask.value.trim()}".`)) setAsk(null);
    } else if (own) {
      const r = rename(prefs, key, ask.value);
      if ("error" in r) { L.setNote({ kind: "warn", text: r.error }); return; }
      if (await L.persist(r.prefs, `Renamed to "${ask.value.trim()}".`, { ...working, name: ask.value.trim() })) setAsk(null);
    }
  }

  function save() {
    if (!own) { setAsk({ kind: "save-as", value: "" }); return; }
    const r = saveOver(prefs, key, working);
    if ("error" in r) { L.setNote({ kind: "warn", text: r.error }); return; }
    void L.persist(r.prefs, `"${own.name}" is saved.`);
  }

  async function del() {
    if (!own || !(await confirmBox.ask(`Delete the layout "${own.name}"?`, "This cannot be undone.", "Delete layout"))) return;
    void L.persist(remove(prefs, key), `"${own.name}" is deleted.`);
  }

  const askProblem = ask ? (ask.value.trim() ? nameProblem(prefs, ask.value, ask.kind === "rename" ? key : undefined) : null) : null;
  const status = dataOf(statusL) ?? null;

  return (
    <div className="mx-auto w-full space-y-space-3" data-page="terminal-v2" data-layout={key}>
      {confirmBox.dialog}
      {/* TERM-2 step 5: one line; every order is still shown to you before it is placed (the ticket's Review step). */}
      <PageIntro id="terminal" lines={["One symbol and expiry for the chart, chain, basket and your positions."]} />
      <ContextBarV2 symbol={underlying} onSymbol={(v) => setWorking((w) => ({ ...w, underlying: v === DEFAULT_UNDERLYING ? null : v }))}
        chain={chain} spot={spot} prevClose={strip.prevClose} vix={strip.vix} mode={modeChip(dataOf(statusL) ?? null)} compact={narrow} />
      {/* CT-1 T3: the layout controls only where the grid renders (from 1024 px); phone and tablet show the tabs. */}
      {!narrow && <div className="flex flex-wrap items-center gap-space-2" role="toolbar" aria-label="Terminal" data-block="terminal-toolbar">
        <div className="flex flex-wrap items-center gap-space-2" data-tour="terminal-layouts">
          <div className="flex items-center gap-space-2 text-t13 text-fg-muted">
            <span aria-hidden>Layout</span>
            <Combobox label="Layout" value={key} onChange={pickLayout} disabled={L.busy} dataAttrs={{ "data-terminal": "layout" }}
              options={[...PRESET_IDS.filter((id) => id !== "multi_chart" || key === "multi_chart")   // TERM-2: Charts owns multi-chart
                .map((id) => ({ value: id, label: PRESETS[id].name, group: "Ready layouts" })),
                ...(count > 0 ? Object.entries(prefs.layouts).map(([id, l]) => ({ value: id, label: l.name, group: "Your layouts" })) : [])]} />
          </div>
          {!narrow && <PanelsMenu panels={working.panels} onToggle={(id, on) => setPanels(on ? openPanel(working.panels, id) : closePanel(working.panels, id))} />}
          <button type="button" className={BUTTON} onClick={save} disabled={L.busy || (!!own && !dirty)} data-terminal="save">
            <Save size={16} aria-hidden />{own ? "Save" : "Save as mine"}
          </button>
          {own && <button type="button" className={BUTTON} onClick={() => setAsk({ kind: "save-as", value: "" })} disabled={L.busy || count >= MAX_LAYOUTS} data-terminal="save-as">Save as new</button>}
          {own && <button type="button" className={BUTTON} onClick={() => setAsk({ kind: "rename", value: own.name })} disabled={L.busy} data-terminal="rename">Rename</button>}
          {own && <button type="button" className={BUTTON} onClick={del} disabled={L.busy} data-terminal="delete">Delete</button>}
          <button type="button" className={BUTTON} onClick={() => setWorking(resetToPreset(working))} data-terminal="reset"
            title={`Back to the ${PRESETS[working.preset].name} layout`}>
            <RotateCcw size={16} aria-hidden />Reset
          </button>
          <span className="text-t13 text-fg-muted" data-terminal="count">{count} of {MAX_LAYOUTS} saved</span>
        </div>
      </div>}

      {ask && (
        <form className="flex flex-wrap items-end gap-space-2" data-terminal="ask" onSubmit={(e) => { e.preventDefault(); void submitAsk(); }}>
          <label className="flex min-w-0 flex-col gap-space-1 text-t13 text-fg-muted">
            {ask.kind === "save-as" ? "Name of the new layout" : "New name"}
            <input autoFocus value={ask.value} maxLength={40} onChange={(e) => setAsk({ ...ask, value: e.target.value })} data-terminal="name"
              className="h-11 w-64 max-w-full rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg focus:border-brand focus:outline-none md:h-9" />
          </label>
          <button type="submit" className={BUTTON} disabled={L.busy || !ask.value.trim() || !!askProblem} data-terminal="ask-ok">{ask.kind === "save-as" ? "Save" : "Rename"}</button>
          <button type="button" className={BUTTON} onClick={() => setAsk(null)}>Cancel</button>
          {askProblem && <p className="w-full text-t13 text-warn" role="alert">{askProblem}</p>}
        </form>
      )}

      {L.note && <NoteV2 kind={L.note.kind} testId="terminal-layout">{L.note.text}</NoteV2>}

      {/* T1-4: a multi-leg order that stopped half way shows at once, above every panel and tab, with "Complete the
          leg" / "Exit the filled leg" - never a silent naked short. Nothing here places or closes by itself. */}
      {signedIn && features.on("smart_execution") && <div data-terminal="incomplete"><IncompleteOrdersV2 /></div>}
      {/* TERM-2 R1: a roll that is half done (old legs closed, new legs in the basket, not placed) says so, beside it. */}
      {rollOn && <div data-terminal="roll-pending"><RollPendingV2 onBasket={openBasket} /></div>}

      {narrow ? (
        <div className="space-y-space-3" data-tour="terminal-panels">
          <PhoneTabsV2 tab={tab} onTab={setTab} counts={{ basket: legs.length, positions: dataOf(positionsL)?.length ?? 0 }} />
          <section id={`terminal-panel-${tab}`} role="tabpanel" aria-labelledby={`terminal-tab-${tab}`} data-phone-panel={tab}
            className="rounded-panel border border-subtle bg-surface-1 p-space-3">
            {tab === "chart" && <div className="h-[22rem]">{panel("chart-1")}</div>}
            {tab === "chain" && panel("chain")}
            {/* TERM-2 step 3: the one basket has its own tab on a phone. */}
            {tab === "basket" && panel("basket")}
            {tab === "order" && panel("order")}
            {tab === "positions" && panel("positions")}
            {tab === "orders" && panel("orders")}
            {tab === "bots" && panel("bots")}
          </section>
        </div>
      ) : (
        <div data-tour="terminal-panels">
          <TerminalGridV2 panels={working.panels} onChange={setPanels} onClose={(id) => setPanels(closePanel(working.panels, id))}
            render={(place) => panel(place.id, place)} title={title} />
        </div>
      )}

      <div data-tour="terminal-status">
        <StatusBarV2 status={status} failed={statusL.status === "error"} masked={masked} onMask={toggleMask} compact={narrow} combined={combined.list} showMode={false} showFeed={false} />
      </div>
      {trading.layer}
    </div>
  );
}

/** The toolbar's "Panels" menu: every panel with a tick for open (opening one finds it a free place on the grid). */
function PanelsMenu({ panels, onToggle }: { panels: readonly PanelPlace[]; onToggle: (id: PanelId, on: boolean) => void }) {
  const open = new Set(panels.filter((x) => x.open).map((x) => x.id));
  return (
    <details className="relative" data-terminal="panels">
      <summary className={cx(BUTTON, "cursor-pointer list-none")}><LayoutPanelTop size={16} aria-hidden />Panels</summary>
      <fieldset className="absolute left-0 z-20 mt-space-1 w-60 space-y-px rounded-panel border border-subtle bg-surface-1 p-space-2 shadow-raised">
        <legend className="sr-only">Show panels</legend>
        {CHOOSER_PANEL_IDS.map((id) => (
          <label key={id} className="flex min-h-9 cursor-pointer items-center gap-space-2 rounded-control px-space-2 text-t14 text-fg hover:bg-surface-2">
            <input type="checkbox" checked={open.has(id)} onChange={(e) => onToggle(id, e.target.checked)} data-panel-toggle={id}
              disabled={open.has(id) && open.size <= 1} className="h-4 w-4 accent-brand" />
            {PANELS[id].label}
          </label>
        ))}
      </fieldset>
    </details>
  );
}
