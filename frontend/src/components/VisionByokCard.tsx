import { Eye, ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { BYOK_CHANGED_EVENT } from "../aiKeys/detect";
import { Card } from "./ui";
import type { ByokAuditScope, ByokChannels, ByokPrefsInput, ByokProviderName, ByokProviderStatus, ByokStatus } from "../types";
import { capUsage, formatUsd, keyShapeError, parseCap } from "./visionByok";
import { formatDateTimeIST } from "../ui/format";
import { TextOnDemand } from "../ui/numbersFirst";
import { useUiV2 } from "../ui/useFeatures";

const input = "w-full rounded bg-surface-2 border border-border px-2 py-1 text-xs";
const button = "rounded border border-border px-2 py-1 text-xs hover:bg-surface-2 disabled:opacity-50";
const TITLE = "Vision with your own AI key";

const AUDIT_LABELS: { key: keyof ByokAuditScope; label: string }[] = [
  { key: "proposals", label: "Trade proposals" },
  { key: "scan_alerts", label: "Every scan alert" },
  { key: "daily_review", label: "Daily review" },
];
const CHANNEL_LABELS: { key: keyof ByokChannels; label: string }[] = [
  { key: "in_app", label: "In-app" },
  { key: "telegram", label: "Telegram" },
];

function when(value: string | null): string {
  return value ? formatDateTimeIST(value) : "never";
}

/** BYOK-V1: each user's own AI provider API key for Vision chart review (operator flag `vision_byok`). The key is typed
 * here, sent once, stored encrypted and never shown again - only its first and last four characters. */
export default function VisionByokCard() {
  const v2 = useUiV2();
  const [status, setStatus] = useState<ByokStatus | null>(null);
  const [off, setOff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [keys, setKeys] = useState<Record<ByokProviderName, string>>({ anthropic: "", openai: "", gemini: "" });
  const [caps, setCaps] = useState({ daily: "", monthly: "" });

  function apply(s: ByokStatus) {
    setStatus(s);
    setOff(false);
    setCaps({ daily: String(s.caps.daily_cap_usd), monthly: String(s.caps.monthly_cap_usd) });
  }

  useEffect(() => {
    const read = () => api.byokStatus().then(apply).catch((e) => {
      if (e instanceof ApiError && e.status === 404) setOff(true);
      else setError(String(e));
    });
    void read();
    // MO2 part B: a key saved from the "Get an AI key" guide (src/aiKeys/KeyGuidesV2.tsx) shows here at once.
    window.addEventListener(BYOK_CHANGED_EVENT, read);
    return () => window.removeEventListener(BYOK_CHANGED_EVENT, read);
  }, []);

  async function run(label: string, fn: () => Promise<ByokStatus>) {
    setBusy(true); setError(null); setMessage(null);
    try { apply(await fn()); setMessage(label); } catch (e) { setError(e instanceof ApiError && e.detail ? e.detail : String(e)); } finally { setBusy(false); }
  }

  function savePrefs(body: ByokPrefsInput, label = "Vision settings saved.") {
    void run(label, () => api.byokSavePrefs(body));
  }

  async function saveKey(p: ByokProviderStatus) {
    const problem = keyShapeError(p.provider, keys[p.provider]);
    if (problem) { setError(problem); setMessage(null); return; }
    const key = keys[p.provider].trim();
    setKeys((k) => ({ ...k, [p.provider]: "" }));          // never kept in the form once sent
    await run(`${p.label} key saved (encrypted). Use Test to check it.`, () => api.byokSaveKey(p.provider, key));
  }

  async function testKey(p: ByokProviderStatus) {
    setBusy(true); setError(null); setMessage(null);
    try {
      const r = await api.byokTestKey(p.provider);
      apply(r.status);
      if (r.result.ok) setMessage(`${p.label} key works (model ${r.result.model}, test cost ${formatUsd(r.result.cost_usd)}).`);
      else setError(`${p.label} test failed: ${r.result.error ?? "unknown error"}`);
    } catch (e) {
      setError(e instanceof ApiError && e.detail ? e.detail : String(e));
    } finally { setBusy(false); }
  }

  function saveCaps() {
    const max = status?.caps.max_cap_usd ?? 500;
    const daily = parseCap(caps.daily, max);
    const monthly = parseCap(caps.monthly, max);
    const problem = daily.error ?? monthly.error;
    if (problem || daily.value === null || monthly.value === null) { setError(problem ?? "Enter both caps"); return; }
    if (daily.value > monthly.value) { setError("The daily cap cannot be above the monthly cap"); return; }
    savePrefs({ daily_cap_usd: daily.value, monthly_cap_usd: monthly.value }, "Spend caps saved.");
  }

  if (off) {
    return (
      <Card title={TITLE}>
        <p className="text-xs text-fg-muted">Vision with your own AI key is not enabled for this organisation yet (feature <code>vision_byok</code>).</p>
      </Card>
    );
  }
  if (!status) {
    return <Card title={TITLE}>{error ? <div className="text-sm text-down">{error}</div> : <p className="text-xs text-fg-muted">Loading...</p>}</Card>;
  }

  const notes = (
    <>
      <p className="text-xs text-warn mb-1">{status.notes.subscription}</p>
      <p className="text-xs text-fg-muted mb-3">{status.notes.privacy}</p>
    </>
  );
  const scope = status.scope;
  const scopeLine = scope && (
    <p className="mt-1 text-xs text-fg-muted">
                {scope.v3_enabled
                  ? `Scan alerts: alerts of rules you created, at most ${scope.scan_audits_per_hour} per hour. Daily review: after ${scope.daily_review_at} IST on trading days, up to ${scope.daily_review_max_symbols} of your own symbols (your deployments, then your symbol alerts), ${scope.daily_review_timeframe} chart.`
                  : "Scan-alert and daily-review audits are not enabled for your organisation yet."}
              </p>
  );

  const withKeys = status.providers.filter((p) => p.key_set);
  const today = capUsage(status.spend.today_usd, status.caps.daily_cap_usd);
  const month = capUsage(status.spend.month_usd, status.caps.monthly_cap_usd);

  return (
    <Card title={TITLE}>
      {/* UX-11 U1: on the v2 pages the explanation, both notes and the audit scope sit in one closed Details. */}
      <TextOnDemand id="vision-byok.about" on={v2}>
      <p className="text-xs text-fg-muted mb-2">
        Vision reviews charts and comments on them using your own AI provider API key. You pay the provider directly; the platform stores your
        key encrypted, never bills you for it and never uses it for anyone else. Vision only comments - it never places, blocks or changes an order.
      </p>
      {v2 && notes}
      {v2 && scopeLine}
      </TextOnDemand>
      <div className={`mb-2 inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-bold ${status.active ? "border-brand/40 bg-brand/10 text-brand" : "border-border bg-surface-2 text-fg-muted"}`}>
        <Eye size={12} /> {status.vision_line}
      </div>
      {!v2 && notes}

      <div className="space-y-3">
        {status.providers.map((p) => (
          <div key={p.provider} className="rounded border border-border p-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              <span className="font-semibold text-fg">{p.label}</span>
              <span className="text-fg-muted">{p.key_set ? <>key <code>{p.key_hint}</code> stored (encrypted)</> : "no key"}</span>
              {p.key_set && <span className="text-fg-muted">last used {when(p.last_used_at)}</span>}
              {p.key_set && <span className="text-fg-muted">this month ≈ {formatUsd(p.month_spend_usd)}</span>}
              {p.last_test_ok === true && <span className="text-up">test ok {when(p.last_tested_at)}</span>}
              {p.last_test_ok === false && <span className="text-down">test failed: {p.last_test_error}</span>}
            </div>
            <div className="mt-1 text-xs text-fg-muted">
              Create an API key in the provider's developer console:{" "}
              <a href={p.console_url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-0.5 text-brand hover:underline">
                {p.console_url.replace(/^https:\/\//, "")} <ExternalLink size={10} />
              </a>
            </div>
            <div className="mt-2 grid gap-2 md:grid-cols-[2fr_1fr_auto]">
              <input className={input} type="password" autoComplete="off" spellCheck={false} aria-label={`${p.label} API key`}
                     placeholder={p.key_set ? "Paste a new key to replace the stored one" : "Paste your API key"}
                     value={keys[p.provider]} onChange={(e) => setKeys((k) => ({ ...k, [p.provider]: e.target.value }))} />
              <select className={input} aria-label={`${p.label} model`} value={p.model} disabled={busy || !p.key_set}
                      onChange={(e) => savePrefs({ models: { [p.provider]: e.target.value === p.default_model ? "" : e.target.value } }, "Model saved.")}>
                {p.models.map((m) => <option key={m} value={m}>{m}{m === p.default_model ? " (default)" : ""}</option>)}
              </select>
              <div className="flex items-center gap-2">
                <button className={button} disabled={busy || !keys[p.provider].trim()} onClick={() => void saveKey(p)}>{p.key_set ? "Replace" : "Add"}</button>
                <button className={button} disabled={busy || !p.key_set} onClick={() => void testKey(p)}>Test</button>
                {p.key_set && (
                  <button className="text-xs text-down hover:underline disabled:opacity-50" disabled={busy}
                          onClick={() => void run(`${p.label} key removed.`, () => api.byokDeleteKey(p.provider))}>Remove</button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-2 text-xs">
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-fg">
            <input type="checkbox" checked={status.enabled} disabled={busy} onChange={(e) => savePrefs({ enabled: e.target.checked })} />
            Vision chart review on
          </label>
          <label className="flex items-center gap-2 text-fg-muted">
            Charts go to
            <select className="rounded bg-surface-2 border border-border px-2 py-1 text-xs" disabled={busy} value={status.provider ?? "auto"}
                    onChange={(e) => savePrefs({ provider: e.target.value as ByokProviderName | "auto" })}>
              <option value="auto">first provider with a key</option>
              {withKeys.map((p) => <option key={p.provider} value={p.provider}>{p.label}</option>)}
            </select>
          </label>
          <div>
            <div className="text-fg-muted mb-1">Which charts to audit</div>
            <div className="flex flex-wrap gap-3">
              {AUDIT_LABELS.map(({ key, label }) => {
                const v3 = key !== "proposals";
                const off = v3 && status.scope != null && !status.scope.v3_enabled;
                return (
                  <label key={key} className={`flex items-center gap-1 ${off ? "text-fg-muted" : "text-fg"}`}
                         title={off ? "Not enabled for your organisation yet" : undefined}>
                    <input type="checkbox" checked={status.audit[key]} disabled={busy || off} onChange={(e) => savePrefs({ audit: { [key]: e.target.checked } })} /> {label}
                  </label>
                );
              })}
            </div>
            {!v2 && scopeLine}
          </div>
          <div>
            <div className="text-fg-muted mb-1">Where reports go</div>
            <div className="flex flex-wrap gap-3">
              {CHANNEL_LABELS.map(({ key, label }) => (
                <label key={key} className="flex items-center gap-1 text-fg">
                  <input type="checkbox" checked={status.channels[key]} disabled={busy} onChange={(e) => savePrefs({ channels: { [key]: e.target.checked } })} /> {label}
                </label>
              ))}
            </div>
          </div>
        </div>
        <div className="space-y-2">
          <div className="text-fg-muted">Spend caps (USD, your own key) - a hard stop: Vision pauses at a cap until it resets ({status.spend.resets}).</div>
          <div className="grid grid-cols-[auto_1fr] items-center gap-2">
            <span>Daily</span>
            <input className={input} inputMode="decimal" aria-label="Daily cap in USD" value={caps.daily} onChange={(e) => setCaps({ ...caps, daily: e.target.value })} />
            <span>Monthly</span>
            <input className={input} inputMode="decimal" aria-label="Monthly cap in USD" value={caps.monthly} onChange={(e) => setCaps({ ...caps, monthly: e.target.value })} />
          </div>
          <button className={button} disabled={busy} onClick={saveCaps}>Save caps</button>
          {[{ name: "Today", usage: today }, { name: `Month ${status.spend.month}`, usage: month }].map(({ name, usage }) => (
            <div key={name}>
              <div className={usage.reached ? "text-warn" : "text-fg-muted"}>{name}: {usage.label}</div>
              <div className="mt-0.5 h-1.5 w-full rounded bg-border overflow-hidden">
                <div className={`h-full ${usage.reached ? "bg-warn" : "bg-brand"}`} style={{ width: `${usage.pct}%` }} />
              </div>
            </div>
          ))}
          <div className="text-xs text-fg-muted">Estimates from the platform's own metering; your provider's bill is the authority.</div>
        </div>
      </div>
      {error && <div className="mt-3 text-sm text-down">{error}</div>}
      {message && <div className="mt-3 text-sm text-up">{message}</div>}
    </Card>
  );
}
