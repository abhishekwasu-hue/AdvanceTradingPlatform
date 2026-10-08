import { Bell, CheckCircle2, Clock, MessageCircle, Satellite, ShieldAlert, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { Badge, Button, Dialog, EmptyState, fieldClass, type Tone } from "../../components/primitives";
import StepUpDialog, { isStepUpError } from "../../components/StepUpDialog";
import type { AiAction, NotificationEntry, TelegramInboundStatus } from "../../types";
import { friendlyError } from "../aiTask";
import TiltCard from "../components/TiltCard";
import { useCopilotData } from "../data";
import { useCopilotT } from "../i18n";
import { Panel } from "./shared";

/**
 * Watchtower: what the monitoring agent proposed (pause a deployment, exit a position, reduce risk, review) as a
 * timeline - the trader approves or rejects each one; a LIVE deployment or position asks for the authenticator code
 * first (server-enforced; the step-up dialog retries the approval). The agent never acts on its own (ADR-0006).
 * Below: the latest alerts and whether Telegram approvals are linked.
 */
const STATUS_TONE: Record<string, Tone> = { PROPOSED: "warn", APPROVED: "info", EXECUTED: "up", REJECTED: "neutral", EXPIRED: "neutral", FAILED: "down" };
const SEVERITY_TONE: Record<string, Tone> = { INFO: "info", WARNING: "warn", CRITICAL: "down", EMERGENCY: "down" };

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "-";
}

function ProposalCard({ a, onDecided }: { a: AiAction; onDecided: (msg: string) => void }) {
  const t = useCopilotT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stepUp, setStepUp] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [note, setNote] = useState("");

  async function approve() {
    setBusy(true); setError(null);
    try { await api.aiApproveAction(a.id); onDecided(t("watch.approved")); }
    catch (e) { if (isStepUpError(e)) setStepUp(String(e)); else setError(friendlyError(e, t)); }
    finally { setBusy(false); }
  }
  async function reject() {
    setBusy(true); setError(null);
    try { await api.aiRejectAction(a.id, note.trim() || undefined); setRejectOpen(false); onDecided(t("watch.rejected")); }
    catch (e) { setError(friendlyError(e, t)); }
    finally { setBusy(false); }
  }

  return (
    <TiltCard tilt={false} glow="ai" className="space-y-2" data-testid="watch-proposal">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <ShieldAlert size={15} className="text-warn" aria-hidden />
        <b className="text-fg">{t(`watch.action.${a.action}`)}</b>
        <span className="text-xs text-fg-muted">{t("watch.meta", { rule: a.rule, deployment: a.deployment_id ?? "-" })}{a.trade_id ? ` · ${t("watch.position", { id: a.trade_id })}` : ""}</span>
        <Badge className="ml-auto" tone="warn">{t("watch.status.PROPOSED")}</Badge>
      </div>
      <p className="whitespace-pre-wrap text-sm text-fg">{a.reason}</p>
      <details className="text-xs text-fg-muted">
        <summary className="cursor-pointer hover:text-fg">{t("watch.evidence")}</summary>
        <pre className="mt-1 overflow-x-auto rounded-lg bg-surface-2 p-2 font-mono text-[11px] text-fg">{JSON.stringify(a.evidence, null, 2)}</pre>
      </details>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" icon={<CheckCircle2 size={13} />} loading={busy && !rejectOpen} disabled={busy} onClick={() => void approve()}>{t("watch.approve")}</Button>
        <Button size="sm" variant="danger" icon={<XCircle size={13} />} disabled={busy} onClick={() => setRejectOpen(true)}>{t("watch.reject")}</Button>
        <span className="text-[11px] text-fg-muted"><Clock size={11} className="mr-1 inline" />{t("watch.expires", { at: when(a.expires_at) })}</span>
      </div>
      <p className="text-[11px] text-fg-muted">{t("watch.liveNote")}</p>
      {error && <p className="text-xs text-down" role="alert">{error}</p>}
      {stepUp && <StepUpDialog reason={stepUp} onCancel={() => setStepUp(null)} onVerified={() => { setStepUp(null); void approve(); }} />}
      <Dialog open={rejectOpen} onOpenChange={setRejectOpen} title={t("watch.rejectTitle")} description={t("watch.rejectHint")}
              footer={<><Button onClick={() => setRejectOpen(false)}>{t("states.cancel")}</Button><Button variant="danger" loading={busy} onClick={() => void reject()}>{t("watch.reject")}</Button></>}>
        <label htmlFor={`reject-${a.id}`} className="mb-1 block text-xs text-fg-muted">{t("watch.rejectNote")}</label>
        <textarea id={`reject-${a.id}`} rows={3} className={`${fieldClass} py-2`} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
      </Dialog>
    </TiltCard>
  );
}

