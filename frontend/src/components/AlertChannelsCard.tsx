import { BellRing, Mail, MessageSquare, Send, Smartphone, Webhook } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Card } from "../components/ui";
import type { AlertChannel, AlertChannelType, AlertDelivery, NotificationSeverity, TelegramInboundStatus } from "../types";

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
  // Phase BE: two-way Telegram - commands (/brief /positions /risk ...) and Approve/Reject buttons on
  // PAPER monitor proposals. Off by default; LIVE decisions stay on the web with the authenticator.
  const [inbound, setInbound] = useState<TelegramInboundStatus | null>(null);
  const [inboundChats, setInboundChats] = useState("");
  const [wh, setWh] = useState({ url: "", secret: "", event_types: "", min_severity: "WARNING" as NotificationSeverity, enabled: true });
  const [sms, setSms] = useState({
    preset: "msg91", url: "", headers: "", body_template: "", to_numbers: "", content_type: "application/json",
    min_severity: "CRITICAL" as NotificationSeverity, enabled: true,
  });
  const [pushSupported] = useState(() => typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window);
  const [em, setEm] = useState({
    smtp_host: "", smtp_port: "587", username: "", password: "", use_tls: true, from_address: "", to_addresses: "",
    min_severity: "CRITICAL" as NotificationSeverity, enabled: true,
  });

  function refresh() {
    api.listAlertChannels().then((list) => {
      setChannels(list);
      const t = list.find((c) => c.channel_type === "TELEGRAM");
      if (t) setTg((s) => ({ ...s, chat_id: String(t.config.chat_id ?? ""), min_severity: t.min_severity, enabled: t.enabled }));
      const w = list.find((c) => c.channel_type === "WEBHOOK");
      if (w) setWh((s) => ({ ...s, url: String(w.config.url ?? ""), event_types: ((w.config.event_types as string[]) ?? []).join(", "), min_severity: w.min_severity, enabled: w.enabled }));
      const s = list.find((c) => c.channel_type === "SMS");
      if (s) {
        setSms((prev) => ({
          ...prev, preset: "custom", url: String(s.config.url ?? ""), body_template: String(s.config.body_template ?? ""),
          content_type: String(s.config.content_type ?? "application/json"), to_numbers: ((s.config.to_numbers as string[]) ?? []).join(", "),
          min_severity: s.min_severity, enabled: s.enabled,
        }));
      }
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
    api.telegramInboundStatus().then((s) => { setInbound(s); setInboundChats(s.allowed_chat_ids.slice(1).join(", ")); }).catch(() => setInbound(null));
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

  const SMS_PRESETS: Record<string, { url: string; headers: string; body_template: string; content_type: string; hint: string }> = {
    msg91: { url: "https://control.msg91.com/api/v5/flow/", headers: "authkey: <your MSG91 auth key>", content_type: "application/json",
      body_template: '{"template_id":"<DLT approved template id>","recipients":[{"mobiles":"{to}","message":"{text}"}]}', hint: "MSG91 Flow API; a DLT-approved template is required in India." },
    twilio: { url: "https://api.twilio.com/2010-04-01/Accounts/<ACCOUNT_SID>/Messages.json", headers: "Authorization: Basic <base64(ACCOUNT_SID:AUTH_TOKEN)>",
      content_type: "application/x-www-form-urlencoded", body_template: "From=<+1...>&To={to}&Body={text}", hint: "Twilio Messages API (form-encoded)." },
    custom: { url: "", headers: "", body_template: "", content_type: "application/json", hint: "Any HTTPS gateway: placeholders {to} {text} {title} {severity}." },
  };

  function applyPreset(name: string) {
    const p = SMS_PRESETS[name];
    setSms((prev) => ({ ...prev, preset: name, ...(name === "custom" ? {} : { url: p.url, headers: p.headers, body_template: p.body_template, content_type: p.content_type }) }));
  }

  function parseHeaders(text: string): Record<string, string> {
    const out: Record<string, string> = {};
    text.split("\n").forEach((line) => { const i = line.indexOf(":"); if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim(); });
    return out;
  }

  async function enablePushHere() {
    const { public_key } = await api.pushPublicKey();
    const registration = await navigator.serviceWorker.register("/sw.js");
    const permission = await Notification.requestPermission();
    if (permission !== "granted") throw new Error("Notification permission was not granted in the browser");
    const padded = public_key.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (public_key.length % 4)) % 4);
    const raw = Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
    const sub = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: raw });
    const json = sub.toJSON();
    const label = `${navigator.platform || "device"} · ${/Chrome|Firefox|Safari|Edg/.exec(navigator.userAgent)?.[0] ?? "browser"}`;
    await api.upsertAlertChannel("push", { enabled: true, min_severity: (stored("PUSH")?.min_severity ?? "WARNING") as NotificationSeverity,
      config: { subscription: { endpoint: json.endpoint, p256dh: json.keys?.p256dh, auth: json.keys?.auth, label } } });
  }

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
    <Card title="Alert delivery (Telegram / email / webhook / push / SMS)">
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
          {stored("TELEGRAM") && inbound && (
            <div className="mt-2 border-t border-border pt-2 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-slate-200">Two-way Telegram (commands + PAPER approvals)</span>
                <span className={inbound.inbound_enabled ? "text-accent" : "text-muted"}>{inbound.inbound_enabled ? "on" : "off"}</span>
              </div>
              {inbound.flag_enabled === false && <div className="text-[11px] text-amber-400">Feature flag <code>telegram_inbound</code> is off for this organisation - ask the platform admin to enable it.</div>}
              <p className="text-[11px] text-muted">
                Chat commands: /brief /positions /risk /news /levels /thesis /why, or ask a question. Approve/Reject buttons appear on
                monitor proposals for <b>PAPER</b> deployments only ({inbound.telegram_actions.join(", ")}); exits and every LIVE decision stay on the web with your authenticator.
              </p>
              <input className={input} placeholder="Extra allowed chat ids (comma separated, up to 10); the alert chat id is always allowed" value={inboundChats} onChange={(e) => setInboundChats(e.target.value)} />
              <div className="flex flex-wrap items-center gap-2">
                <button disabled={busy || inbound.flag_enabled === false} onClick={() => run(inbound.inbound_enabled ? "Two-way Telegram switched off." : "Two-way Telegram switched on - now register the webhook.",
                  () => api.telegramInboundConfigure({ enabled: !inbound.inbound_enabled, allowed_chat_ids: inboundChats.split(",").map((c) => c.trim()).filter(Boolean) }))}
                  className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">{inbound.inbound_enabled ? "Turn off" : "Turn on"}</button>
                <button disabled={busy || !inbound.inbound_enabled} onClick={() => run("Allowed chats saved.",
                  () => api.telegramInboundConfigure({ enabled: true, allowed_chat_ids: inboundChats.split(",").map((c) => c.trim()).filter(Boolean) }))}
                  className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1 text-xs disabled:opacity-50">Save chats</button>
                <button disabled={busy || !inbound.inbound_enabled || !inbound.has_secret} onClick={() => run("Webhook registered with Telegram.", async () => {
                  const r = await api.telegramInboundRegister();
                  if (!r.ok) throw new Error(r.description ?? "Telegram refused the webhook");
                })} className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1 text-xs disabled:opacity-50">Register webhook</button>
              </div>
              <div className="text-[11px] text-muted break-all">Webhook: {inbound.webhook_url} (HTTPS required; the secret header is set automatically)</div>
            </div>
          )}
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

        <div className="rounded-lg border border-border bg-panel2/40 p-3 space-y-2 lg:col-span-2">
          <div className="flex items-center gap-2 text-sm font-bold text-emerald-400"><Webhook size={14} /> Webhook (signed JSON POST)</div>
          <div className="text-[11px]">{status("WEBHOOK")}</div>
          <p className="text-[11px] text-muted">
            Every event is POSTed as JSON with <code>X-ATP-Signature: sha256=HMAC-SHA256(secret, timestamp + "." + body)</code> and
            <code> X-ATP-Timestamp</code>, so your receiver (a bot, n8n, Zapier, a Slack relay) can verify it came from here. HTTPS only.
          </p>
          <div className="grid grid-cols-3 gap-2">
            <input className={`${input} col-span-2`} placeholder="https://your-endpoint.example/atp" value={wh.url} onChange={(e) => setWh({ ...wh, url: e.target.value })} />
            <input className={input} type="password" autoComplete="off" placeholder="Shared secret, 16+ chars (blank = keep)" value={wh.secret} onChange={(e) => setWh({ ...wh, secret: e.target.value })} />
          </div>
          <input className={input} placeholder="Event types to send, comma separated (blank = all): ORDER_FILLED, DAILY_LOSS_LIMIT, ..." value={wh.event_types} onChange={(e) => setWh({ ...wh, event_types: e.target.value })} />
          <div className="flex items-center gap-3 text-xs">
            <label className="flex items-center gap-1 text-muted">floor
              <select className="rounded bg-panel2 border border-border px-1 py-0.5" value={wh.min_severity} onChange={(e) => setWh({ ...wh, min_severity: e.target.value as NotificationSeverity })}>
                {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-1 text-muted"><input type="checkbox" checked={wh.enabled} onChange={(e) => setWh({ ...wh, enabled: e.target.checked })} /> enabled</label>
          </div>
          <div className="flex gap-2">
            <button disabled={busy || !wh.url} onClick={() => run("Webhook channel saved.", () => api.upsertAlertChannel("webhook", {
              enabled: wh.enabled, min_severity: wh.min_severity,
              config: { url: wh.url, secret: wh.secret || null, event_types: wh.event_types.split(",").map((a) => a.trim()).filter(Boolean) },
            }))} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">Save</button>
            <button disabled={busy || !stored("WEBHOOK")} onClick={() => test("WEBHOOK")} className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1 text-xs disabled:opacity-50">Send test</button>
            {stored("WEBHOOK") && <button disabled={busy} onClick={() => run("Webhook channel removed.", () => api.deleteAlertChannel("webhook"))} className="text-xs text-danger hover:underline">Remove</button>}
          </div>
        </div>

        <div className="rounded-lg border border-border bg-panel2/40 p-3 space-y-2">
          <div className="flex items-center gap-2 text-sm font-bold text-violet-400"><Smartphone size={14} /> Browser push</div>
          <div className="text-[11px]">{status("PUSH")}</div>
          <p className="text-[11px] text-muted">
            System notifications on this device even with the tab closed (Chrome, Edge, Firefox, Safari 16+). Messages are
            end-to-end encrypted to this browser; the push service never sees them. Add each device you use.
          </p>
          {((stored("PUSH")?.config.devices as { label: string; endpoint: string }[] | undefined) ?? []).map((d) => (
            <div key={d.endpoint} className="flex items-center justify-between text-xs">
              <span className="text-slate-200">{d.label}</span>
              <button disabled={busy} onClick={() => run("Device removed.", () => (stored("PUSH")?.config.count as number) > 1
                ? api.upsertAlertChannel("push", { enabled: true, min_severity: stored("PUSH")!.min_severity, config: { remove_endpoint: d.endpoint } })
                : api.deleteAlertChannel("push"))} className="text-danger hover:underline">remove</button>
            </div>
          ))}
          <div className="flex gap-2">
            <button disabled={busy || !pushSupported} title={pushSupported ? "" : "This browser does not support Web Push"} onClick={() => run("Push enabled on this device.", enablePushHere)} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">Enable on this device</button>
            <button disabled={busy || !stored("PUSH")} onClick={() => test("PUSH")} className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1 text-xs disabled:opacity-50">Send test</button>
            {stored("PUSH") && <button disabled={busy} onClick={() => run("Push channel removed.", () => api.deleteAlertChannel("push"))} className="text-xs text-danger hover:underline">Remove all</button>}
          </div>
        </div>

        <div className="rounded-lg border border-border bg-panel2/40 p-3 space-y-2">
          <div className="flex items-center gap-2 text-sm font-bold text-teal-400"><MessageSquare size={14} /> SMS (any HTTP gateway)</div>
          <div className="text-[11px]">{status("SMS")}</div>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-muted">Preset</span>
            <select className="rounded bg-panel2 border border-border px-1 py-0.5" value={sms.preset} onChange={(e) => applyPreset(e.target.value)}>
              <option value="msg91">MSG91</option><option value="twilio">Twilio</option><option value="custom">Custom</option>
            </select>
            <span className="text-[11px] text-muted">{SMS_PRESETS[sms.preset]?.hint}</span>
          </div>
          <input className={input} placeholder="Gateway URL (https)" value={sms.url} onChange={(e) => setSms({ ...sms, url: e.target.value })} />
          <textarea className={`${input} font-mono text-[11px]`} rows={2} placeholder={"Headers, one per line: Name: value (blank = keep stored)"} value={sms.headers} onChange={(e) => setSms({ ...sms, headers: e.target.value })} />
          <textarea className={`${input} font-mono text-[11px]`} rows={2} placeholder="Body template with {to} and {text}" value={sms.body_template} onChange={(e) => setSms({ ...sms, body_template: e.target.value })} />
          <div className="grid grid-cols-3 gap-2">
            <input className={`${input} col-span-2`} placeholder="Recipients, comma separated (+91...)" value={sms.to_numbers} onChange={(e) => setSms({ ...sms, to_numbers: e.target.value })} />
            <input className={input} placeholder="Content type" value={sms.content_type} onChange={(e) => setSms({ ...sms, content_type: e.target.value })} />
          </div>
          <div className="flex items-center gap-3 text-xs">
            <label className="flex items-center gap-1 text-muted">floor
              <select className="rounded bg-panel2 border border-border px-1 py-0.5" value={sms.min_severity} onChange={(e) => setSms({ ...sms, min_severity: e.target.value as NotificationSeverity })}>
                {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-1 text-muted"><input type="checkbox" checked={sms.enabled} onChange={(e) => setSms({ ...sms, enabled: e.target.checked })} /> enabled</label>
          </div>
          <div className="flex gap-2">
            <button disabled={busy || !sms.url || !sms.to_numbers} onClick={() => run("SMS channel saved.", () => api.upsertAlertChannel("sms", {
              enabled: sms.enabled, min_severity: sms.min_severity,
              config: { url: sms.url, headers: parseHeaders(sms.headers), body_template: sms.body_template, content_type: sms.content_type,
                method: "POST", to_numbers: sms.to_numbers.split(",").map((n) => n.trim()).filter(Boolean) },
            }))} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">Save</button>
            <button disabled={busy || !stored("SMS")} onClick={() => test("SMS")} className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1 text-xs disabled:opacity-50">Send test</button>
            {stored("SMS") && <button disabled={busy} onClick={() => run("SMS channel removed.", () => api.deleteAlertChannel("sms"))} className="text-xs text-danger hover:underline">Remove</button>}
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
