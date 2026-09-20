import {
  Activity, BarChart3, GitMerge, History, Layers, Link2, Server, ShieldCheck, Target, TrendingUp,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Card, StatTile } from "../components/ui";
import type { StrategyInfo } from "../types";

const ENGINES: {
  icon: LucideIcon;
  tone: "brand" | "accent" | "sky";
  title: string;
  description: string;
}[] = [
  { icon: Activity, tone: "accent", title: "Strategy Engine", description: "7 inbuilt auto-executable multi-timeframe & indicator scalpers" },
  { icon: ShieldCheck, tone: "brand", title: "Risk Engine", description: "Position sizing, daily loss / trade-count / consecutive-loss gates" },
  { icon: Link2, tone: "brand", title: "Broker Abstraction", description: "One interface across every supported broker - paper trading until a real adapter is authenticated" },
  { icon: TrendingUp, tone: "sky", title: "Price Action + S/R", description: "Market structure, candlestick patterns, support/resistance zone engine" },
  { icon: Layers, tone: "sky", title: "Option Chain Intelligence", description: "PCR, Max Pain, ATM/ITM/OTM, OI buildup/unwinding, directional bias" },
  { icon: Target, tone: "accent", title: "Signal Scoring", description: "Weighted composite combining every engine above into one confidence score" },
  { icon: History, tone: "accent", title: "Backtest Engine", description: "Event-driven simulation with realistic slippage/charges cost modelling" },
];

const TONE_CLASSES: Record<string, string> = {
  brand: "bg-brand/10 text-brand border-brand/30",
  accent: "bg-accent/10 text-accent border-accent/30",
  sky: "bg-sky-500/10 text-sky-400 border-sky-500/30",
};

export default function DashboardPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [brokers, setBrokers] = useState<string[]>([]);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

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
        <h1 className="text-xl font-semibold text-slate-100">Dashboard</h1>
        <p className="text-sm text-muted">Live status of the backend engines this console talks to.</p>
      </div>

      {error && (
        <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          Could not reach the backend API ({error}). Is `uvicorn app.main:app` running on :8000?
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatTile icon={Server} label="Backend" value={healthy === null ? "…" : healthy ? "Online" : "Offline"} tone={healthy ? "up" : healthy === false ? "down" : "default"} />
        <StatTile icon={GitMerge} label="Inbuilt Strategies" value={strategies.length || "…"} />
        <StatTile icon={BarChart3} label="MTF Combos" value={mtf.length || "…"} />
        <StatTile icon={Link2} label="Broker Adapters" value={brokers.length || "…"} />
      </div>

      <Card title="Engines wired into this console">
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {ENGINES.map(({ icon: Icon, tone, title, description }) => (
            <div key={title} className="rounded-lg border border-border bg-panel2 p-3.5 flex gap-3">
              <div className={`shrink-0 rounded-md border p-2 h-fit ${TONE_CLASSES[tone]}`}>
                <Icon size={16} />
              </div>
              <div>
                <div className="text-sm font-medium text-slate-100">{title}</div>
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
