import { BellRing, Mail, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Card } from "../components/ui";
import type { AlertChannel, AlertChannelType, AlertDelivery, NotificationSeverity } from "../types";

const SEVERITIES: NotificationSeverity[] = ["INFO", "WARNING", "CRITICAL"];

const input = "w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm";

/**
 * Settings card for out-of-app alerts. The worker raises TOKEN_EXPIRED / SYSTEM_FAILURE /
 * DAILY_LOSS_LIMIT while no browser is open - this is how they reach a phone. Secrets are
 * write-only: the API returns a masked summary and keeps the stored secret when a field is
 * saved blank.
 */
export default function AlertChannelsCard() {
  const [channels, setChannels] = useState<AlertChannel[]>([]);
  const [deliveries, setDeliveries] = useState<AlertDelivery[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [tg, setTg] = useState({ bot_token: "", chat_id: "", min_severity: "WARNING" as NotificationSeverity, enabled: true });
  const [em, setEm] = useState({
    smtp_host: "", smtp_port: "587", username: "", password: "", use_tls: true, from_address: "", to_addresses: "",
    min_severity: "CRITICAL" as NotificationSeverity, enabled: true,
  });

  function refresh() {
    api.listAlertChannels().then((list) => {
      setChannels(list);
      const t = list.find((c) => c.channel_type === "TELEGRAM");
      if (t) setTg((s) => ({ ...s, chat_id: String(t.config.chat_id ?? ""), min_severity: t.min_severity, enabled: t.enabled }));
      const e = list.find((c) => c.channel_type === "EMAIL");
      if (e) {
        setEm((s) => ({
          ...s, smtp_host: String(e.config.smtp_host ?? ""), smtp_port: String(e.config.smtp_port ?? "587"),
          username: String(e.config.username ?? ""), use_tls: Boolean(e.config.use_tls ?? true),
          from_address: String(e.config.from_address ?? ""), to_addresses: ((e.config.to_addresses as string[]) ?? []).join(", "),
          min_severity: e.min_severity, enabled: e.enabled,
        }));
      }
    }).catch((e) => setError(String(e)));
    api.listAlertDeliveries(10).then(setDeliveries).catch(() => {});
  }

  useEffect(refresh, []);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await fn();
      setMessage(label);
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function test(type: AlertChannelType) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.testAlertChannel(type);
      if (result.ok) setMessage(result.detail);
      else setError(result.detail);
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const stored = (type: AlertChannelType) => channels.find((c) => c.channel_type === type);

  function status(type: AlertChannelType) {
    const c = stored(type);
    if (!c) return <span className="text-muted">not configured</span>;
    return (
      <span className={c.last_error ? "text-danger" : c.enabled ? "text-accent" : "text-muted"}>
        {c.enabled ? "enabled" : "disabled"} · floor {c.min_severity}
        {c.last_delivered_at && ` · last sent ${new Date(c.last_delivered_at).toLocaleString()}`}
        {c.last_error && ` · ${c.last_error}`}
      </span>
    );
  }

  return (
    <Card title="Alert delivery (Telegram / email)">
      <p className="text-xs text-muted mb-3">
        The trading worker raises CRITICAL alerts (broker session expired, stop-loss could not be
        placed, deployment auto-paused, daily loss limit) while no browser is open. Configure at
        least one channel so they reach you. Secrets are stored encrypted and never shown again;
        leave a secret blank when editing to keep the stored one.
      </p>

      <div className="grid lg:grid-cols-2 gap-4">
        <div className="rounded-lg border border-border bg-panel2/40 p-3 space-y-2">
          <div className="flex items-center gap-2 text-sm font-bold text-sky-400"><Send size={14} /> Telegram</div>
          <div className="text-[11px]">{status("TELEGRAM")}</div>
          <input className={input} type="password" autoComplete="off" placeholder="Bot token from @BotFather (blank = keep stored)" value={tg.bot_token} onChange={(e) => setTg({ ...tg, bot_token: e.target.value })} />
          <input className={input} placeholder="Chat id (your user id or a group id)" value={tg.chat_id} onChange={(e) => setTg({ ...tg, chat_id: e.target.value })} />
          <div className="flex items-center gap-3 text-xs">
            <label className="flex items-center gap-1 text-muted">floor
              <select className="rounded bg-panel2 border border-border px-1 py-0.5" value={tg.min_severity} onChange={(e) => setTg({ ...tg, min_severity: e.target.value as NotificationSeverity })}>
                {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-1 text-muted"><input type="checkbox" checked={tg.enabled} onChange={(e) => setTg({ ...tg, enabled: e.target.checked })} /> enabled</label>
          </div>
          <div className="flex gap-2">
            <button disabled={busy || !tg.chat_id} onClick={() => run("Telegram channel saved.", () => api.upsertAlertChannel("telegram", { enabled: tg.enabled, min_severity: tg.min_severity, config: { bot_token: tg.bot_token, chat_id: tg.chat_id } }))} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">Save</button>
            <button disabled={busy || !stored("TELEGRAM")} onClick={() => test("TELEGRAM")} className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1 text-xs disabled:opacity-50">Send test</button>
            {stored("TELEGRAM") && <button disabled={busy} onClick={() => run("Telegram channel removed.", () => api.deleteAlertChannel("telegram"))} className="text-xs text-danger hover:underline">Remove</button>}
          </div>
        </div>

        <div className="rounded-lg border border-border bg-panel2/40 p-3 space-y-2">
          <div className="flex items-center gap-2 text-sm font-bold text-amber-400"><Mail size={14} /> Email (SMTP)</div>
          <div className="text-[11px]">{status("EMAIL")}</div>
          <div className="grid grid-cols-3 gap-2">
            <input className={`${input} col-span-2`} placeholder="SMTP host" value={em.smtp_host} onChange={(e) => setEm({ ...em, smtp_host: e.target.value })} />
            <input className={input} placeholder="Port" value={em.smtp_port} onChange={(e) => setEm({ ...em, smtp_port: e.target.value })} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input className={input} autoComplete="off" placeholder="Username" value={em.username} onChange={(e) => setEm({ ...em, username: e.target.value })} />
            <input className={input} type="password" autoComplete="off" placeholder="Password (blank = keep stored)" value={em.password} onChange={(e) => setEm({ ...em, password: e.target.value })} />
          </div>
          <input className={input} placeholder="From address" value={em.from_address} onChange={(e) => setEm({ ...em, from_address: e.target.value })} />
          <input className={input} placeholder="To addresses, comma separated" value={em.to_addresses} onChange={(e) => setEm({ ...em, to_addresses: e.target.value })} />
          <div className="flex items-center gap-3 text-xs">
            <label className="flex items-center gap-1 text-muted"><input type="checkbox" checked={em.use_tls} onChange={(e) => setEm({ ...em, use_tls: e.target.checked })} /> STARTTLS</label>
            <label className="flex items-center gap-1 text-muted">floor
              <select className="rounded bg-panel2 border border-border px-1 py-0.5" value={em.min_severity} onChange={(e) => setEm({ ...em, min_severity: e.target.value as NotificationSeverity })}>
                {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-1 text-muted"><input type="checkbox" checked={em.enabled} onChange={(e) => setEm({ ...em, enabled: e.target.checked })} /> enabled</label>
          </div>
          <div className="flex gap-2">
            <button
              disabled={busy || !em.smtp_host || !em.from_address || !em.to_addresses}
              onClick={() => run("Email channel saved.", () => api.upsertAlertChannel("email", {
                enabled: em.enabled, min_severity: em.min_severity,
                config: {
                  smtp_host: em.smtp_host, smtp_port: Number(em.smtp_port) || 587, username: em.username || null,
                  password: em.password || null, use_tls: em.use_tls, from_address: em.from_address,
                  to_addresses: em.to_addresses.split(",").map((a) => a.trim()).filter(Boolean),
                },
              }))}
              className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50"
            >
              Save
            </button>
            <button disabled={busy || !stored("EMAIL")} onClick={() => test("EMAIL")} className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1 text-xs disabled:opacity-50">Send test</button>
            {stored("EMAIL") && <button disabled={busy} onClick={() => run("Email channel removed.", () => api.deleteAlertChannel("email"))} className="text-xs text-danger hover:underline">Remove</button>}
          </div>
        </div>
      </div>

      {error && <div className="mt-3 text-sm text-danger">{error}</div>}
      {message && <div className="mt-3 text-sm text-accent">{message}</div>}

      {deliveries.length > 0 && (
        <div className="mt-4">
          <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-muted mb-1.5"><BellRing size={12} /> Recent deliveries</div>
          <table className="w-full text-xs">
            <tbody>
              {deliveries.map((d) => (
                <tr key={d.id} className="border-t border-border">
                  <td className="py-1 pr-3 text-muted whitespace-nowrap">{new Date(d.created_at).toLocaleString()}</td>
                  <td className="py-1 pr-3 capitalize text-slate-300">{d.channel_type.toLowerCase()}</td>
                  <td className="py-1 pr-3 text-slate-200">[{d.severity}] {d.title}</td>
                  <td className={`py-1 pr-3 font-semibold ${d.status === "SENT" ? "text-accent" : d.status === "FAILED" ? "text-danger" : "text-warn"}`}>{d.status}{d.attempts > 1 ? ` (${d.attempts})` : ""}</td>
                  <td className="py-1 text-muted">{d.last_error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
