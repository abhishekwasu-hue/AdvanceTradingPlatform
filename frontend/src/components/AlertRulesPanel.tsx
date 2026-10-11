/**
 * S3c (ADR-0022): manage alert rules and how they are delivered - the rule list (pause / resume, delivery log),
 * a new instrument rule (ScreenQL condition, validated by the server), the organisation's delivery policy (quiet
 * hours, hourly cap, grouping, end-of-day digest time), dead-lettered deliveries with retry, and the user's own
 * Telegram link and email opt-outs. A rule only notifies - it never places or changes an order.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import {
  blankToNull, describeRule, policyErrors, problemsFrom, reasonLabel, ruleErrors, TIMEFRAMES,
  type AlertEventRow, type AlertRule, type AlertRuleInput, type DeadLetter, type EmailOptOut, type NotificationPolicy, type TelegramLinkCode,
} from "../alerts/rules";
import { Badge, Button, Input, Select } from "./primitives";
import { Card } from "./ui";

const NEW_RULE: AlertRuleInput = { name: "", kind: "instrument", symbol: "", condition: "", base_tf: "5m", priority: "normal",
  cooldown_minutes: 60, mode: "instant", digest_every: "hourly" };

function errorText(e: unknown): string[] {
  if (e instanceof ApiError) {
    const problems = problemsFrom(e.body);
    return problems.length ? [e.message, ...problems] : [e.message];
  }
  return [String(e)];
}

function Errors({ lines }: { lines: string[] }) {
  if (!lines.length) return null;
  return <ul role="alert" className="text-xs text-down space-y-0.5">{lines.map((l) => <li key={l}>{l}</li>)}</ul>;
}

function RuleRow({ rule, onChanged }: { rule: AlertRule; onChanged: () => void }) {
  const [events, setEvents] = useState<AlertEventRow[] | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  async function toggle() {
    try {
      await api.setAlertRuleStatus(rule.id, rule.status === "active" ? "pause" : "resume");
      onChanged();
    } catch (e) { setErrors(errorText(e)); }
  }
  async function showLog() {
    if (events) { setEvents(null); return; }
    try { setEvents(await api.alertRuleEvents(rule.id)); } catch (e) { setErrors(errorText(e)); }
  }
  return (
    <div className="rounded border border-border px-3 py-2 space-y-1">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Badge tone={rule.status === "active" ? "up" : "neutral"}>{rule.status}</Badge>
          {rule.priority === "critical" && <Badge tone="down">critical</Badge>}
          <span className="text-sm font-medium text-fg truncate">{rule.name}</span>
        </div>
        <div className="flex gap-1 shrink-0">
          <Button size="sm" variant="ghost" onClick={showLog}>{events ? "Hide log" : "Delivery log"}</Button>
          <Button size="sm" variant="secondary" onClick={toggle}>{rule.status === "active" ? "Pause" : "Resume"}</Button>
        </div>
      </div>
      <div className="text-xs text-fg-muted font-tabular break-words">{describeRule(rule)}</div>
      <Errors lines={errors} />
      {events && (events.length === 0 ? <div className="text-xs text-fg-muted">No firings yet.</div> : (
        <table className="w-full text-xs">
          <thead><tr className="text-fg-muted text-left"><th className="py-1">Bar</th><th>Symbol</th><th>Status</th><th>Why</th></tr></thead>
          <tbody>
            {events.map((ev) => (
              <tr key={ev.id} className="border-t border-border">
                <td className="py-1 font-tabular">{new Date(ev.bar_time).toLocaleString()}</td>
                <td>{ev.symbol}</td>
                <td>{ev.status}</td>
                <td className="text-fg-muted">{reasonLabel(ev.reason)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ))}
    </div>
  );
}

function NewRuleForm({ onCreated }: { onCreated: () => void }) {
  const [form, setForm] = useState<AlertRuleInput>(NEW_RULE);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof AlertRuleInput>(k: K, v: AlertRuleInput[K]) => setForm((f) => ({ ...f, [k]: v }));
  async function submit() {
    const local = ruleErrors(form);
    setErrors(local);
    if (local.length) return;
    setBusy(true);
    try {
      await api.createAlertRule({ ...form, name: form.name.trim(), symbol: form.symbol.trim(), condition: form.condition.trim() });
      setForm(NEW_RULE);
      onCreated();
    } catch (e) { setErrors(errorText(e)); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-3">
        <Input label="Name" value={form.name} onChange={(e) => set("name", e.target.value)} maxLength={120} />
        <Input label="Symbol" value={form.symbol} onChange={(e) => set("symbol", e.target.value)} placeholder="TCS" />
        <Select label="Timeframe" value={form.base_tf} onChange={(v) => set("base_tf", v)} options={TIMEFRAMES.map((t) => ({ value: t, label: t }))} />
      </div>
      <Input label="Condition (ScreenQL)" value={form.condition} onChange={(e) => set("condition", e.target.value)}
        placeholder="close > SMA(close, 20) and volume > 2 * SMA(volume, 20)" hint="Checked on closed bars only. The server validates it before saving." />
      <div className="grid gap-2 sm:grid-cols-4">
        <Select label="Priority" value={form.priority} onChange={(v) => set("priority", v as AlertRuleInput["priority"])}
          options={[{ value: "critical", label: "Critical (ignores quiet hours)" }, { value: "normal", label: "Normal" }, { value: "low", label: "Low" }]} />
        <Input label="Cooldown (min)" type="number" min={0} max={10080} value={form.cooldown_minutes} onChange={(e) => set("cooldown_minutes", Number(e.target.value))} />
        <Select label="Delivery" value={form.mode} onChange={(v) => set("mode", v as AlertRuleInput["mode"])}
          options={[{ value: "instant", label: "Instant" }, { value: "digest", label: "Digest" }]} />
        {form.mode === "digest" && (
          <Select label="Digest every" value={form.digest_every} onChange={(v) => set("digest_every", v as AlertRuleInput["digest_every"])}
            options={[{ value: "hourly", label: "Hour" }, { value: "eod", label: "End of day" }]} />
        )}
      </div>
      <Errors lines={errors} />
      <Button onClick={submit} loading={busy}>Create rule</Button>
    </div>
  );
}

function PolicyForm() {
  const [policy, setPolicy] = useState<NotificationPolicy | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);
  useEffect(() => { api.getNotificationPolicy().then(setPolicy).catch((e) => setErrors(errorText(e))); }, []);
  if (!policy) return <Errors lines={errors} />;
  const set = <K extends keyof NotificationPolicy>(k: K, v: NotificationPolicy[K]) => { setSaved(false); setPolicy({ ...policy, [k]: v }); };
  async function save() {
    if (!policy) return;
    const local = policyErrors(policy);
    setErrors(local);
    if (local.length) return;
    try { setPolicy(await api.putNotificationPolicy(policy)); setSaved(true); } catch (e) { setErrors(errorText(e)); }
  }
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-3">
        <Input label="Timezone" value={policy.timezone} onChange={(e) => set("timezone", e.target.value)} hint="IANA name, e.g. Asia/Kolkata" />
        <Input label="Quiet from (HH:MM)" value={policy.quiet_start ?? ""} onChange={(e) => set("quiet_start", blankToNull(e.target.value))} placeholder="22:00" />
        <Input label="Quiet until (HH:MM)" value={policy.quiet_end ?? ""} onChange={(e) => set("quiet_end", blankToNull(e.target.value))} placeholder="07:00" />
        <Input label="Messages per hour" type="number" min={1} max={600} value={policy.max_per_hour} onChange={(e) => set("max_per_hour", Number(e.target.value))} />
        <Input label="Group a burst for (s)" type="number" min={0} max={600} value={policy.group_window_seconds} onChange={(e) => set("group_window_seconds", Number(e.target.value))} />
        <Input label="End-of-day digest (HH:MM)" value={policy.eod_digest_time} onChange={(e) => set("eod_digest_time", e.target.value)} />
      </div>
      <p className="text-xs text-fg-muted">Critical rules go out during quiet hours and above the hourly cap. Everything else is held and sent after.</p>
      <Errors lines={errors} />
      <div className="flex items-center gap-2"><Button onClick={save}>Save policy</Button>{saved && <span className="text-xs text-up">Saved.</span>}</div>
    </div>
  );
}

function DeadLetters() {
  const [rows, setRows] = useState<DeadLetter[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const load = useCallback(() => {
    api.listDeadLetters().then(setRows).catch((e) => {
      if (!(e instanceof ApiError && e.status === 403)) setErrors(errorText(e));        // viewers: the list is for traders
    });
  }, []);
  useEffect(load, [load]);
  async function retry(id: number) {
    try { await api.retryDelivery(id); load(); } catch (e) { setErrors(errorText(e)); }
  }
  return (
    <div className="space-y-2">
      <Errors lines={errors} />
      {rows.length === 0 ? <div className="text-xs text-fg-muted">Nothing failed. Deliveries that give up after every retry show here.</div> : rows.map((d) => (
        <div key={d.id} className="flex items-start justify-between gap-2 rounded border border-border px-3 py-2">
          <div className="min-w-0 text-xs">
            <div className="font-medium text-fg">{d.channel} · notification #{d.notification_id} · {d.attempts} attempt(s)</div>
            <div className="text-fg-muted">{reasonLabel(d.reason)}{d.last_error ? ` - ${d.last_error}` : ""}</div>
          </div>
          <Button size="sm" variant="secondary" onClick={() => retry(d.id)}>Retry</Button>
        </div>
      ))}
    </div>
  );
}

function PersonalChannels() {
  const [link, setLink] = useState<{ linked: boolean; linked_at: string | null } | null>(null);
  const [code, setCode] = useState<TelegramLinkCode | null>(null);
  const [optOuts, setOptOuts] = useState<EmailOptOut[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const load = useCallback(() => {
    api.telegramLinkStatus().then(setLink).catch((e) => setErrors(errorText(e)));
    api.listEmailOptOuts().then(setOptOuts).catch(() => setOptOuts([]));          // traders only; others just see none
  }, []);
  useEffect(load, [load]);
  async function makeCode() {
    try { setCode(await api.telegramLinkCode()); setErrors([]); } catch (e) { setErrors(errorText(e)); }
  }
  async function unlink() {
    try { await api.telegramUnlink(); setCode(null); load(); } catch (e) { setErrors(errorText(e)); }
  }
  async function resubscribe(id: number) {
    try { await api.removeEmailOptOut(id); load(); } catch (e) { setErrors(errorText(e)); }
  }
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <div className="text-sm text-fg">Your Telegram: {link?.linked ? <Badge tone="up">linked</Badge> : <Badge>not linked</Badge>}</div>
        <p className="text-xs text-fg-muted">A linked chat gets the alerts of rules you created. It cannot run commands or approve anything.</p>
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" onClick={makeCode}>{link?.linked ? "Link another chat" : "Get a link code"}</Button>
          {link?.linked && <Button size="sm" variant="ghost" onClick={unlink}>Unlink</Button>}
        </div>
        {code && (
          <div className="rounded border border-border bg-surface-2 px-3 py-2 text-xs space-y-1">
            <div>Send this to the organisation's bot in a <b>private</b> chat:</div>
            <code className="block font-tabular text-sm text-fg select-all">{code.command}</code>
            <div className="text-fg-muted">Works once, until {new Date(code.expires_at).toLocaleTimeString()}.</div>
          </div>
        )}
      </div>
      <div className="space-y-1">
        <div className="text-sm text-fg">Email addresses that unsubscribed from screen alerts</div>
        {optOuts.length === 0 ? <div className="text-xs text-fg-muted">None.</div> : optOuts.map((o) => (
          <div key={o.id} className="flex items-center justify-between text-xs">
            <span>{o.address}</span>
            <Button size="sm" variant="ghost" onClick={() => resubscribe(o.id)}>Re-subscribe</Button>
          </div>
        ))}
      </div>
      <Errors lines={errors} />
    </div>
  );
}

export function AlertRulesPanel() {
  const [rules, setRules] = useState<AlertRule[] | null>(null);
  const [off, setOff] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const load = useCallback(() => {
    api.listAlertRules().then((r) => { setRules(r); setOff(false); }).catch((e) => {
      if (e instanceof ApiError && e.status === 503) setOff(true);
      else setErrors(errorText(e));
    });
  }, []);
  useEffect(load, [load]);
  return (
    <div className="space-y-4">
      {off ? (
        <Card title="Alert rules"><p className="text-sm text-fg-muted">Alert rules are not switched on for this organisation yet (feature <code>screener_v2</code>).</p></Card>
      ) : (
        <>
          <Card title={`Alert rules${rules ? ` (${rules.length})` : ""}`}>
            <Errors lines={errors} />
            <div className="space-y-2">
              {rules?.length === 0 && <div className="text-xs text-fg-muted">No rules yet.</div>}
              {rules?.map((r) => <RuleRow key={r.id} rule={r} onChanged={load} />)}
            </div>
            <p className="mt-2 text-xs text-fg-muted">Alerts report that a rule's conditions matched. They are not recommendations and never place orders.</p>
          </Card>
          <Card title="New instrument rule"><NewRuleForm onCreated={load} /></Card>
          <Card title="Delivery policy"><PolicyForm /></Card>
        </>
      )}
      <Card title="Failed deliveries"><DeadLetters /></Card>
      <Card title="Your channels"><PersonalChannels /></Card>
    </div>
  );
}
