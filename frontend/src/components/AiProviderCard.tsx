import { Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card } from "./ui";
import type { AiProviderConfig, AiProviderName } from "../types";

const input = "w-full rounded bg-panel2 border border-border px-2 py-1 text-xs";

/** Phase L1: the tenant's AI provider. The API key is typed here, sent once over TLS, stored
 * encrypted and never shown again - the same rule as broker credentials. */
export default function AiProviderCard() {
  const { user } = useAuth();
  const [config, setConfig] = useState<AiProviderConfig | null>(null);
  const [form, setForm] = useState({ provider: "rule_based" as AiProviderName, model: "", api_key: "", enabled: true });
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isOwner = user?.role === "OWNER" || user?.role === "SUPER_ADMIN";

  function refresh() {
    api.aiProvider().then((c) => { setConfig(c); setForm((f) => ({ ...f, provider: c.provider, model: c.model, enabled: c.enabled, api_key: "" })); }).catch((e) => setError(String(e)));
  }
  useEffect(refresh, []);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(true); setError(null); setMessage(null);
    try { await fn(); setMessage(label); refresh(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  return (
    <Card title="AI provider">
      <p className="text-xs text-muted mb-3">
        Powers the AI Copilot's strategy generator and the wording of monitoring proposals. Without a provider (or on the Free plan)
        everything falls back to the built-in rule-based parser - no data leaves the platform. The model only ever receives the text
        you type; it never sees broker credentials, and nothing it produces can trade before you backtest and approve it.
      </p>
      {config && (
        <div className="text-xs mb-3 flex flex-wrap gap-3">
          <span className="inline-flex items-center gap-1 rounded-md border border-brand/40 bg-brand/10 px-2 py-0.5 font-bold text-brand"><Sparkles size={12} /> {config.provider} · {config.model}</span>
          <span className="text-muted">{config.configured ? (config.api_key_set ? "key stored (encrypted)" : "no key needed") : "not configured - rule-based"}</span>
          {config.last_used_at && <span className="text-muted">last used {new Date(config.last_used_at).toLocaleString()}</span>}
          {config.last_error && <span className="text-danger">{config.last_error}</span>}
          {!config.ai_features_allowed && <span className="text-amber-400">external providers need the Pro or Business plan</span>}
        </div>
      )}
      {isOwner && (
        <div className="grid md:grid-cols-4 gap-2">
          <select className={input} value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value as AiProviderName, model: config?.default_models[e.target.value] ?? "" })}>
            {(config?.providers ?? ["rule_based"]).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <input className={input} placeholder="Model" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} disabled={form.provider === "rule_based"} />
          <input className={input} type="password" autoComplete="off" placeholder={form.provider === "rule_based" ? "no key needed" : "API key (blank = keep stored)"} value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} disabled={form.provider === "rule_based"} />
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1 text-xs text-muted"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> enabled</label>
            <button disabled={busy} onClick={() => run("AI provider saved.", () => api.aiSaveProvider({ provider: form.provider, model: form.model || null, api_key: form.api_key || null, enabled: form.enabled }))} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">Save</button>
            {config?.configured && <button disabled={busy} onClick={() => run("AI provider removed.", () => api.aiDeleteProvider())} className="text-xs text-danger hover:underline">Remove</button>}
          </div>
        </div>
      )}
      {error && <div className="mt-3 text-sm text-danger">{error}</div>}
      {message && <div className="mt-3 text-sm text-accent">{message}</div>}
    </Card>
  );
}
