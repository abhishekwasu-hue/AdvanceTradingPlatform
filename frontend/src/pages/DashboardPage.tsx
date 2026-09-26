import {
  Activity, BarChart3, Bot, GitMerge, History, Layers, Link2, Server, ShieldCheck, Target, TrendingUp,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, StatTile } from "../components/ui";
import type { StrategyInfo, WorkerStatus } from "../types";

const ENGINES: {
  icon: LucideIcon;
  tone: "brand" | "accent" | "sky" | "warn" | "violet" | "rose" | "teal";
  title: string;
  description: string;
}[] = [
  { icon: Activity, tone: "accent", title: "Strategy Engine", description: "7 inbuilt auto-executable multi-timeframe & indicator scalpers" },
  { icon: ShieldCheck, tone: "warn", title: "Risk Engine", description: "Position sizing, daily loss / trade-count / consecutive-loss gates" },
  { icon: Link2, tone: "brand", title: "Broker Abstraction", description: "One interface across every supported broker - paper trading until a real adapter is authenticated" },
  { icon: TrendingUp, tone: "sky", title: "Price Action + S/R", description: "Market structure, candlestick patterns, support/resistance zone engine" },
  { icon: Layers, tone: "violet", title: "Option Chain Intelligence", description: "PCR, Max Pain, ATM/ITM/OTM, OI buildup/unwinding, directional bias" },
  { icon: Target, tone: "rose", title: "Signal Scoring", description: "Weighted composite combining every engine above into one confidence score" },
  { icon: History, tone: "teal", title: "Backtest Engine", description: "Event-driven simulation with realistic slippage/charges cost modelling" },
  { icon: Bot, tone: "accent", title: "Autopilot Worker", description: "Background service trading deployed strategies every minute on live broker candles - independent of any browser" },
];

const TONE_CLASSES: Record<string, string> = {
  brand: "bg-brand/10 text-brand border-brand/30",
  accent: "bg-accent/10 text-accent border-accent/30",
  sky: "bg-sky-500/10 text-sky-400 border-sky-500/30",
  warn: "bg-warn/10 text-warn border-warn/30",
  violet: "bg-violet-500/10 text-violet-400 border-violet-500/30",
  rose: "bg-rose-500/10 text-rose-400 border-rose-500/30",
  teal: "bg-teal-500/10 text-teal-400 border-teal-500/30",
};

export default function DashboardPage() {
  const { user } = useAuth();
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [brokers, setBrokers] = useState<string[]>([]);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) {
      setWorker(null);
      return;
    }
    const load = () => api.workerStatus().then(setWorker).catch(() => setWorker(null));
    load();
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, [user]);

  useEffect(() => {
    Promise.all([api.listStrategies(), api.availableBrokers(), api.health()])
      .then(([s, b]) => {
        setStrategies(s);
        setBrokers(b.brokers);
        setHealthy(true);
      })
      .catch((e) => {
        setError(String(e));
        setHealthy(false);
      });
  }, []);

  const mtf = strategies.filter((s) => s.category === "multi_timeframe");
  const indicatorBased = strategies.filter((s) => s.category === "indicator_based");
  const other = strategies.length - mtf.length - indicatorBased.length;
  const total = strategies.length || 1;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold text-slate-100">Dashboard</h1>
        <p className="text-sm text-muted">Live status of the backend engines this console talks to.</p>
      </div>

      {error && (
        <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          Could not reach the backend API ({error}). Is `uvicorn app.main:app` running on :8000?
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <StatTile icon={Server} label="Backend" value={healthy === null ? "…" : healthy ? "Online" : "Offline"} tone={healthy ? "up" : healthy === false ? "down" : "default"} />
        <StatTile
          icon={Bot} label="Autopilot"
          value={!user ? "Log in" : worker === null ? "…" : worker.running ? (worker.market_open ? "Trading" : "Idle") : "Stopped"}
          tone={!user || worker === null ? "default" : worker.running ? "up" : "down"}
          accentClass="text-slate-400"
        />
        <StatTile icon={GitMerge} label="Inbuilt Strategies" value={strategies.length || "…"} accentClass="text-violet-400" />
        <StatTile icon={BarChart3} label="MTF Combos" value={mtf.length || "…"} accentClass="text-sky-400" />
        <StatTile icon={Link2} label="Broker Adapters" value={brokers.length || "…"} accentClass="text-orange-400" />
      </div>

      {user && worker && (
        <div className={`rounded-lg border px-3 py-2 text-xs ${worker.running ? "border-border bg-panel2 text-muted" : "border-danger/40 bg-danger/10 text-danger"}`}>
          {worker.running
            ? `Worker heartbeat ${worker.seconds_since_heartbeat}s ago · ${worker.cycle_count} cycles · ${worker.market_status}`
            : "No trading-worker heartbeat - deployed strategies are not being evaluated. Start the worker service (docs/OPERATIONS.md)."}
        </div>
      )}

      <Card title="Engines wired into this console">
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {ENGINES.map(({ icon: Icon, tone, title, description }) => (
            <div key={title} className="rounded-lg border border-border bg-panel2 p-3.5 flex gap-3">
              <div className={`shrink-0 rounded-md border p-2 h-fit ${TONE_CLASSES[tone]}`}>
                <Icon size={16} />
              </div>
              <div>
                <div className="text-sm font-bold text-slate-100">{title}</div>
                <div className="text-xs text-muted mt-0.5 leading-relaxed">{description}</div>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {strategies.length > 0 && (
        <Card title="Strategy mix">
          <div className="space-y-2.5">
            {[
              { label: "Multi-timeframe", count: mtf.length, className: "bg-brand" },
              { label: "Indicator-based", count: indicatorBased.length, className: "bg-accent" },
              ...(other > 0 ? [{ label: "Other", count: other, className: "bg-slate-500" }] : []),
            ].map((row) => (
              <div key={row.label} className="flex items-center gap-3 text-xs">
                <div className="w-32 shrink-0 text-muted">{row.label}</div>
                <div className="flex-1 h-2 rounded-full bg-panel2 overflow-hidden">
                  <div className={`h-2 rounded-full ${row.className}`} style={{ width: `${(row.count / total) * 100}%` }} />
                </div>
                <div className="w-6 shrink-0 text-right font-tabular text-slate-300">{row.count}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card title="Indicator-based scalpers">
        <div className="grid sm:grid-cols-3 gap-3">
          {indicatorBased.map((s) => (
            <div key={s.id} className="rounded-md border border-border bg-panel2 p-3">
              <div className="text-sm font-medium text-slate-100">{s.name}</div>
              <div className="text-xs text-muted mt-1">{s.description}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
