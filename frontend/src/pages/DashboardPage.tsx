import {
  Activity, AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight, BarChart3, Bell, Bot, Briefcase, GitMerge, History,
  Layers, Link2, PieChart, Rocket, Server, ShieldCheck, Sparkles, Target, TrendingUp, Wallet, Zap, type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card } from "../components/ui";
import { Badge, Button, PageHeader } from "../components/primitives";
import GoLiveChecklist from "../components/GoLiveChecklist";
import MarketPulseCard from "../components/MarketPulseCard";
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
 */

type Tone = "emerald" | "rose" | "sky" | "violet" | "amber" | "orange" | "fuchsia" | "teal" | "indigo" | "lime";

// P1.2: colour carries meaning only - up (money made / healthy), down (money lost / stopped), warn (attention).
// Every other family of facts uses the neutral surface with the brand accent; the tone names stay for the call sites.
const NEUTRAL = { ring: "border-border", text: "text-fg", bg: "bg-surface-2 text-brand", bar: "bg-brand", glow: "", grad: "from-transparent to-transparent" };
const TONE: Record<Tone, { ring: string; text: string; bg: string; bar: string; glow: string; grad: string }> = {
  emerald: { ring: "border-up/40", text: "text-up", bg: "bg-up/10", bar: "bg-up", glow: "", grad: "from-transparent to-transparent" },
  rose:    { ring: "border-down/40", text: "text-down", bg: "bg-down/10", bar: "bg-down", glow: "", grad: "from-transparent to-transparent" },
  amber:   { ring: "border-warn/40", text: "text-warn", bg: "bg-warn/10", bar: "bg-warn", glow: "", grad: "from-transparent to-transparent" },
  sky: NEUTRAL, violet: NEUTRAL, orange: NEUTRAL, fuchsia: NEUTRAL, teal: NEUTRAL, indigo: NEUTRAL, lime: NEUTRAL,
};
const PALETTE: Tone[] = ["sky", "violet", "amber", "emerald", "fuchsia", "orange", "teal", "indigo", "lime", "rose"];

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

const money = (v: number | null | undefined, digits = 0) =>
  v == null ? "-" : `${v < 0 ? "-" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: digits })}`;
