import { Copy, KeyRound } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card } from "./ui";
import type { ApiKey } from "../types";

const input = "w-full rounded bg-panel2 border border-border px-2 py-1 text-xs";

/** Phase K3: public API keys - created by an OWNER, shown once, scoped and rate-limited. */
export default function ApiKeysCard() {
  const { user } = useAuth();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [scopes, setScopes] = useState<Record<string, string>>({});
  const [form, setForm] = useState({ name: "", scopes: ["read:positions", "read:orders"] as string[], rate: "60", expires: "" });
  const [fresh, setFresh] = useState<ApiKey | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isOwner = user?.role === "OWNER" || user?.role === "SUPER_ADMIN";

  function refresh() {
    api.listApiKeys().then(setKeys).catch((e) => setError(String(e)));
    api.apiKeyScopes().then(setScopes).catch(() => {});
  }
  useEffect(refresh, []);

  async function create() {
    setBusy(true); setError(null);
    try {
      const k = await api.createApiKey({ name: form.name, scopes: form.scopes, rate_limit_per_minute: Number(form.rate) || 60, expires_in_days: form.expires ? Number(form.expires) : null });
      setFresh(k); setForm({ ...form, name: "" }); refresh();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  const toggle = (s: string) => setForm({ ...form, scopes: form.scopes.includes(s) ? form.scopes.filter((x) => x !== s) : [...form.scopes, s] });

  return (
    <Card title="Public API keys">
      <p className="text-xs text-muted mb-3">
        Keys reach only <code>/api/public/v1/*</code> (never the console endpoints), only within their scopes, and are metered against
        the plan's daily allowance. The developer reference is at <code>GET /api/public/v1/docs</code>. Send the key as the
        <code> X-API-Key</code> header. A key is shown once - store it in your own secret manager.
      </p>

      {fresh?.key && (
        <div className="mb-3 rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-3 text-xs">
          <div className="font-bold text-emerald-400 mb-1">New key "{fresh.name}" - copy it now, it will not be shown again</div>
          <div className="flex gap-2">
            <input readOnly className="flex-1 rounded bg-panel2 border border-border px-2 py-1 font-mono" value={fresh.key} />
            <button onClick={() => navigator.clipboard?.writeText(fresh.key ?? "")} className="rounded border border-border px-2 hover:bg-panel2"><Copy size={13} /></button>
          </div>
        </div>
      )}

      {isOwner && (
        <div className="rounded-lg border border-border bg-panel2/40 p-3 space-y-2 mb-3">
          <div className="grid grid-cols-3 gap-2">
            <input className={`${input} col-span-1`} placeholder="Key name (e.g. my-bot)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input className={input} placeholder="Requests / minute (max 600)" value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} />
            <input className={input} placeholder="Expires in days (blank = never)" value={form.expires} onChange={(e) => setForm({ ...form, expires: e.target.value })} />
          </div>
          <div className="flex flex-wrap gap-2 text-[11px]">
            {Object.entries(scopes).map(([s, d]) => (
              <label key={s} title={d} className={`flex items-center gap-1 rounded border px-2 py-0.5 cursor-pointer ${form.scopes.includes(s) ? "border-brand/50 text-brand bg-brand/10" : "border-border text-muted"}`}>
                <input type="checkbox" className="hidden" checked={form.scopes.includes(s)} onChange={() => toggle(s)} />{s}
              </label>
            ))}
          </div>
          <button disabled={busy || !form.name || form.scopes.length === 0} onClick={create} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">Create key</button>
        </div>
      )}

      {keys.length === 0 ? <div className="text-xs text-muted">No API keys yet.</div> : (
        <table className="w-full text-xs"><tbody>
          {keys.map((k) => (
            <tr key={k.id} className={`border-t border-border/60 ${k.revoked_at ? "opacity-50" : ""}`}>
              <td className="py-1 font-bold"><KeyRound size={12} className="inline mr-1 text-brand" />{k.name}</td>
              <td className="py-1 font-mono text-muted">atp_{k.key_prefix}_…</td>
              <td className="py-1 text-muted">{k.scopes.join(", ")}</td>
              <td className="py-1 text-muted">{k.rate_limit_per_minute}/min</td>
              <td className="py-1 text-muted">{k.last_used_at ? `used ${new Date(k.last_used_at).toLocaleString()}` : "never used"}{k.expires_at ? ` · expires ${new Date(k.expires_at).toLocaleDateString()}` : ""}</td>
              <td className="py-1 text-right">{k.revoked_at ? <span className="text-muted">revoked</span> : isOwner && <button disabled={busy} onClick={() => api.revokeApiKey(k.id).then(refresh).catch((e) => setError(String(e)))} className="text-danger hover:underline">Revoke</button>}</td>
            </tr>
          ))}
        </tbody></table>
      )}
      {error && <div className="mt-3 text-sm text-danger">{error}</div>}
    </Card>
  );
}
