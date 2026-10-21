import { ArrowDownRight, ArrowUpRight, ChartCandlestick } from "lucide-react";
import { Suspense, lazy, useId, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import ContractNoteCard from "../components/ContractNoteCard";
import PaperAccountCard from "../components/PaperAccountCard";
import PaperTicketCard from "../components/PaperTicketCard";
import { CardV2, ChipV2, EmptyStateV2, LoadFailedV2, LoadingRowsV2, toneClass } from "../dashboard/CardV2";
import { signTone } from "../dashboard/model";
import { dataOf, usePolled, type Loadable } from "../dashboard/useDashboardData";
import DecisionToolsCard from "../progression/DecisionToolsCard";
import ExperienceCard from "../progression/ExperienceCard";
import type { PageProps } from "../routes";
import { chartsPath } from "../shell/shellRoutes";
import type { PositionGreeks, TradeRecord } from "../types";
import { BrokerGateView, CONNECT_BROKER_PATH, SIGN_IN_PATH, useBrokerLogin } from "../ui/BrokerGate";
import { useBrokerSession } from "../ui/brokerSession";
import { EMPTY, formatINR, formatNumber, formatPct, formatPrice, formatSignedINR, formatTimeIST } from "../ui/format";
import { PageIntro } from "../ui/PageIntro";
import { TermHint } from "../ui/TermHint";
import { useFeatures } from "../ui/useFeatures";
import {
  closedRows, closedText, exitConfirmText, failedText, heldRows, holdingTiles, openCaption, type ClosedRow, type HeldRow,
} from "./model";
import { BUTTON_SECONDARY, FIELD, NoteV2, PageTitleV2, TileV2, TilesV2 } from "./PartsV2";

// N1 F3 (flag portfolio_risk): the portfolio risk card is its own chunk, loaded only while the flag is on.
const PortfolioRiskCard = lazy(() => import("../portfolio/PortfolioRiskCard"));

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
 * OWN_BROKER_STATES): while no broker session is usable the gate says what is missing, and below it the trader's own open
 * positions - their real records at the entry price, no live price and nothing made up - keep their exit buttons. The
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

  const { state: broker, refresh: refreshBroker } = useBrokerSession();
  const connected = broker.kind === "connected";
  const signedIn = !!user;
  const [positions, reloadPositions] = usePolled(api.listPositions, every(REFRESH_MS), signedIn);
  const [trades, reloadTrades] = usePolled(api.listTrades, every(REFRESH_MS * 2), signedIn && connected);
  const [exposure, reloadExposure] = usePolled(() => api.portfolioExposure(true), every(REFRESH_MS), signedIn && connected);
  const reload = () => { reloadPositions(); reloadTrades(); reloadExposure(); };
  const login = useBrokerLogin(() => { refreshBroker(); reload(); });
  const [notice, setNotice] = useState<Notice>(null);

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
  const actions = { exitNowOn, canTrade, onExit: exitNow, onCheck: checkPrice };

  if (!connected) {
    return (
      <div className="mx-auto w-full space-y-space-4 md:space-y-space-6" data-page="positions-v2">
        <PageTitleV2 title="My positions" subtitle="What you hold now and how it is doing." />
        <div>
          <BrokerGateView state={broker} onConnect={() => navigate(CONNECT_BROKER_PATH)} onSignIn={() => navigate(SIGN_IN_PATH)}
            onLogin={login.login} onRetry={refreshBroker} busy={login.busy} error={login.error} codeStep={login.codeStep} onSubmitCode={login.submitCode} />
        </div>
        {signedIn && broker.kind !== "loading" && list && list.length > 0 && (
          <div className="space-y-space-4">
            {notice && <NoteV2 kind={notice.kind}>{notice.text}</NoteV2>}
            <CardV2 title="Your open positions" count={list.length} testId="exits">
              <p className="mb-space-3 text-t13 text-fg-muted">
                Live prices and profit or loss show once your broker is connected. You can still exit a paper position here.
              </p>
              <PositionList rows={rows} byId={byId} actions={actions} />
            </CardV2>
          </div>
        )}
      </div>
    );
  }

  const tiles = holdingTiles(list, dataOf(exposure));
  const hasOptions = (list ?? []).some((p) => p.instrument_kind === "OPTION");

  return (
    <div className="mx-auto w-full space-y-space-4 md:space-y-space-6" data-page="positions-v2">
      <PageIntro id="positions" lines={[
        "Everything you hold right now, with its live price and profit or loss.",
        "Open a position's chart, or exit a paper position at the current price.",
      ]} />
      <PageTitleV2 title="My positions" subtitle="What you hold now and how it is doing." />

      <section aria-label="Summary" data-block="summary">
        {positions.status === "loading" ? <LoadingRowsV2 rows={2} /> : (
          <TilesV2>
            <TileV2 testId="open" label="Open positions" value={positions.status === "ok" ? String(tiles.open) : EMPTY} sub={positions.status === "ok" ? openCaption(tiles) : undefined} />
            <TileV2 testId="open-pnl" label="Open P&L" hint="open-pnl" value={formatSignedINR(tiles.openPnl)} tone={signTone(tiles.openPnl)}
              sub={tiles.open === 0 ? undefined : tiles.openPnl === null ? "waiting for live prices" : "at live prices"} />
            <TileV2 testId="booked" label="Booked today" hint="booked-pnl" value={formatSignedINR(tiles.booked)} tone={signTone(tiles.booked)}
              sub={tiles.booked === null ? undefined : "closed trades since the open"} />
            <TileV2 testId="risk" label="Risk at stops" hint="risk-at-stops" value={formatINR(tiles.riskAtStops)}
              sub={tiles.riskPct === null ? undefined : `${formatNumber(tiles.riskPct, 1)}% of capital`} />
          </TilesV2>
        )}
      </section>

      {notice && <NoteV2 kind={notice.kind} testId="action">{notice.text}</NoteV2>}

      <CardV2 title="Open positions" count={list?.length} testId="positions">
        {positions.status === "loading" ? <LoadingRowsV2 /> : positions.status === "error" ? <LoadFailedV2 what="your positions" /> : rows.length === 0 ? (
          <EmptyStateV2 text="No open positions. Find a setup or let Autopilot trade for you." action={{ label: "Find setups", to: "/screener" }} />
        ) : (
          <PositionList rows={rows} byId={byId} actions={actions} />
        )}
      </CardV2>

      {hasOptions && <GreeksCardV2 />}

      <ClosedTradesV2 trades={trades} onSaved={reloadTrades} />

      <section aria-labelledby="paper-tools" className="space-y-space-4" data-block="tools">
        <h2 id="paper-tools" className="text-t15 font-semibold text-fg">Paper account and tools</h2>
        <PaperAccountCard canTrade={canTrade} />
        <ExperienceCard canTrade={canTrade} />
        {portfolioRiskOn && <Suspense fallback={null}><PortfolioRiskCard /></Suspense>}
        <DecisionToolsCard />
        <PaperTicketCard canTrade={canTrade} onPlaced={reload} />
        {canTrade && <ContractNoteCard onApplied={reload} />}
      </section>
    </div>
  );
}

