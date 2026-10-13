/**
 * S10 seam (flag `alerts_whatsapp_dlt`): the WhatsApp alert channel - a WhatsApp Business gateway (Meta Cloud API or a
 * BSP) through a request template, like SMS. WhatsApp sends business-initiated messages only as a PRE-APPROVED template
 * to numbers that opted in: the form asks for the template name and for the opt-in confirmation. Paid plans only (each
 * message is billed by the provider). Secrets (headers) are write-only.
 */
import { MessageCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { AlertChannel, NotificationSeverity } from "../types";

const input = "w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm";
const SEVERITIES: NotificationSeverity[] = ["INFO", "WARNING", "CRITICAL"];
export const META_TEMPLATE =
  '{"messaging_product":"whatsapp","to":"{to}","type":"template","template":{"name":"{template}","language":{"code":"{language}"},'
  + '"components":[{"type":"body","parameters":[{"type":"text","text":"{text}"}]}]}}';

export function parseHeaders(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  text.split("\n").forEach((line) => { const i = line.indexOf(":"); if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim(); });
  return out;
}

export interface WhatsAppSectionProps {
  channel: AlertChannel | undefined;
  busy: boolean;
  run: (label: string, fn: () => Promise<unknown>) => void;
  test: () => void;
  status: JSX.Element;
}

export default function WhatsAppSection({ channel, busy, run, test, status }: WhatsAppSectionProps) {
  const [wa, setWa] = useState({
    url: "https://graph.facebook.com/v19.0/<PHONE_NUMBER_ID>/messages", headers: "", body_template: META_TEMPLATE, template_name: "",
    language: "en", to_numbers: "", opt_in: false, min_severity: "CRITICAL" as NotificationSeverity, enabled: true,
  });
  useEffect(() => {
    if (!channel) return;
    const c = channel.config;
    setWa((s) => ({
      ...s, url: String(c.url ?? s.url), body_template: String(c.body_template ?? s.body_template), template_name: String(c.template_name ?? ""),
      language: String(c.language ?? "en"), to_numbers: ((c.to_numbers as string[]) ?? []).join(", "), opt_in: Boolean(c.opt_in_confirmed),
      min_severity: channel.min_severity, enabled: channel.enabled,
    }));
  }, [channel]);
  return (
    <div className="rounded-lg border border-border bg-surface-2/40 p-3 space-y-2" data-testid="whatsapp-section">
      <div className="flex items-center gap-2 text-sm font-bold text-fg"><MessageCircle size={14} /> WhatsApp (Business gateway, paid plans)</div>
      <div className="text-[11px]">{status}</div>
      <p className="text-[11px] text-fg-muted">
        WhatsApp delivers alerts only as a template your provider has approved, to numbers that opted in to messages from
        your organisation. Placeholders: {"{to} {template} {language} {title} {text} {severity}"}.
      </p>
      <input className={input} placeholder="Gateway URL (https)" value={wa.url} onChange={(e) => setWa({ ...wa, url: e.target.value })} />
      <textarea className={`${input} font-mono text-[11px]`} rows={2} placeholder="Headers, one per line: Name: value (blank = keep stored)"
                value={wa.headers} onChange={(e) => setWa({ ...wa, headers: e.target.value })} />
      <textarea className={`${input} font-mono text-[11px]`} rows={3} placeholder="Body template with {template} and {to}"
                value={wa.body_template} onChange={(e) => setWa({ ...wa, body_template: e.target.value })} />
      <div className="grid grid-cols-3 gap-2">
        <input className={input} placeholder="Approved template name" value={wa.template_name} onChange={(e) => setWa({ ...wa, template_name: e.target.value })} />
        <input className={input} placeholder="Language (en)" value={wa.language} onChange={(e) => setWa({ ...wa, language: e.target.value })} />
        <input className={input} placeholder="Recipients (+91...)" value={wa.to_numbers} onChange={(e) => setWa({ ...wa, to_numbers: e.target.value })} />
      </div>
      <label className="flex items-center gap-1 text-xs text-fg">
        <input type="checkbox" checked={wa.opt_in} onChange={(e) => setWa({ ...wa, opt_in: e.target.checked })} />
        Every recipient opted in to WhatsApp messages from my organisation
      </label>
      <div className="flex items-center gap-3 text-xs">
        <label className="flex items-center gap-1 text-fg-muted">floor
          <select className="rounded bg-surface-2 border border-border px-1 py-0.5" value={wa.min_severity} onChange={(e) => setWa({ ...wa, min_severity: e.target.value as NotificationSeverity })}>
            {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1 text-fg-muted"><input type="checkbox" checked={wa.enabled} onChange={(e) => setWa({ ...wa, enabled: e.target.checked })} /> enabled</label>
      </div>
      <div className="flex gap-2">
        <button disabled={busy || !wa.url || !wa.to_numbers || !wa.template_name || !wa.opt_in} onClick={() => run("WhatsApp channel saved.", () => api.upsertAlertChannel("whatsapp", {
          enabled: wa.enabled, min_severity: wa.min_severity,
          config: { url: wa.url, headers: parseHeaders(wa.headers), body_template: wa.body_template, template_name: wa.template_name,
            language: wa.language || "en", to_numbers: wa.to_numbers.split(",").map((n) => n.trim()).filter(Boolean), opt_in_confirmed: wa.opt_in },
        }))} className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1 text-xs disabled:opacity-50">Save</button>
        <button disabled={busy || !channel} onClick={test} className="rounded border border-border hover:bg-surface-2 text-fg px-3 py-1 text-xs disabled:opacity-50">Send test</button>
        {channel && <button disabled={busy} onClick={() => run("WhatsApp channel removed.", () => api.deleteAlertChannel("whatsapp"))} className="text-xs text-down hover:underline">Remove</button>}
      </div>
    </div>
  );
}
