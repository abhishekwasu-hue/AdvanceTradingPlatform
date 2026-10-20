import { ChevronRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router";
import { useAuth } from "../auth/AuthContext";
import type { PageProps } from "../routes";
import { BrokerGateView, CONNECT_BROKER_PATH, SIGN_IN_PATH, useBrokerLogin } from "../ui/BrokerGate";
import { useBrokerSession, type BrokerGateState } from "../ui/brokerSession";
import { useFeatures } from "../ui/useFeatures";
import { ActiveSignalsV2, OpenPositionsV2, TodaySummaryV2, type Rows } from "./BlocksV2";
import { CARD } from "./CardV2";
import MarketStripV2 from "./MarketStripV2";
import {
  attentionRows, brokerChip, greeting, marketLabel, oiLines, positionRows, setupProgress, signalsToday, todayPnl, todayTiles,
  type PositionRow, type SetupSignals, type SignalRow,
} from "./model";
import NeedsAttentionV2 from "./NeedsAttentionV2";
import { dataOf, useDashboardData, type Loadable } from "./useDashboardData";

/**
 * UI2-2 (UI v2 proposal §5, §8, §9; MOBILE_APP_PLAN Phase A): the v2 dashboard, "Today" - "What needs me right now?".
 * Shown only while `ui_v2` is on (the v2 shell picks it for `/`, src/shell/pagesV2.ts); with the flag off today's
 * DashboardPage renders exactly as before.
 *
 * Order, top to bottom: greeting, Setup chip (until setup is done), market strip, Needs attention, Open positions, Active
 * signals, Today. Desktop: the strip runs full width, then Needs attention and Open positions on the left (2/3) and
 * Active signals and Today on the right (1/3). Phone: one column in that order.
 *
 * Broker states (the dashboard handles them itself, so the strip still works before the day's broker login, UI-3):
 * - signed out: the sign-in page (UI-14 - no signed-out dashboard);
 * - checking: the gate's neutral placeholder; the check failed: the gate's "Try again" (fails closed);
 * - no broker stored: the gate's "Connect your broker" with one action, and nothing else (no sample data);
 * - stored, not logged in today: the dashboard, with "Log in to <Broker>" in Needs attention and the strip's levels
 *   from market memory, marked with their time;
 * - connected: the dashboard.
 * Removed from today's page (§5 "Removed or moved"): backend and worker status, the six coloured KPI tiles, the header
 * buttons, Market pulse, the OI banners card, P&L by strategy, strategy mix, exposure by symbol, running deployments,
 * the alerts list, the go-live checklist, "Engines behind this console" and the signed-out tiles.
 */

const BLOCKING: ReadonlySet<BrokerGateState["kind"]> = new Set(["loading", "signed-out", "no-broker", "unavailable"]);

/** The time, re-read every minute (the greeting and "first one expires in 8 min"). */
function useNow(stepMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), stepMs);
    return () => window.clearInterval(id);
  }, [stepMs]);
  return now;
}

function rowsOf<T, R>(l: Loadable<T>, map: (data: T) => { rows: R[]; total: number }): Rows<R> {
  if (l.status !== "ok") return l;
  return { status: "ok", ...map(l.data) };
}

function SetupChipV2({ label, next, to }: { label: string; next: string; to: string }) {
  return (
    <Link to={to} data-block="setup"
      className={`${CARD} flex min-h-12 items-center gap-space-3 px-space-4 py-space-2 transition-colors duration-fast hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:px-space-5`}>
      <span className="inline-flex h-7 shrink-0 items-center rounded-pill bg-brand/10 px-space-3 text-t13 font-semibold text-brand">{label}</span>
      <span className="min-w-0 flex-1 text-t14 text-fg">{next}</span>
      <ChevronRight size={18} aria-hidden className="shrink-0 text-fg-muted" />
    </Link>
  );
}

export default function DashboardV2(_props: PageProps = {}) {
  const { user, loading } = useAuth();
  const features = useFeatures();
  const navigate = useNavigate();
  const { state: broker, refresh: refreshBroker, rows: brokerRows } = useBrokerSession();
  const live = !!user && !BLOCKING.has(broker.kind);
  const data = useDashboardData(live);
  const login = useBrokerLogin(() => { refreshBroker(); data.reload(); });
  const now = useNow();

  const hello = greeting(now, user?.email);
  const approvalsPath = features.on("ai_copilot") ? "/copilot/watchtower" : "/notifications";
  const actions = dataOf(data.actions);
  const deployments = dataOf(data.deployments);
  const exposure = dataOf(data.exposure);
  const strip = dataOf(data.strip);
  const signals = useMemo(() => signalsToday(actions, now), [actions, now]);
  const attentionKnown = data.actions.status !== "loading" && data.deployments.status !== "loading";
  const attention = attentionKnown ? attentionRows({
    nowMs: now, broker, brokerRows, actions, deployments, risk: dataOf(data.risk), exposure, unread: dataOf(data.unread),
    status: dataOf(data.status), emailVerified: user?.email_verified, approvalsPath,
  }) : null;
  const channels = dataOf(data.channels);
  const setupSignals: SetupSignals | null = brokerRows && deployments && channels
    ? { brokerStored: brokerRows.length > 0, strategyDeployed: deployments.length > 0, alertsOn: channels.some((c) => c.enabled) }
    : null;
  const setup = setupProgress(setupSignals);
  const positions: Rows<PositionRow> = rowsOf(data.positions, (p) => ({ rows: positionRows(p, exposure), total: p.length }));
  const signalRows: Rows<SignalRow> = rowsOf(data.actions, () => ({ rows: signals.rows, total: signals.count }));
  const tilesLoading = data.exposure.status === "loading" && data.summary.status === "loading";

  if (!loading && !user) return <Navigate to={SIGN_IN_PATH} replace />;

  return (
    <div className="mx-auto w-full space-y-space-4 md:space-y-space-6" data-page="dashboard-v2">
      <header>
        <p className="text-t13 text-fg-muted">
          {hello.date}<span className="md:hidden"> · {hello.time}</span>
        </p>
        <h1 className="mt-px text-t20 font-semibold tracking-tight text-fg md:text-t24">{hello.hello}</h1>
      </header>

      {BLOCKING.has(broker.kind) ? (
        <BrokerGateView state={broker} onConnect={() => navigate(CONNECT_BROKER_PATH)} onSignIn={() => navigate(SIGN_IN_PATH)} onRetry={refreshBroker} />
      ) : (
        <>
          {setup && <SetupChipV2 {...setup} />}
          <MarketStripV2 strip={data.strip} oi={oiLines(dataOf(data.oi))} marketText={strip ? marketLabel(strip.market, now) : null}
            broker={brokerChip(broker)} pnl={todayPnl(exposure)} nowMs={now} />
          <div className="grid grid-cols-1 gap-space-4 lg:grid-cols-3 lg:items-start lg:gap-space-6">
            <div className="min-w-0 space-y-space-4 lg:col-span-2 lg:space-y-space-6">
              <NeedsAttentionV2 rows={attention} loginBroker={broker.kind === "login-needed" ? broker.broker : null} login={login} />
              <OpenPositionsV2 data={positions} />
            </div>
            <div className="min-w-0 space-y-space-4 lg:space-y-space-6">
              <ActiveSignalsV2 data={signalRows} />
              <TodaySummaryV2 tiles={todayTiles(exposure, dataOf(data.summary), dataOf(data.risk), signals)} loading={tilesLoading} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
