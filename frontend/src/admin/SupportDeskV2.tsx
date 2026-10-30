import { useCallback, useEffect, useState } from "react";
import { friendlyError } from "../api/client";
import { BUTTON, FOCUS, Panel, Pill } from "./AdminSectionsV2";
import { ago, maskEmail } from "./consoleApi";
import { supportDeskApi, type SupportDeskSettings, type SupportTicket, type SupportTickets, type TicketStatus } from "./supportDeskApi";
import { SkeletonRows } from "../components/primitives";
import { Combobox } from "../ui/Combobox";
import { formatDateTimeIST } from "../ui/format";

/**
 * C12: Admin › Support - the inbox as tickets (every "Send feedback" and "Report a problem" message) with a status, an
 * assignee and the response-time targets, and the desk settings (the support WhatsApp number and e-mail users see while
 * the market is open, and the targets). SUPER_ADMIN with two-factor on the server; every change is audited there.
 * Nothing is hard-coded: with no target set, no ticket is ever "late".
 */

const STATUS_LABEL: Record<TicketStatus, string> = { open: "Open", answered: "Answered", closed: "Closed" };
const STATUS_TONE: Record<TicketStatus, "warn" | "ok" | "off"> = { open: "warn", answered: "ok", closed: "off" };
const FILTERS: ("all" | TicketStatus)[] = ["open", "answered", "closed", "all"];
const INPUT = `h-11 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg md:h-9 ${FOCUS}`;

export function hours(h: number | null): string {
  if (h == null) return "no target";
  return h < 1 ? `${Math.round(h * 60)} min` : `${h} h`;
}

export function dueLine(t: SupportTicket): { text: string; late: boolean } | null {
  if (t.status === "closed") return null;
  if (!t.first_response_at && t.first_response_due_at) {
    return { text: t.first_response_late ? `First answer late (due ${ago(t.first_response_due_at)})` : `First answer due ${formatDateTimeIST(t.first_response_due_at)}`,
             late: !!t.first_response_late };
  }
  if (t.resolve_due_at) {
    return { text: t.resolve_late ? "Past the time to resolve" : `Resolve by ${formatDateTimeIST(t.resolve_due_at)}`,
             late: !!t.resolve_late };
  }
  return null;
}

/** The assignee rows: "Nobody assigned", then each admin by masked e-mail. */
export function assigneeOptions(assignees: SupportTickets["assignees"]): { value: string; label: string }[] {
  return [{ value: "", label: "Nobody assigned" }, ...assignees.map((a) => ({ value: String(a.id), label: maskEmail(a.email) }))];
}

export function TicketRow({ t, assignees, onChange }: { t: SupportTicket; assignees: SupportTickets["assignees"]; onChange: (t: SupportTicket) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const due = dueLine(t);
  async function change(c: { status?: TicketStatus; assignee_id?: number | null }) {
    setBusy(true); setError(null);
    try { onChange({ ...t, ...(await supportDeskApi.updateTicket(t.id, c)) }); } catch (e) { setError(friendlyError(e)); } finally { setBusy(false); }
  }
  const ctx = t.context ?? {};
  return (
    <li data-feedback={t.id} data-ticket={t.id} data-ticket-status={t.status} className="py-space-3">
      <div className="flex flex-wrap items-center gap-space-2">
        <span className="text-t13 font-semibold text-fg">#{t.id}</span>
        <Pill tone={STATUS_TONE[t.status]}>{STATUS_LABEL[t.status]}</Pill>
        <Pill tone={t.kind === "problem" ? "bad" : "off"}>{t.kind === "problem" ? "Problem" : "Feedback"}</Pill>
        {due && <span className={`text-t12 ${due.late ? "font-semibold text-down" : "text-fg-muted"}`} data-ticket-late={due.late || undefined}>{due.text}</span>}
      </div>
      <p className="mt-space-1 whitespace-pre-wrap break-words text-t14 text-fg">{t.message}</p>
      <p className="text-t12 text-fg-muted">{maskEmail(t.email)} · organisation {t.tenant_id} · {t.screen} · {t.app_version} · {ago(t.created_at)}</p>
      {ctx.error_id && (
        <p className="text-t12 text-fg-muted" data-ticket-error>
          Last error: <span className="font-mono text-fg">{ctx.error_id}</span>{ctx.error_screen ? ` on ${ctx.error_screen}` : ""}
          {ctx.error_found ? ` · ${ctx.error_status} ${ctx.error_route}` : " · not in this server's recent errors (search the logs)"}
        </p>
      )}
      <div className="mt-space-2 flex flex-wrap items-center gap-space-2">
        <Combobox id={`ticket-status-${t.id}`} label={`Status of #${t.id}`} value={t.status} disabled={busy}
          onChange={(v) => void change({ status: v as TicketStatus })}
          options={(Object.keys(STATUS_LABEL) as TicketStatus[]).map((s) => ({ value: s, label: STATUS_LABEL[s] }))} />
        <Combobox id={`ticket-assignee-${t.id}`} label={`Assignee of #${t.id}`} value={t.assignee_id == null ? "" : String(t.assignee_id)} disabled={busy}
          onChange={(v) => void change({ assignee_id: v ? Number(v) : null })}
          options={assigneeOptions(assignees)} />
        {error && <span className="text-t12 text-down" role="alert">{error}</span>}
      </div>
    </li>
  );
}

export function SupportTicketsPanel() {
  const [filter, setFilter] = useState<"all" | TicketStatus>("open");
  const [data, setData] = useState<SupportTickets | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    let live = true;
    setError(null);
    supportDeskApi.tickets(filter === "all" ? undefined : filter).then((d) => { if (live) setData(d); }, (e) => { if (live) setError(friendlyError(e)); });
    return () => { live = false; };
  }, [filter]);
  useEffect(load, [load]);
  const counts = data?.counts;
  return (
    <Panel title="Support tickets" id="feedback"
      note={<>Every “Send feedback” and “Report a problem” message, newest first. Targets: first answer {hours(data?.targets.first_response_hours ?? null)}, resolve {hours(data?.targets.resolve_hours ?? null)} (set them below).</>}>
      <div className="flex flex-wrap items-center gap-space-2" role="group" aria-label="Show tickets">
        {FILTERS.map((f) => (
          <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}
            className={`${BUTTON} ${filter === f ? "border-brand text-brand" : ""}`}>
            {f === "all" ? "All" : STATUS_LABEL[f]}{counts && f !== "all" ? ` (${counts[f]})` : ""}
          </button>
        ))}
        {data && data.late > 0 && <Pill tone="bad">{data.late} late</Pill>}
      </div>
      {!data ? (error ? <p className="mt-space-3 text-t12 text-down">Could not load: {error}</p> : <SkeletonRows rows={2} label="Loading" />)
        : data.tickets.length === 0 ? <p className="mt-space-3 text-t12 text-fg-muted">No tickets here.</p> : (
          <ul className="mt-space-2 divide-y divide-border">
            {data.tickets.map((t) => (
              <TicketRow key={t.id} t={t} assignees={data.assignees}
                onChange={(next) => setData((d) => d && { ...d, tickets: d.tickets.map((x) => (x.id === next.id ? { ...x, ...next } : x)) })} />
            ))}
          </ul>
        )}
    </Panel>
  );
}