const pct = (v: number | null | undefined, digits = 1) => (v == null ? "-" : `${(v * (Math.abs(v) <= 1 ? 100 : 1)).toFixed(digits)}%`);
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
      <div className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${t.grad}`} />
      <div className="relative flex items-start justify-between gap-2">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">{label}</div>
          <div className={`font-tabular mt-1.5 text-2xl font-extrabold leading-none ${t.text}`}>{value}</div>
          {sub && <div className="mt-1.5 text-[11px] text-fg-muted">{sub}</div>}
        </div>
        <div className={`shrink-0 rounded-xl ${t.bg} ${t.text} p-2.5`}><Icon size={18} /></div>
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
        <div className={`font-tabular font-extrabold text-slate-100 ${String(label).length > 4 ? "text-sm" : "text-xl"}`}>{label}</div>
        {sub && <div className="text-[10px] uppercase tracking-wider text-slate-300">{sub}</div>}
      </div>
    </div>
  );
}

function Bars({ rows, valueLabel, signed = false }: { rows: { key: string; value: number; tone: Tone; hint?: string }[]; valueLabel: (v: number) => string; signed?: boolean }) {
  const max = Math.max(1e-9, ...rows.map((r) => Math.abs(r.value)));
  return (
    <div className="space-y-2">
      {rows.map((r) => {
        const tone = signed ? (r.value >= 0 ? "emerald" : "rose") : r.tone;
        return (
          <div key={r.key} className="flex items-center gap-3 text-xs">
            <div className="w-36 shrink-0 truncate text-slate-300" title={r.key}>{r.key}</div>
            <div className="flex-1 h-2.5 rounded-full bg-panel3 overflow-hidden">
              <div className={`h-2.5 rounded-full ${TONE[tone].bar} transition-all`} style={{ width: `${Math.max(3, (Math.abs(r.value) / max) * 100)}%` }} />
            </div>
            <div className={`w-24 shrink-0 text-right font-tabular font-semibold ${signed ? (r.value >= 0 ? "text-emerald-300" : "text-rose-300") : TONE[tone].text}`}>{valueLabel(r.value)}</div>
            {r.hint && <div className="w-14 shrink-0 text-right text-[10px] text-slate-300">{r.hint}</div>}
          </div>
        );
      })}
    </div>
  );
}

const SEVERITY: Record<string, Tone> = { INFO: "sky", WARNING: "amber", CRITICAL: "rose", EMERGENCY: "rose" };
const STATUS_TONE: Record<string, Tone> = { ACTIVE: "emerald", PAUSED: "amber", STOPPED: "rose" };

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

  useEffect(() => {
    Promise.all([api.listStrategies(), api.availableBrokers(), api.health()])
      .then(([s, b]) => { setStrategies(s); setBrokers(b.brokers); setHealthy(true); })
      .catch((e) => { setError(String(e)); setHealthy(false); });
  }, []);

  useEffect(() => {
    if (!user) { setWorker(null); setSummary(null); setExposure(null); setPositions([]); setDeployments([]); setAccounts([]); setAlerts([]); return; }
    const load = () => {
      api.workerStatus().then(setWorker).catch(() => setWorker(null));
      api.getAnalyticsSummary().then(setSummary).catch(() => setSummary(null));
      api.portfolioExposure(true).then(setExposure).catch(() => setExposure(null));
      api.listPositions().then(setPositions).catch(() => setPositions([]));
      api.listDeployments().then(setDeployments).catch(() => setDeployments([]));
      api.listAccounts().then(setAccounts).catch(() => setAccounts([]));
      api.listNotifications().then((n) => setAlerts(n.slice(0, 6))).catch(() => setAlerts([]));
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
          <Button icon={<Sparkles size={15} />} onClick={() => onNavigate("ai-copilot")}>AI Copilot</Button>
        </>}
      />

      {error && (
        <div className="rounded-xl border border-down/40 bg-down/10 px-3 py-2 text-sm text-down">
          Some dashboard data could not be loaded: {error}
        </div>
      )}

      {/* KPI tiles */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <KpiTile icon={netPnl != null && netPnl < 0 ? ArrowDownRight : ArrowUpRight} tone={netPnl == null || netPnl === 0 ? "sky" : netPnl < 0 ? "rose" : "emerald"} label="Net P&L"
                 value={user ? (summary ? money(netPnl) : "…") : "Log in"} sub={summary ? `${summary.closed_trades} closed · PF ${summary.profit_factor?.toFixed(2) ?? "-"}` : "all trades, net of charges"} onClick={onNavigate && (() => onNavigate("analytics"))} />
        <KpiTile icon={Target} tone="sky" label="Win rate" value={user && summary ? pct(summary.win_rate) : "…"}
                 sub={summary ? `avg win ${money(summary.avg_win)} · avg loss ${money(summary.avg_loss)}` : "closed trades"} onClick={onNavigate && (() => onNavigate("analytics"))} />
        <KpiTile icon={Briefcase} tone="violet" label="Open positions" value={user ? positions.length : "…"}
                 sub={exposure ? `${money(exposure.gross_notional)} gross · ${pct(exposure.risk_pct_of_capital)} at stops` : "across every account"} onClick={onNavigate && (() => onNavigate("positions"))} />
        <KpiTile icon={Bot} tone="sky" label="Deployments" value={user ? `${live + paper}` : "…"}
                 sub={user ? <><span className="text-emerald-300">{live} LIVE</span> · <span className="text-sky-300">{paper} PAPER</span>{paused ? <> · <span className="text-amber-300">{paused} paused</span></> : null}</> : "active strategies"} onClick={onNavigate && (() => onNavigate("deployments"))} />
        <KpiTile icon={Wallet} tone="orange" label="Broker funds" value={user ? (accounts.length ? money(balance) : "-") : "…"}
                 sub={`${accounts.length} account${accounts.length === 1 ? "" : "s"} · ${brokers.length} adapters`} onClick={onNavigate && (() => onNavigate("settings"))} />
        <KpiTile icon={GitMerge} tone="fuchsia" label="Strategies" value={strategies.length || "…"}
                 sub={`${mtf} multi-timeframe · ${indicator} indicator`} onClick={onNavigate && (() => onNavigate("strategies"))} />
      </div>

      {user && <MarketPulseCard onNavigate={onNavigate} />}

      {/* Charts row */}
      <div className="grid lg:grid-cols-3 gap-4">
        <Card title="P&L by strategy" className="lg:col-span-2">
          {user && byStrategy.length > 0 ? (
            <Bars signed rows={byStrategy.map((g, i) => ({ key: g.key, value: g.net_pnl, tone: PALETTE[i % PALETTE.length], hint: `${g.trades} tr · ${pct(g.win_rate, 0)}` }))} valueLabel={(v) => money(v)} />
          ) : (
            <div className="flex h-32 items-center justify-center text-xs text-slate-300">{user ? "No closed trades yet - the first PAPER session fills this in." : "Log in to see your strategies' P&L."}</div>
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
            {bySymbol.length > 0 && exposure ? (
              <>
                <div className="mb-3 flex items-center gap-4">
                  <Ring size={96} segments={[{ value: exposure.long_notional, tone: "emerald" }, { value: exposure.short_notional, tone: "rose" }]} label={pct(exposure.gross_pct_of_capital, 0)} sub="of capital" />
                  <div className="text-xs space-y-1">
                    <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-emerald-400" /><span className="text-slate-300">Long</span><span className="ml-auto font-tabular text-emerald-300">{money(exposure.long_notional)}</span></div>
                    <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-rose-400" /><span className="text-slate-300">Short</span><span className="ml-auto font-tabular text-rose-300">{money(exposure.short_notional)}</span></div>
                    <div className="text-slate-300">largest symbol {pct(exposure.largest_symbol_pct, 0)} · unrealised <span className={exposure.unrealised_pnl >= 0 ? "text-emerald-300" : "text-rose-300"}>{money(exposure.unrealised_pnl)}</span></div>
                  </div>
                </div>
                <Bars rows={bySymbol.map((s, i) => ({ key: s.symbol, value: s.notional, tone: PALETTE[i % PALETTE.length], hint: `${s.positions} pos` }))} valueLabel={(v) => money(v)} />
              </>
            ) : (
              <div className="flex h-32 items-center justify-center text-xs text-slate-300">No open positions - exposure is empty.</div>
            )}
          </Card>

          {/* Deployments */}
          <Card title="Running deployments">
            {deployments.length === 0 ? (
              <div className="flex h-32 flex-col items-center justify-center gap-2 text-xs text-slate-300">
                Nothing deployed yet.
                {onNavigate && <Button size="sm" variant="primary" onClick={() => onNavigate("deployments")}>Deploy the first one</Button>}
              </div>
            ) : (
              <ul className="space-y-2">
                {deployments.slice(0, 6).map((d) => (
                  <li key={d.id} className="flex items-center gap-2 rounded-xl border border-border bg-panel2 px-3 py-2 text-xs">
                    <span className={`rounded-md px-1.5 py-0.5 font-bold ${d.mode === "LIVE" ? "bg-emerald-500/20 text-emerald-300" : "bg-sky-500/20 text-sky-300"}`}>{d.mode}</span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-semibold text-slate-100">{d.strategy_id} <span className="text-slate-300">on</span> {d.symbol}</div>
                      <div className="truncate text-[10px] text-slate-300">{d.timeframe} · {d.broker_name ?? "paper"} · {d.open_positions} open{d.last_error ? ` · ${d.last_error}` : ""}</div>
                    </div>
                    <span className={`h-2 w-2 rounded-full ${TONE[STATUS_TONE[d.status] ?? "sky"].bar}`} title={d.status} />
                  </li>
                ))}
                {deployments.length > 6 && onNavigate && <button onClick={() => onNavigate("deployments")} className="text-[11px] text-amber-300 hover:underline">+{deployments.length - 6} more</button>}
              </ul>
            )}
          </Card>

          {/* Positions + alerts */}
          <div className="space-y-4">
            <Card title="Open positions">
              {positions.length === 0 ? (
                <div className="text-xs text-slate-300">Flat. Nothing at risk right now.</div>
              ) : (
                <ul className="space-y-1.5">
                  {positions.slice(0, 5).map((p) => (
                    <li key={p.id} className="flex items-center gap-2 text-xs">
                      <span className={`rounded-md px-1.5 py-0.5 font-bold ${p.direction === "LONG" ? "bg-emerald-500/20 text-emerald-300" : "bg-rose-500/20 text-rose-300"}`}>{p.direction === "LONG" ? "L" : "S"}</span>
                      <span className="min-w-0 flex-1 truncate font-semibold text-slate-100">{p.symbol}</span>
                      <span className="font-tabular text-slate-300">{p.quantity} @ {p.entry_price.toLocaleString("en-IN")}</span>
                      <span className={`rounded px-1 text-[10px] ${p.mode === "LIVE" ? "text-emerald-300" : "text-sky-300"}`}>{p.mode}</span>
                    </li>
                  ))}
                  {positions.length > 5 && onNavigate && <button onClick={() => onNavigate("positions")} className="text-[11px] text-violet-300 hover:underline">+{positions.length - 5} more</button>}
                </ul>
              )}
            </Card>
            <Card title={`Latest alerts${unread ? ` · ${unread} unread` : ""}`}>
              {alerts.length === 0 ? (
                <div className="text-xs text-slate-300">Quiet. Entries, exits and risk events will show here.</div>
              ) : (
                <ul className="space-y-1.5">
                  {alerts.map((a) => (
                    <li key={a.id} className="flex items-start gap-2 text-xs">
                      <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${TONE[SEVERITY[a.severity] ?? "sky"].bar}`} />
                      <div className="min-w-0 flex-1">
                        <div className={`truncate ${a.read ? "text-slate-300" : "font-semibold text-slate-100"}`}>{a.title}</div>
                        <div className="text-[10px] text-slate-300">{a.event_type} · {ago(a.created_at)}</div>
                      </div>
                    </li>
                  ))}
                  {onNavigate && <button onClick={() => onNavigate("notifications")} className="flex items-center gap-1 text-[11px] text-sky-300 hover:underline"><Bell size={11} /> All notifications</button>}
                </ul>
              )}
            </Card>
          </div>
        </div>
      )}

      {user && <GoLiveChecklist kind="tenant" onNavigate={onNavigate} />}

      <Card title="Engines behind this console">
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {ENGINES.map(({ icon: Icon, tone, title, description }) => {
            const t = TONE[tone];
            return (
              <div key={title} className={`relative overflow-hidden rounded-xl border ${t.ring} bg-surface-2 p-3.5`}>
                <div className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${t.grad}`} />
                <div className="relative">
                  <div className={`mb-2 inline-flex rounded-xl ${t.bg} ${t.text} p-2`}><Icon size={16} /></div>
                  <div className="text-sm font-bold text-slate-100">{title}</div>
                  <div className="mt-0.5 text-xs leading-relaxed text-slate-300">{description}</div>
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
