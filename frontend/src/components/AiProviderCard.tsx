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
  const [form, setForm] = useState({ provider: "rule_based" as AiProviderName, model: "", api_key: "", enabled: true, data_consent: false });
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isOwner = user?.role === "OWNER" || user?.role === "SUPER_ADMIN";

  function refresh() {
    api.aiProvider().then((c) => {
      setConfig(c);
      // Not configured yet: pre-select the platform's recommended provider (Claude) so the owner only pastes a key.
      const provider = c.configured ? c.provider : (c.default_provider ?? "anthropic");
      setForm((f) => ({ ...f, provider, model: c.configured ? c.model : (c.default_models[provider] ?? ""), enabled: c.enabled, api_key: "" }));
    }).catch((e) => setError(String(e)));
  }
  useEffect(refresh, []);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(true); setError(null); setMessage(null);
    try { await fn(); setMessage(label); refresh(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  return (
    <Card title="AI provider">
      <p className="text-xs text-muted mb-3">
        Powers the AI Copilot's strategy generator and the wording of monitoring proposals. Claude (Anthropic) is the recommended
        provider: paste an Anthropic API key from console.anthropic.com. Without a key (or on the Free plan) everything falls back to the
        built-in rule-based parser - no data leaves the platform. With an external provider the model receives the text needed to
        answer: your questions, your interview answers (capital, experience, risk appetite, goals), facts about your deployments and
        trades, market data and news headlines - never broker credentials, API keys or passwords. Nothing it produces can trade before
        you backtest and approve it, and it gives no investment advice.
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
      {config?.configured && config.provider !== "rule_based" && config.models && (
        <div className="text-xs text-muted mb-3">
          Models: <span className="text-text">{config.models.strong}</span> writes strategies and scanner plans;{" "}
          <span className="text-text">{config.models.fast}</span> narrates, classifies news and answers questions (set by the operator).
        </div>
      )}
      {config?.usage && config.configured && config.provider !== "rule_based" && (
        <div className={`rounded border px-3 py-2 text-xs mb-3 ${config.usage.exhausted ? "border-amber-500/50 bg-amber-500/10" : "border-border bg-panel2"}`}>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span className="font-semibold">AI usage {config.usage.month}</span>
            <span>₹{config.usage.spent_inr.toLocaleString("en-IN", { maximumFractionDigits: 2 })}{config.usage.budget_inr > 0 ? ` of ₹${config.usage.budget_inr.toLocaleString("en-IN")} budget` : " (no cap on this plan)"}</span>
            <span className="text-muted">${config.usage.spent_usd.toFixed(4)} · {config.usage.calls} calls · {config.usage.tokens_input.toLocaleString()} in / {config.usage.tokens_output.toLocaleString()} out tokens</span>
          </div>
          {config.usage.budget_inr > 0 && (
            <div className="mt-1 h-1.5 w-full rounded bg-border overflow-hidden">
              <div className={`h-full ${config.usage.exhausted ? "bg-amber-500" : "bg-brand"}`} style={{ width: `${Math.min(100, (config.usage.spent_inr / config.usage.budget_inr) * 100)}%` }} />
            </div>
          )}
          {Object.keys(config.usage.by_feature).length > 0 && (
            <div className="mt-1 text-muted">
              {Object.entries(config.usage.by_feature).sort((a, b) => b[1] - a[1]).map(([f, usd]) => `${f} $${usd.toFixed(4)}`).join(" · ")}
            </div>
          )}
          <div className={`mt-1 ${config.usage.exhausted ? "text-amber-400" : "text-muted"}`}>{config.usage.note}</div>
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
            <button disabled={busy || (form.provider !== "rule_based" && !form.data_consent)} onClick={() => run("AI provider saved.", () => api.aiSaveProvider({ provider: form.provider, model: form.model || null, api_key: form.api_key || null, enabled: form.enabled, data_consent: form.data_consent, language: "en" }))} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">Save</button>
            {config?.configured && <button disabled={busy} onClick={() => run("AI provider removed.", () => api.aiDeleteProvider())} className="text-xs text-danger hover:underline">Remove</button>}
          </div>
        </div>
      )}
      {isOwner && form.provider !== "rule_based" && config?.data_consent && (
        <div className="mt-3 rounded border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
          <div className="font-semibold text-amber-200 mb-1">Data-sharing consent (DPDP) · version {config.data_consent.version}
            {config.data_consent.accepted && <span className="ml-2 text-emerald-300">accepted {config.data_consent.accepted_at ? new Date(config.data_consent.accepted_at).toLocaleDateString() : ""}</span>}
          </div>
          <p className="text-slate-200">{config.data_consent.text.en}</p>
          <p className="mt-1 text-slate-300">{config.data_consent.text.mr}</p>
          <label className="mt-2 flex items-start gap-2 text-slate-100">
            <input type="checkbox" checked={form.data_consent} onChange={(e) => setForm({ ...form, data_consent: e.target.checked })} />
            <span>I am the owner of this organisation and consent to this data being sent to the selected provider for the stated purpose. I can opt out at any time by switching to the rule-based provider. · मी या संस्थेचा मालक आहे आणि वर नमूद उद्देशासाठी हा data निवडलेल्या provider कडे पाठवण्यास संमती देतो.</span>
          </label>
        </div>
      )}
      {error && <div className="mt-3 text-sm text-danger">{error}</div>}
      {message && <div className="mt-3 text-sm text-accent">{message}</div>}
    </Card>
  );
}