export function SupportDeskSettingsPanel() {
  const [form, setForm] = useState<{ whatsapp: string; email: string; first: string; resolve: string } | null>(null);
  const [saved, setSaved] = useState<SupportDeskSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const fill = (s: SupportDeskSettings) => {
    setSaved(s);
    setForm({ whatsapp: s.whatsapp, email: s.email, first: s.first_response_hours?.toString() ?? "", resolve: s.resolve_hours?.toString() ?? "" });
  };
  useEffect(() => { supportDeskApi.settings().then(fill, (e) => setMessage({ ok: false, text: friendlyError(e) })); }, []);

  async function save() {
    if (!form) return;
    setBusy(true); setMessage(null);
    try {
      fill(await supportDeskApi.saveSettings({
        whatsapp: form.whatsapp.trim(), email: form.email.trim(),
        first_response_hours: form.first.trim() ? Number(form.first) : null, resolve_hours: form.resolve.trim() ? Number(form.resolve) : null,
      }));
      setMessage({ ok: true, text: "Saved." });
    } catch (e) {
      setMessage({ ok: false, text: friendlyError(e) });
    } finally {
      setBusy(false);
    }
  }

  const field = (key: "whatsapp" | "email" | "first" | "resolve", label: string, props: Record<string, string>) => (
    <label className="flex min-w-0 flex-col gap-space-1 text-t12 text-fg-muted">{label}
      <input {...props} value={form?.[key] ?? ""} disabled={!form} onChange={(e) => setForm((f) => f && { ...f, [key]: e.target.value })}
        className={`${INPUT} w-full`} />
    </label>
  );
  return (
    <Panel title="Support contact and targets" id="support-desk"
      note="Users see the WhatsApp and e-mail link in the Help centre while the market is open (holidays included). Leave a field empty to show none. The targets mark tickets late; empty means no target.">
      <form className="grid gap-space-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save(); }} data-support-settings>
        {field("whatsapp", "WhatsApp number, with the country code", { inputMode: "tel", placeholder: "+91…", autoComplete: "off" })}
        {field("email", "Support e-mail", { type: "email", placeholder: "support@…", autoComplete: "off" })}
        {field("first", "First answer within (hours)", { inputMode: "decimal", placeholder: "no target" })}
        {field("resolve", "Resolve within (hours)", { inputMode: "decimal", placeholder: "no target" })}
        <div className="flex flex-wrap items-center gap-space-2 sm:col-span-2">
          <button type="submit" className={BUTTON} disabled={!form || busy}>Save</button>
          {message && <span className={`text-t12 ${message.ok ? "text-up" : "text-down"}`} role={message.ok ? "status" : "alert"}>{message.text}</span>}
          {saved && !saved.whatsapp && !saved.email && <span className="text-t12 text-fg-muted">No contact set: users see none.</span>}
        </div>
      </form>
    </Panel>
  );
}
