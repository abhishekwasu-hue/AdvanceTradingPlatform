import { errorSentence } from "../api/errors";
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight, BarChart3, Bell, Bot, Briefcase, GitMerge, History,
  Layers, Link2, PieChart, Rocket, Server, ShieldCheck, Sparkles, Target, TrendingUp, Wallet, Zap, type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import RenewBanner from "../broker/RenewBanner";
import { useAuth } from "../auth/AuthContext";
import { Card, signClass, signTone } from "../components/ui";
import { Badge, Button, PageHeader, SkeletonRows } from "../components/primitives";
import { EmptyStateV2 } from "../dashboard/CardV2";
import MarketPulseCard from "../components/MarketPulseCard";
import ContextPanelCard from "../progression/ContextPanelCard";
import OiBannersCard from "../components/OiBannersCard";
import GetStartedCardV2 from "../setup/GetStartedCardV2";
import { formatINR, formatNumber, formatPrice } from "../ui/format";
import type { Page } from "../components/Sidebar";
import type {
  AnalyticsSummary, BrokerAccount, Deployment, NotificationEntry, PortfolioExposure, StrategyInfo, TradeRecord, WorkerStatus,
} from "../types";

/**
 * Phase AM: the designer dashboard. One glance answers "is the engine alive, how is the book doing,
 * what is running, what needs me" - each family of facts in its own colour so the eye can find it:
 * emerald = money made / healthy, rose = money lost / stopped, sky = market & win rate, violet = open
 * risk, amber = automation, orange = brokers, fuchsia = strategies, teal = data. Every number is the
 * backend's; nothing here is decorative data.
 *
 * P3 (the owner's "simple front page"): the Get started card sits at the top while setup is not done (three steps,
 * src/setup/GetStartedCardV2.tsx); the full go-live checklist moved to Settings → Advanced ("Prepare for LIVE"), and
 * nothing about LIVE shows here while the trader has no LIVE deployment.
 */

type Tone = "emerald" | "rose" | "sky" | "violet" | "amber" | "orange" | "fuchsia" | "teal" | "indigo" | "lime";

// P1.2: colour carries meaning only - up (money made / healthy), down (money lost / stopped), warn (attention).
// Every other family of facts uses the neutral surface with the brand accent; the tone names stay for the call sites.
const NEUTRAL = { ring: "border-border", text: "text-fg", bg: "bg-surface-2", icon: "text-brand", bar: "bg-brand" };
const TONE: Record<Tone, { ring: string; text: string; bg: string; icon: string; bar: string }> = {
  emerald: { ring: "border-up/40", text: "text-up", bg: "bg-up/10", icon: "text-up", bar: "bg-up" },
  rose:    { ring: "border-down/40", text: "text-down", bg: "bg-down/10", icon: "text-down", bar: "bg-down" },
  amber:   { ring: "border-warn/40", text: "text-warn", bg: "bg-warn/10", icon: "text-warn", bar: "bg-warn" },
  sky: NEUTRAL, violet: NEUTRAL, orange: NEUTRAL, fuchsia: NEUTRAL, teal: NEUTRAL, indigo: NEUTRAL, lime: NEUTRAL,
};
// Categories (symbols, strategy families) carry no up/down meaning: neutral shades only.
const PALETTE: Tone[] = ["sky", "violet", "teal", "indigo", "fuchsia", "orange", "lime"];

const ENGINES: { icon: LucideIcon; tone: Tone; title: string; description: string }[] = [
  { icon: Activity, tone: "sky", title: "Strategy Engine", description: "Inbuilt multi-timeframe and indicator scalpers plus your own DSL strategies" },
  { icon: ShieldCheck, tone: "sky", title: "Risk Guardian", description: "Eight-level limits, drawdown ladder, cooldowns, kill switches - before every order" },
  { icon: Link2, tone: "sky", title: "Broker Layer", description: "Seven real adapters behind one interface, per-account sessions and reconciliation" },
  { icon: TrendingUp, tone: "sky", title: "Price Action + S/R", description: "Market structure, candlestick patterns, support and resistance zones" },
  { icon: Layers, tone: "sky", title: "Option Intelligence", description: "PCR, max pain, OI build-up, strike selection, thirteen spread structures" },
  { icon: Target, tone: "sky", title: "Signal Scoring", description: "One weighted confidence score across every engine, with the reasons" },
  { icon: History, tone: "sky", title: "Backtest Lab", description: "Event-driven simulation, Monte Carlo, walk-forward, parameter optimisation" },
  { icon: Bot, tone: "sky", title: "Autopilot + AI", description: "Worker trades deployments every cycle; AI Copilot drafts behind a human approval gate" },
];