export interface RowActions {
  exitNowOn: boolean;
  canTrade: boolean;
  onExit: (p: TradeRecord) => void;
  onCheck: (p: TradeRecord, price: number) => void;
}

function PositionList({ rows, byId, actions }: { rows: HeldRow[]; byId: Map<number, TradeRecord>; actions: RowActions }) {
  return (
    <ul className="divide-y divide-subtle">
      {rows.map((r) => {
        const p = byId.get(r.id);
        return p ? <PositionRowV2 key={r.id} row={r} position={p} actions={actions} /> : null;
      })}
    </ul>
  );
}

export function PositionRowV2({ row, position, actions }: { row: HeldRow; position: TradeRecord; actions: RowActions }) {
  const tone = toneClass(signTone(row.pnl));
  const showExit = actions.exitNowOn && actions.canTrade && position.mode === "PAPER";
  return (
    <li className="py-space-3 first:pt-0 last:pb-0" data-position={row.id}>
      <div className="flex items-start gap-space-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-space-2">
            <span className="text-t15 font-semibold text-fg">{row.symbol}</span>
            <span className={`inline-flex items-center gap-px text-t13 font-medium ${row.side === "Buy" ? "text-up" : "text-down"}`}>
              {row.side === "Buy" ? <ArrowUpRight size={14} aria-hidden /> : <ArrowDownRight size={14} aria-hidden />}{row.side === "Buy" ? "Long" : "Short"}
            </span>
            <ChipV2 kind={row.mode === "Live" ? "solid" : "outline"}>{row.mode}</ChipV2>
            {row.structure && <ChipV2>{row.structure}</ChipV2>}
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
        </div>
        <div className="hidden w-28 shrink-0 text-right sm:block">
          <div className="flex items-center justify-end gap-space-1 text-t12 text-fg-muted">LTP</div>
          <div className="font-tabular text-t15 font-medium text-fg">{formatPrice(row.ltp)}</div>
        </div>
        <div className="w-28 shrink-0 text-right">
          <div className="text-t12 text-fg-muted">P&amp;L</div>
          <div className={`font-tabular text-t15 font-semibold ${tone}`}>{formatSignedINR(row.pnl)}</div>
          <div className={`font-tabular text-t13 ${tone}`}>{formatPct(row.pnlPct)}</div>
        </div>
      </div>
      <div className="mt-space-2 flex flex-wrap items-center gap-space-2">
        {showExit && (
          // UI-11: a neutral button; today's confirm runs before anything is sent. Never disabled here (ADR-0004).
          <button type="button" onClick={() => actions.onExit(position)} data-testid="exit-now" className={BUTTON_SECONDARY}>
            Exit now
          </button>
        )}
        {!actions.exitNowOn && <CheckPriceV2 position={position} onCheck={actions.onCheck} />}
        <Link to={chartsPath(row.chartSymbol)} className={BUTTON_SECONDARY}>
          <ChartCandlestick size={16} aria-hidden />Chart
        </Link>
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

function GreeksCardV2() {
  const [greeks, setGreeks] = useState<PositionGreeks | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true); setError(null);
    try { setGreeks(await api.positionGreeks()); } catch (e) { setError(failedText("We could not work out the Greeks.", e)); } finally { setBusy(false); }
  }
  return (
    <CardV2 title={<>Option Greeks <TermHint term="greeks" /></>} testId="greeks">
      <div className="flex flex-col gap-space-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-t13 text-fg-muted">Worked out from each option&apos;s last price and the index or stock price now.</p>
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
            As of {formatTimeIST(greeks.as_of)}.{greeks.skipped.length > 0 ? ` Left out: ${greeks.skipped.map((k) => `#${k.trade_id} (${k.reason})`).join("; ")}.` : ""}
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
    <CardV2 title="Closed trades" count={rows.length} more={{ label: "Orders", to: "/orders" }} testId="history">
      {trades.status === "loading" ? <LoadingRowsV2 /> : trades.status === "error" ? <LoadFailedV2 what="your closed trades" /> : rows.length === 0 ? (
        <EmptyStateV2 text="No closed trades yet. A trade shows here once it is closed." />
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
            <span className={`text-t13 font-medium ${row.side === "Long" ? "text-up" : "text-down"}`}>{row.side}</span>
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
          <div className={`font-tabular text-t15 font-semibold ${tone}`}>{formatSignedINR(row.pnl)}</div>
          <button type="button" onClick={edit} className="mt-space-1 inline-flex min-h-11 items-center text-t13 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-0">
            {row.notes || row.tags.length ? "Edit note" : "Add a note"}
          </button>
        </div>
      </div>
    </li>
  );
}
