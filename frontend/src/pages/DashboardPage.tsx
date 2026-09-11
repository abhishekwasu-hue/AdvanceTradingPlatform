import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Card, StatTile } from "../components/ui";
import type { StrategyInfo } from "../types";

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
        <StatTile label="Backend" value={healthy === null ? "…" : healthy ? "Online" : "Offline"} tone={healthy ? "up" : healthy === false ? "down" : "default"} />
        <StatTile label="Inbuilt Strategies" value={strategies.length || "…"} />
        <StatTile label="MTF Combos" value={mtf.length || "…"} />
        <StatTile label="Broker Adapters" value={brokers.length || "…"} />
      </div>

      <Card title="Engines wired into this console">
        <ul className="text-sm text-slate-300 space-y-1.5 list-disc list-inside">
          <li>Strategy Engine — 7 inbuilt auto-executable multi-timeframe &amp; indicator scalpers</li>
          <li>Risk Engine — position sizing, daily loss / trade-count / consecutive-loss gates</li>
          <li>Broker Abstraction — {brokers.join(", ") || "…"} (paper trading only until a real adapter is authenticated)</li>
          <li>Price Action + Support/Resistance — market structure, candlestick patterns, zone engine</li>
          <li>Option Chain Intelligence — PCR, Max Pain, ATM/ITM/OTM, OI activity, bias</li>
          <li>Signal Scoring — weighted composite combining all of the above</li>
          <li>Backtest Engine — event-driven simulation with realistic cost modelling</li>
        </ul>
      </Card>

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
