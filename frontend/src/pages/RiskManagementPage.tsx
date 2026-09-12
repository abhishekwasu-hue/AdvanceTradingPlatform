import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card } from "../components/ui";
import type { RiskConfig } from "../types";

const FIELDS: { key: keyof RiskConfig; label: string; step?: string }[] = [
  { key: "capital", label: "Capital (₹)" },
  { key: "risk_per_trade_pct", label: "Risk per trade (%)", step: "0.1" },
  { key: "max_daily_loss_pct", label: "Max daily loss (%)", step: "0.1" },
  { key: "max_trades_per_day", label: "Max trades / day" },
  { key: "max_open_positions", label: "Max open positions" },
  { key: "max_consecutive_losses", label: "Max consecutive losses" },
  { key: "min_risk_reward", label: "Min risk/reward", step: "0.1" },
  { key: "lot_size", label: "Lot size" },
];

export default function RiskManagementPage() {
  const { user, loading: authLoading } = useAuth();
  const [config, setConfig] = useState<RiskConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    api.getRiskSettings().then(setConfig).catch((e) => setError(String(e)));
  }, [user]);

  async function handleSave() {
    if (!config) return;
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      const saved = await api.updateRiskSettings(config);
      setConfig(saved);
      setMessage("Saved. Every /paper-execute call you make now uses these limits by default.");
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold text-slate-100">Risk Management</h1>
        <Card>
          <p className="text-sm text-muted">
            Log in from the Account tab to configure your own risk limits. Every paper (and,
            once wired, live) order passes through the Risk Engine using these settings before
            it can execute.
          </p>
        </Card>
      </div>
    );
  }

  if (!config) {
    return <div className="text-sm text-muted">Loading…</div>;
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Risk Management</h1>
        <p className="text-sm text-muted">
          Position sizing and daily-loss/trade-count/consecutive-loss guards the Risk Engine
          checks before every paper-execute order. Anonymous calls always use the platform
          default; these apply only to your own logged-in orders.
        </p>
      </div>

      <Card>
        <div className="grid sm:grid-cols-2 gap-4">
          {FIELDS.map((f) => (
            <div key={f.key}>
              <label className="block text-xs text-muted mb-1">{f.label}</label>
              <input
                type="number"
                step={f.step ?? "1"}
                className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                value={config[f.key]}
                onChange={(e) => setConfig({ ...config, [f.key]: Number(e.target.value) })}
              />
            </div>
          ))}
        </div>

        {error && <div className="mt-3 text-sm text-danger">{error}</div>}
        {message && <div className="mt-3 text-sm text-accent">{message}</div>}

        <button
          onClick={handleSave}
          disabled={saving}
          className="mt-4 rounded bg-accent/90 hover:bg-accent text-slate-900 font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save risk settings"}
        </button>
      </Card>
    </div>
  );
}