/** Whole rupees, Indian grouping, "−" for a loss (the money rule, ui/format.ts). */
const money = (v: number | null | undefined) => formatINR(v);
const pct = (v: number | null | undefined, digits = 1) => (v == null ? "-" : `${formatNumber(v * (Math.abs(v) <= 1 ? 100 : 1), digits)}%`);
const ago = (iso: string | null) => {
  if (!iso) return "";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`;
};

function KpiTile({ icon: Icon, tone, label, value, sub, onClick }: { icon: LucideIcon; tone: Tone; label: string; value: ReactNode; sub?: ReactNode; onClick?: () => void }) {
  const t = TONE[tone];
  return (
    <button onClick={onClick} disabled={!onClick}
      className={`group relative overflow-hidden rounded-xl border ${t.ring} bg-surface-1 text-left p-4 shadow-card transition-colors ${onClick ? "hover:bg-surface-2" : "cursor-default"}`}>
      <div className="relative flex items-start justify-between gap-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-fg-muted">{label}</div>
          <div className={`font-tabular mt-1.5 text-2xl font-extrabold leading-none ${t.text}`}>{value}</div>
          {sub && <div className="mt-1.5 text-xs text-fg-muted">{sub}</div>}
        </div>
        <div className={`shrink-0 rounded-xl ${t.bg} ${t.icon} p-2.5`}><Icon size={18} /></div>
      </div>
    </button>
  );
}

// Up / down keep their meaning; categories are shades of the brand colour, not a rainbow.
const RING_COLORS: Record<Tone, string> = {
  emerald: "rgb(var(--up))", rose: "rgb(var(--down))", sky: "rgb(var(--brand))", violet: "rgb(var(--brand) / 0.6)",
  fuchsia: "rgb(var(--brand) / 0.35)", amber: "rgb(var(--fg-muted))", orange: "rgb(var(--fg-muted) / 0.6)",
  teal: "rgb(var(--brand) / 0.8)", indigo: "rgb(var(--brand) / 0.5)", lime: "rgb(var(--fg-muted) / 0.4)",
};

function Ring({ segments, size = 128, label, sub }: { segments: { value: number; tone: Tone }[]; size?: number; label: ReactNode; sub?: string }) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  const r = 46, c = 2 * Math.PI * r;
  let offset = 0;
  const COLORS = RING_COLORS;
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg viewBox="0 0 120 120" className="w-full h-full -rotate-90">
        <circle cx="60" cy="60" r={r} fill="none" stroke="rgb(var(--surface-3))" strokeWidth="12" />
        {segments.filter((s) => s.value > 0).map((s, i) => {
          const len = (s.value / total) * c;
          const el = <circle key={i} cx="60" cy="60" r={r} fill="none" stroke={COLORS[s.tone]} strokeWidth="12" strokeLinecap="butt"
                             strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset} />;
          offset += len;
          return el;
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <div className={`font-tabular font-extrabold text-fg ${String(label).length > 4 ? "text-sm" : "text-xl"}`}>{label}</div>
        {sub && <div className="text-xs uppercase tracking-wider text-fg-muted">{sub}</div>}
      </div>
    </div>
  );
}

function Bars({ rows, valueLabel, signed = false }: { rows: { key: string; value: number; tone: Tone; hint?: string }[]; valueLabel: (v: number) => string; signed?: boolean }) {
  const max = Math.max(1e-9, ...rows.map((r) => Math.abs(r.value)));
  return (
    <div className="space-y-2">
      {rows.map((r) => {
        const sign = signTone(r.value, 0);
        const tone: Tone = signed ? (sign === "up" ? "emerald" : sign === "down" ? "rose" : "sky") : r.tone;
        return (
          <div key={r.key} className="flex items-center gap-3 text-xs">
            <div className="w-36 shrink-0 truncate text-fg-muted" title={r.key}>{r.key}</div>
            <div className="flex-1 h-2.5 rounded-full bg-surface-3 overflow-hidden">
              <div className={`h-2.5 rounded-full ${TONE[tone].bar} transition-all`} style={{ width: `${Math.max(3, (Math.abs(r.value) / max) * 100)}%` }} />
            </div>
            <div className={`w-24 shrink-0 text-right font-tabular font-semibold ${signed ? signClass(r.value, 0) : TONE[tone].text}`}>{valueLabel(r.value)}</div>
            {r.hint && <div className="w-14 shrink-0 text-right text-xs text-fg-muted">{r.hint}</div>}
          </div>
        );
      })}
    </div>
  );
}

const SEVERITY: Record<string, Tone> = { INFO: "sky", WARNING: "amber", CRITICAL: "rose", EMERGENCY: "rose" };
const STATUS_TONE: Record<string, Tone> = { ACTIVE: "emerald", PAUSED: "amber", STOPPED: "rose" };
type ReadFlags = { exposure: boolean; positions: boolean; deployments: boolean; alerts: boolean };
const NOT_READ: ReadFlags = { exposure: false, positions: false, deployments: false, alerts: false };

export default function DashboardPage({ onNavigate }: { onNavigate?: (page: Page) => void } = {}) {
  const { user } = useAuth();
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [brokers, setBrokers] = useState<string[]>([]);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [exposure, setExposure] = useState<PortfolioExposure | null>(null);
  const [positions, setPositions] = useState<TradeRecord[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [alerts, setAlerts] = useState<NotificationEntry[]>([]);
  // M63 I-7: which of the book's reads have answered once (placeholder rows until then, never an empty line first).
  const [read, setRead] = useState<ReadFlags>(NOT_READ);

  useEffect(() => {
    Promise.all([api.listStrategies(), api.availableBrokers(), api.health()])
      .then(([s, b]) => { setStrategies(s); setBrokers(b.brokers); setHealthy(true); })
      .catch((e) => { setError(errorSentence(e)); setHealthy(false); });
  }, []);

  useEffect(() => {
    if (!user) { setWorker(null); setSummary(null); setExposure(null); setPositions([]); setDeployments([]); setAccounts([]); setAlerts([]); setRead(NOT_READ); return; }
    const seen = (k: keyof ReadFlags) => () => setRead((r) => (r[k] ? r : { ...r, [k]: true }));
    const load = () => {
      api.workerStatus().then(setWorker).catch(() => setWorker(null));
      api.getAnalyticsSummary().then(setSummary).catch(() => setSummary(null));
      api.portfolioExposure(true).then(setExposure).catch(() => setExposure(null)).finally(seen("exposure"));
      api.listPositions().then(setPositions).catch(() => setPositions([])).finally(seen("positions"));
      api.listDeployments().then(setDeployments).catch(() => setDeployments([])).finally(seen("deployments"));
      api.listAccounts().then(setAccounts).catch(() => setAccounts([]));
      api.listNotifications().then((n) => setAlerts(n.slice(0, 6))).catch(() => setAlerts([])).finally(seen("alerts"));
    };
    load();
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, [user]);

  const mtf = strategies.filter((s) => s.category === "multi_timeframe").length;
  const indicator = strategies.filter((s) => s.category === "indicator_based").length;
  const other = Math.max(0, strategies.length - mtf - indicator);
  const live = deployments.filter((d) => d.mode === "LIVE" && d.status === "ACTIVE").length;
  const paper = deployments.filter((d) => d.mode === "PAPER" && d.status === "ACTIVE").length;
  const paused = deployments.filter((d) => d.status === "PAUSED").length;
  // P3: in PAPER (no LIVE deployment at all) the front page says nothing about LIVE.
  const anyLive = deployments.some((d) => d.mode === "LIVE");
  const balance = accounts.reduce((a, b) => a + (b.available_balance ?? 0), 0);
  const netPnl = summary?.net_pnl ?? null;
  const unread = alerts.filter((a) => !a.read).length;
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const name = user?.email?.split("@")[0] ?? "trader";

  const byStrategy = useMemo(() => (summary?.by_strategy ?? []).slice().sort((a, b) => Math.abs(b.net_pnl) - Math.abs(a.net_pnl)).slice(0, 6), [summary]);
  const bySymbol = useMemo(() => (exposure?.by_symbol ?? []).slice().sort((a, b) => b.notional - a.notional).slice(0, 6), [exposure]);

  return (
    <div className="space-y-5">
      <PageHeader
        title={`${greeting}, ${name}`}
        description={user ? "Your book, your automation and your alerts on one screen. Numbers refresh every 30 seconds." : "Log in to see your book; the engines below are live either way."}
        meta={<>
          <Badge tone={healthy ? "neutral" : healthy === false ? "down" : "neutral"}><Server size={11} />Backend {healthy === null ? "…" : healthy ? "online" : "offline"}</Badge>
          {user && <Badge tone={worker?.running ? "neutral" : "down"}><Bot size={11} />Autopilot {worker == null ? "…" : worker.running ? `${worker.market_open ? "trading" : "idle"} · beat ${worker.seconds_since_heartbeat ?? "?"}s ago` : "stopped"}</Badge>}
          {worker && <Badge><Zap size={11} />{worker.market_status}</Badge>}
          {exposure?.warnings?.slice(0, 1).map((w) => <Badge key={w} tone="warn"><AlertTriangle size={11} />{w}</Badge>)}
        </>}
        actions={onNavigate && <>
          <Button variant="primary" icon={<Rocket size={15} />} onClick={() => onNavigate("deployments")}>Deploy a strategy</Button>
          <Button icon={<Briefcase size={15} />} onClick={() => onNavigate("positions")}>Positions</Button>
          <Button icon={<Sparkles size={15} />} onClick={() => onNavigate("copilot")}>AI Copilot</Button>
        </>}
      />

      {/* P2b: one "Renew" for a broker that needs today's login (opens the same Connect panel as the Broker page). */}
      {user && <RenewBanner />}
      {user && <GetStartedCardV2 />}

      {error && (
        <div className="rounded-xl border border-down/40 bg-down/10 px-3 py-2 text-sm text-down">
          Some dashboard data could not be loaded: {error}
        </div>
      )}

      {/* KPI tiles */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <KpiTile icon={netPnl != null && netPnl < 0 ? ArrowDownRight : ArrowUpRight} tone={netPnl == null || netPnl === 0 ? "sky" : netPnl < 0 ? "rose" : "emerald"} label="Net P&L"
                 value={user ? (summary ? money(netPnl) : "…") : "Log in"} sub={summary ? `${summary.closed_trades} closed · PF ${formatNumber(summary.profit_factor, 2)}` : "all trades, net of charges"} onClick={onNavigate && (() => onNavigate("analytics"))} />
        <KpiTile icon={Target} tone="sky" label="Win rate" value={user && summary ? pct(summary.win_rate) : "…"}
                 sub={summary ? `avg win ${money(summary.avg_win)} · avg loss ${money(summary.avg_loss)}` : "closed trades"} onClick={onNavigate && (() => onNavigate("analytics"))} />
        <KpiTile icon={Briefcase} tone="violet" label="Open positions" value={user && read.positions ? positions.length : "…"}
                 sub={exposure ? `${money(exposure.gross_notional)} gross · ${pct(exposure.risk_pct_of_capital)} at stops` : "across every account"} onClick={onNavigate && (() => onNavigate("positions"))} />
        <KpiTile icon={Bot} tone="sky" label="Deployments" value={user && read.deployments ? `${live + paper}` : "…"}
                 sub={user && read.deployments ? <>{anyLive && <><span className="font-semibold text-fg">{live} LIVE</span> · </>}<span className="text-fg-muted">{paper} PAPER</span>{paused ? <> · <span className="text-warn">{paused} paused</span></> : null}</> : "active strategies"} onClick={onNavigate && (() => onNavigate("deployments"))} />
        <KpiTile icon={Wallet} tone="orange" label="Broker funds" value={user ? (accounts.length ? money(balance) : "-") : "…"}
                 sub={`${accounts.length} account${accounts.length === 1 ? "" : "s"} · ${brokers.length} adapters`} onClick={onNavigate && (() => onNavigate("broker"))} />
        <KpiTile icon={GitMerge} tone="fuchsia" label="Strategies" value={strategies.length || "…"}
                 sub={`${mtf} multi-timeframe · ${indicator} indicator`} onClick={onNavigate && (() => onNavigate("strategies"))} />
      </div>

      {user && <ContextPanelCard />}
      {user && <MarketPulseCard onNavigate={onNavigate} />}
      {user && <OiBannersCard />}

      {/* Charts row */}
      <div className="grid lg:grid-cols-3 gap-4">
        <Card title="P&L by strategy" className="lg:col-span-2">
          {user && byStrategy.length > 0 ? (
            <Bars signed rows={byStrategy.map((g, i) => ({ key: g.key, value: g.net_pnl, tone: PALETTE[i % PALETTE.length], hint: `${g.trades} tr · ${pct(g.win_rate, 0)}` }))} valueLabel={(v) => money(v)} />
          ) : (
            <div className="flex h-32 items-center justify-center text-xs text-fg-muted">{user ? "No closed trades yet - the first PAPER session fills this in." : "Log in to see your strategies' P&L."}</div>
          )}
        </Card>
        <Card title="Strategy mix">
          <div className="flex items-center gap-5">
            <Ring segments={[{ value: mtf, tone: "sky" }, { value: indicator, tone: "violet" }, { value: other, tone: "fuchsia" }]} label={strategies.length || "…"} sub="inbuilt" />
            <div className="space-y-2 text-xs">
              {[{ l: "Multi-timeframe", v: mtf, t: "sky" as Tone }, { l: "Indicator-based", v: indicator, t: "violet" as Tone }, ...(other ? [{ l: "Other", v: other, t: "fuchsia" as Tone }] : [])].map((r) => (
                <div key={r.l} className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ background: RING_COLORS[r.t] }} /><span className="text-fg-muted">{r.l}</span><span className="ml-auto font-tabular font-bold text-fg">{r.v}</span></div>
              ))}
              {onNavigate && <button onClick={() => onNavigate("strategy-builder")} className="mt-1 flex items-center gap-1 text-brand hover:underline">Build your own <ArrowRight size={11} /></button>}
            </div>
          </div>
        </Card>
      </div>

      {user && (
        <div className="grid lg:grid-cols-3 gap-4">
          {/* Exposure */}
          <Card title="Exposure by symbol">
            {!read.exposure ? (
              <SkeletonRows rows={3} label="Loading your exposure" />
            ) : bySymbol.length > 0 && exposure ? (
              <>
                <div className="mb-3 flex items-center gap-4">
                  <Ring size={96} segments={[{ value: exposure.long_notional, tone: "emerald" }, { value: exposure.short_notional, tone: "rose" }]} label={pct(exposure.gross_pct_of_capital, 0)} sub="of capital" />
                  <div className="text-xs space-y-1">
                    <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-up" /><span className="text-fg-muted">Long</span><span className="ml-auto font-tabular text-up">{money(exposure.long_notional)}</span></div>
                    <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-down" /><span className="text-fg-muted">Short</span><span className="ml-auto font-tabular text-down">{money(exposure.short_notional)}</span></div>
                    <div className="text-fg-muted">largest symbol {pct(exposure.largest_symbol_pct, 0)} · unrealised <span className={signClass(exposure.unrealised_pnl, 0)}>{money(exposure.unrealised_pnl)}</span></div>
                  </div>
                </div>
                <Bars rows={bySymbol.map((s, i) => ({ key: s.symbol, value: s.notional, tone: PALETTE[i % PALETTE.length], hint: `${s.positions} pos` }))} valueLabel={(v) => money(v)} />
              </>
            ) : (
              <EmptyStateV2 text="No open positions - exposure is empty." />
            )}
          </Card>

          {/* Deployments */}
          <Card title="Running deployments">
            {!read.deployments ? (
              <SkeletonRows rows={3} label="Loading your deployments" />
            ) : deployments.length === 0 ? (
              <EmptyStateV2 text="Nothing deployed yet." action={onNavigate && { label: "Deploy the first one", onClick: () => onNavigate("deployments") }} />
            ) : (
              <ul className="space-y-2">
                {deployments.slice(0, 6).map((d) => (
                  <li key={d.id} className="flex items-center gap-2 rounded-xl border border-border bg-surface-2 px-3 py-2 text-xs">
                    <span className={`rounded-md px-1.5 py-0.5 font-bold ${d.mode === "LIVE" ? "bg-warn/15 text-warn" : "border border-border text-fg-muted"}`}>{d.mode}</span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-semibold text-fg">{d.strategy_id} <span className="text-fg-muted">on</span> {d.symbol}</div>
                      <div className="truncate text-xs text-fg-muted">{d.timeframe} · {d.broker_name ?? "paper"} · {d.open_positions} open{d.last_error ? ` · ${d.last_error}` : ""}</div>
                    </div>
                    <span className={`h-2 w-2 rounded-full ${TONE[STATUS_TONE[d.status] ?? "sky"].bar}`} title={d.status} />
                  </li>
                ))}
                {deployments.length > 6 && onNavigate && <button onClick={() => onNavigate("deployments")} className="text-xs text-brand hover:underline">+{deployments.length - 6} more</button>}
              </ul>
            )}
          </Card>

          {/* Positions + alerts */}
          <div className="space-y-4">
            <Card title="Open positions">
              {!read.positions ? (
                <SkeletonRows rows={2} label="Loading your open positions" />
              ) : positions.length === 0 ? (
                <EmptyStateV2 text="Flat. Nothing at risk right now." />
              ) : (
                <ul className="space-y-1.5">
                  {positions.slice(0, 5).map((p) => (
                    <li key={p.id} className="flex items-center gap-2 text-xs">
                      <span className={`rounded-md px-1.5 py-0.5 font-bold ${p.direction === "LONG" ? "bg-up/20 text-up" : "bg-down/20 text-down"}`}>{p.direction === "LONG" ? "L" : "S"}</span>
                      <span className="min-w-0 flex-1 truncate font-semibold text-fg">{p.symbol}</span>
                      <span className="font-tabular text-fg-muted">{p.quantity} @ {formatPrice(p.entry_price)}</span>
                      <span className={`rounded px-1 text-xs ${p.mode === "LIVE" ? "font-semibold text-warn" : "text-fg-muted"}`}>{p.mode}</span>
                    </li>
                  ))}
                  {positions.length > 5 && onNavigate && <button onClick={() => onNavigate("positions")} className="text-xs text-brand hover:underline">+{positions.length - 5} more</button>}
                </ul>
              )}
            </Card>
            <Card title={`Latest alerts${unread ? ` · ${unread} unread` : ""}`}>
              {!read.alerts ? (
                <SkeletonRows rows={2} label="Loading your alerts" />
              ) : alerts.length === 0 ? (
                <EmptyStateV2 text="Quiet. Entries, exits and risk events will show here." />
              ) : (
                <ul className="space-y-1.5">
                  {alerts.map((a) => (
                    <li key={a.id} className="flex items-start gap-2 text-xs">
                      <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${TONE[SEVERITY[a.severity] ?? "sky"].bar}`} />
                      <div className="min-w-0 flex-1">
                        <div className={`truncate ${a.read ? "text-fg-muted" : "font-semibold text-fg"}`}>{a.title}</div>
                        <div className="text-xs text-fg-muted">{a.event_type} · {ago(a.created_at)}</div>
                      </div>
                    </li>
                  ))}
                  {onNavigate && <button onClick={() => onNavigate("notifications")} className="flex items-center gap-1 text-xs text-brand hover:underline"><Bell size={11} /> All notifications</button>}
                </ul>
              )}
            </Card>
          </div>
        </div>
      )}

      <Card title="Engines behind this console">
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {ENGINES.map(({ icon: Icon, tone, title, description }) => {
            const t = TONE[tone];
            return (
              <div key={title} className={`relative overflow-hidden rounded-xl border ${t.ring} bg-surface-2 p-3.5`}>
                <div className="relative">
                  <div className={`mb-2 inline-flex rounded-xl ${t.bg} ${t.icon} p-2`}><Icon size={16} /></div>
                  <div className="text-sm font-bold text-fg">{title}</div>
                  <div className="mt-0.5 text-xs leading-relaxed text-fg-muted">{description}</div>
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      {!user && (
        <div className="grid sm:grid-cols-3 gap-3">
          {[
            { icon: BarChart3, tone: "sky" as Tone, label: "MTF combos", value: mtf || "…" },
            { icon: PieChart, tone: "violet" as Tone, label: "Indicator scalpers", value: indicator || "…" },
            { icon: Link2, tone: "orange" as Tone, label: "Broker adapters", value: brokers.length || "…" },
          ].map((k) => <KpiTile key={k.label} icon={k.icon} tone={k.tone} label={k.label} value={k.value} />)}
        </div>
      )}
    </div>
  );
}
