import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useBrokerChart } from "../components/chartHistory";
import PaperTicketCard from "../components/PaperTicketCard";
import ProChart from "../components/ProChart";
import { PageHeader } from "../components/primitives";
import { useMediaQuery } from "../utils/useMediaQuery";
import { useProgressionT } from "../progression/i18n";
import PositionsBoardCard from "../progression/PositionsBoardCard";
import { TIMEFRAMES, flattenTargets, loadLayout, saveLayout, shortcutFor, staleness, type Layout } from "../progression/workspace";
import type { WorkerStatus } from "../types";

/**
 * N2-9 (P1 spec 2.1 / 2.5, flag `trade_workspace`): one screen for manual trading - the chart on the left, the P1-d
 * PAPER ticket on the right, the positions board below; on a phone the ticket is a bottom sheet. Market, lockout and
 * stale-data states are shown. Keyboard: t ticket, c chart, x exit the focused position, Shift+F flatten all (one
 * confirmation), ? help. The layout is kept per viewer in this browser. Existing routes only: exits are Exit now, never
 * gated; entries go through the ticket's own checks.
 */
export default function TradeWorkspacePage() {
  const t = useProgressionT();
  const { user, loading } = useAuth();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [layout, setLayout] = useState<Layout>(loadLayout);
  const [symbolText, setSymbolText] = useState(layout.symbol);
  const [market, setMarket] = useState<WorkerStatus | null>(null);
  const [lockout, setLockout] = useState<string | null>(null);
  const [help, setHelp] = useState(false);
  const [sheet, setSheet] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [boardKey, setBoardKey] = useState(0);
  const ticketRef = useRef<HTMLDivElement | null>(null);
  const symbolRef = useRef<HTMLInputElement | null>(null);
  const phone = useMediaQuery("(max-width: 767px)");

  useEffect(() => {
    if (!user) return;
    api.myFeatures().then((f) => setEnabled(Boolean(f.trade_workspace))).catch(() => setEnabled(false));
    api.workerStatus().then(setMarket).catch(() => setMarket(null));
    api.paperRisk().then((r) => setLockout(r.lockout ? r.lockout.reason : null)).catch(() => setLockout(null));
  }, [user]);
  useEffect(() => { saveLayout(layout); }, [layout]);

  const chart = useBrokerChart({ enabled: !!user && !!enabled && !!layout.symbol && layout.panels.chart, symbol: layout.symbol, timeframe: layout.timeframe });
  const last = chart.candles.length ? chart.candles[chart.candles.length - 1].timestamp : null;
  const stale = staleness(last, layout.timeframe, new Date(), Boolean(market?.market_open));

  async function flattenAll() {
    let board;
    try { board = await api.positionsBoard(); } catch (e) { setNotice(String(e)); return; }
    const targets = flattenTargets(board);
    if (!targets.length) { setNotice(t("workspace.flattenNone")); return; }
    if (!window.confirm(t("workspace.flattenConfirm", { count: targets.length, list: targets.map((p) => `${p.symbol} ×${p.quantity}`).join(", ") }))) return;
    let closed = 0, failed = 0;
    for (const p of targets) {
      try { await api.exitNow(p.trade_id); closed++; } catch { failed++; }
    }
    setNotice(t("workspace.flattenDone", { closed, failed }));
    setBoardKey((k) => k + 1);
  }

  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const action = shortcutFor({ key: e.key, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, metaKey: e.metaKey, altKey: e.altKey,
                                   targetTag: target?.tagName, editable: Boolean(target?.isContentEditable) });
      if (!action || action === "exit") return;                   // "x" is handled by the focused position row itself
      e.preventDefault();
      if (action === "help") setHelp((h) => !h);
      else if (action === "ticket") { if (phone) setSheet(true); ticketRef.current?.scrollIntoView({ block: "nearest" }); ticketRef.current?.focus(); }
      else if (action === "chart") symbolRef.current?.focus();
      else if (action === "flatten") void flattenAll();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (loading || enabled === null) return <div className="p-4 text-sm text-fg-muted">…</div>;
  if (!user) return <div className="p-4 text-sm text-fg">{t("workspace.signIn")}</div>;
  if (!enabled) return <div className="p-4 text-sm text-fg-muted" data-testid="workspace-off">{t("workspace.off")}</div>;
  const canTrade = user.role !== "VIEWER";
  const setPanel = (k: keyof Layout["panels"]) => setLayout({ ...layout, panels: { ...layout.panels, [k]: !layout.panels[k] } });

  const ticket = <div ref={ticketRef} tabIndex={-1} className="outline-none"><PaperTicketCard canTrade={canTrade} onPlaced={() => setBoardKey((k) => k + 1)} /></div>;
  return (
    <div className="space-y-3" data-testid="trade-workspace">
      <PageHeader title={t("workspace.title")} />
      <WorkspaceStatus market={market} lockout={lockout} staleMinutes={stale.stale ? stale.minutes : null} />
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-fg-muted">{t("workspace.show")}:</span>
        {(["chart", "ticket", "positions"] as const).map((k) => (
          <button key={k} type="button" aria-pressed={layout.panels[k]} onClick={() => setPanel(k)}
                  className={`min-h-[44px] rounded border px-2 py-1 sm:min-h-0 ${layout.panels[k] ? "border-info text-info" : "border-border text-fg-muted"}`}>
            {t(`workspace.panel.${k}`)}
          </button>
        ))}
        <button type="button" onClick={() => setHelp(!help)} className="min-h-[44px] rounded border border-border px-2 py-1 text-fg sm:min-h-0">?</button>
      </div>
      {help && <ShortcutHelp />}
      {notice && <p role="status" className="text-xs text-fg-muted">{notice}</p>}
      <div className={`grid gap-3 ${layout.panels.chart && layout.panels.ticket && !phone ? "lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]" : ""}`}>
        {layout.panels.chart && (
          <section className="space-y-2" aria-label={t("workspace.panel.chart")}>
            <form className="flex flex-wrap items-end gap-2 text-xs" onSubmit={(e) => { e.preventDefault(); setLayout({ ...layout, symbol: symbolText.trim().toUpperCase() }); }}>
              <label className="flex flex-col gap-1 text-fg-muted">{t("workspace.symbol")}
                <input ref={symbolRef} value={symbolText} onChange={(e) => setSymbolText(e.target.value)} maxLength={40}
                       className="min-h-[44px] rounded border border-border bg-surface-2 px-2 py-1 text-fg sm:min-h-0" />
              </label>
              <label className="flex flex-col gap-1 text-fg-muted">{t("workspace.timeframe")}
                <select value={layout.timeframe} onChange={(e) => setLayout({ ...layout, timeframe: e.target.value })}
                        className="min-h-[44px] rounded border border-border bg-surface-2 px-2 py-1 text-fg sm:min-h-0">
                  {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
                </select>
              </label>
            </form>
            {layout.symbol
              ? <ProChart candles={chart.candles} symbol={layout.symbol} timeframe={layout.timeframe} timeframes={TIMEFRAMES}
                          onTimeframeChange={(tf) => setLayout({ ...layout, timeframe: tf })} onLoadOlder={chart.loadOlder}
                          loadingOlder={chart.loadingOlder} olderExhausted={chart.exhausted} height={phone ? 280 : 420} />
              : <p className="text-xs text-fg-muted">{t("workspace.noChart")}</p>}
            {chart.error && <p className="text-xs text-down">{chart.error}</p>}
          </section>
        )}
        {layout.panels.ticket && !phone && <aside aria-label={t("workspace.panel.ticket")}>{ticket}</aside>}
      </div>
      {layout.panels.positions && <PositionsBoardCard key={boardKey} />}
      {layout.panels.ticket && phone && (
        <>
          <button type="button" onClick={() => setSheet(true)}
                  className="fixed bottom-20 right-4 z-30 min-h-[44px] rounded-full border border-info bg-surface-1 px-4 py-2 text-sm text-info shadow-card">
            {t("workspace.ticketSheet")}
          </button>
          {sheet && (
            <div className="fixed inset-x-0 bottom-0 z-40 max-h-[85vh] overflow-y-auto rounded-t-2xl border-t border-border bg-surface-1 p-3 shadow-card"
                 role="dialog" aria-label={t("workspace.ticketSheet")} data-testid="ticket-sheet">
              <div className="mb-2 flex justify-end">
                <button type="button" onClick={() => setSheet(false)} className="min-h-[44px] rounded border border-border px-3 text-sm text-fg">{t("workspace.closeSheet")}</button>
              </div>
              {ticket}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function WorkspaceStatus({ market, lockout, staleMinutes }: { market: WorkerStatus | null; lockout: string | null; staleMinutes: number | null }) {
  const t = useProgressionT();
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="workspace-status">
      {market
        ? <span className={`rounded px-2 py-0.5 ${market.market_open ? "bg-up/15 text-up" : "bg-surface-2 text-fg-muted"}`}>
            {t("workspace.market", { status: market.market_status })}
            {!market.market_open && market.next_market_open ? ` · ${t("workspace.nextOpen", {
              when: new Date(market.next_market_open).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", weekday: "short", hour: "2-digit", minute: "2-digit" }) })}` : ""}
          </span>
        : <span className="rounded bg-surface-2 px-2 py-0.5 text-fg-muted">{t("workspace.marketUnknown")}</span>}
      {lockout != null && <span className="rounded bg-warn/15 px-2 py-0.5 text-warn">{t("workspace.lockout", { reason: lockout })}</span>}
      {staleMinutes != null && <span className="rounded bg-warn/15 px-2 py-0.5 text-warn">{t("workspace.stale", { minutes: staleMinutes })}</span>}
    </div>
  );
}

export function ShortcutHelp() {
  const t = useProgressionT();
  return (
    <ul className="space-y-0.5 rounded border border-border bg-surface-1 p-2 text-xs text-fg" aria-label={t("workspace.shortcuts")} data-testid="workspace-help">
      {(["ticket", "chart", "exit", "flatten", "help"] as const).map((k) => <li key={k}>{t(`workspace.keys.${k}`)}</li>)}
    </ul>
  );
}