export default function WatchtowerTab() {
  const t = useCopilotT();
  const { pending, reloadActions } = useCopilotData();
  const [history, setHistory] = useState<AiAction[]>([]);
  const [alerts, setAlerts] = useState<NotificationEntry[] | null>(null);
  const [telegram, setTelegram] = useState<TelegramInboundStatus | null | "unavailable">(null);
  const [message, setMessage] = useState<string | null>(null);

  const loadHistory = () => { api.aiActions().then((all) => setHistory(all.filter((a) => a.status !== "PROPOSED").slice(0, 20))).catch(() => setHistory([])); };
  useEffect(() => {
    loadHistory();
    api.listNotifications().then((n) => setAlerts(n.slice(0, 8))).catch(() => setAlerts([]));
    api.telegramInboundStatus().then(setTelegram).catch(() => setTelegram("unavailable"));
  }, []);

  const decided = (msg: string) => { setMessage(msg); reloadActions(); loadHistory(); };

  return (
    <div className="space-y-4" data-testid="tab-panel-watchtower">
      <Panel title={t("watch.title", { count: pending.length })} icon={<Satellite size={15} />}>
        <p className="mb-3 text-xs text-fg-muted">{t("watch.intro")}</p>
        {message && <p className="mb-3 rounded-lg border border-ai/30 bg-ai/5 px-3 py-2 text-xs text-fg" role="status">{message}</p>}
        {pending.length === 0 ? (
          <EmptyState icon={<CheckCircle2 size={22} />} title={t("watch.emptyTitle")} body={t("watch.emptyBody")} />
        ) : <div className="space-y-3">{pending.map((a) => <ProposalCard key={a.id} a={a} onDecided={decided} />)}</div>}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title={t("watch.timeline")} icon={<Clock size={15} />} className="lg:col-span-2" testId="watch-timeline">
          {history.length === 0 ? <p className="text-sm text-fg-muted">{t("watch.timelineEmpty")}</p> : (
            <ol className="relative space-y-3 border-l border-border pl-4">
              {history.map((a) => (
                <li key={a.id} className="relative">
                  <span className={`absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full ring-4 ring-surface-1 ${a.status === "EXECUTED" ? "bg-up" : a.status === "FAILED" ? "bg-down" : "bg-fg-muted"}`} aria-hidden />
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="text-fg">{t(`watch.action.${a.action}`)}</span>
                    <Badge tone={STATUS_TONE[a.status] ?? "neutral"}>{t(`watch.status.${a.status}`)}</Badge>
                    <span className="text-xs text-fg-muted">{when(a.created_at)} · {t("watch.ruleShort", { rule: a.rule })}</span>
                  </div>
                  {(a.result || a.decision_note) && <p className="text-xs text-fg-muted">{a.result ?? a.decision_note}</p>}
                </li>
              ))}
            </ol>
          )}
        </Panel>

        <div className="space-y-4">
          <Panel title={t("watch.alerts")} icon={<Bell size={15} />} testId="watch-alerts">
            {alerts == null ? <div className="copilot-skeleton copilot-shimmer h-16" /> : alerts.length === 0 ? <p className="text-sm text-fg-muted">{t("watch.alertsEmpty")}</p> : (
              <ul className="space-y-2 text-xs">
                {alerts.map((n) => (
                  <li key={n.id} className="flex gap-2">
                    <Badge tone={SEVERITY_TONE[n.severity] ?? "neutral"}>{t(`watch.severity.${n.severity}`)}</Badge>
                    <div className="min-w-0">
                      <div className={`truncate ${n.read ? "text-fg-muted" : "font-semibold text-fg"}`}>{n.title}</div>
                      <div className="text-fg-muted">{when(n.created_at)}</div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title={t("watch.telegram")} icon={<MessageCircle size={15} />} testId="watch-telegram">
            {telegram == null ? <div className="copilot-skeleton copilot-shimmer h-10" /> : telegram === "unavailable" ? <p className="text-sm text-fg-muted">{t("watch.tgUnavailable")}</p> : (
              <div className="space-y-1.5 text-sm">
                <div className="flex items-center gap-2"><span className="text-fg-muted">{t("watch.tgBot")}</span><Badge className="ml-auto" tone={telegram.configured ? "up" : "neutral"}>{telegram.configured ? t("watch.linked") : t("watch.notLinked")}</Badge></div>
                <div className="flex items-center gap-2"><span className="text-fg-muted">{t("watch.tgApprovals")}</span><Badge className="ml-auto" tone={telegram.inbound_enabled ? "up" : "neutral"}>{telegram.inbound_enabled ? t("watch.on") : t("watch.off")}</Badge></div>
                <div className="flex items-center gap-2"><span className="text-fg-muted">{t("watch.tgApprovers")}</span><span className="ml-auto font-tabular text-fg">{telegram.approvers?.length ?? 0}</span></div>
                <p className="text-[11px] text-fg-muted">{t("watch.tgHint")}</p>
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
