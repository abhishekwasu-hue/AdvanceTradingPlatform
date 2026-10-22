import { AlarmClock, CheckCircle2, Clock, ExternalLink, ShieldAlert, XCircle } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/client";
import { Badge, Button, Dialog, fieldClass } from "../../components/primitives";
import StepUpDialog, { isStepUpError } from "../../components/StepUpDialog";
import VisionReportPanel from "../../components/VisionReportPanel";
import RiskCheckPanel from "../../proposals/RiskCheckPanel";
import { brokerAppLink, openBrokerAppLink } from "../../proposals/brokerLinks";
import { evidenceSummary, snoozeChoices, timeLeft } from "../../proposals/snooze";
import type { AiAction } from "../../types";
import { friendlyError } from "../aiTask";
import TiltCard from "../components/TiltCard";
import { useCopilotT } from "../i18n";

/**
 * PW5: one proposal, phone-first - what the agent proposes and why, the evidence in a line, the time left, and three
 * big actions: Approve (PAPER; a LIVE deployment or position keeps today's flow, the server asks for the authenticator
 * and the step-up dialog retries), Reject (optional note) and Snooze (15 min / 1 h / 09:00 IST; never past the expiry).
 * An exit proposal may also offer "Trade in your broker app" (flag broker_app_links): the broker's own page opens with
 * the order pre-filled and the user confirms there - ATP places no order. The agent never acts on its own (ADR-0006).
 */
const ACTION_BUTTON = "min-h-[44px] w-full px-2 text-sm";

/** Z3: a target reached on an approval-only position reads "Exit now?"; everything else by its action. */
export function proposalTitle(a: Pick<AiAction, "action" | "rule">, t: (key: string) => string): string {
  if (a.action === "EXIT_POSITION" && a.rule.startsWith("TARGET_")) return t("watch.exitNow");
  return t(`watch.action.${a.action}`);
}

function clock(d: Date): string {
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
}

export default function ProposalCard({ a, onDecided, snoozed = false, now = new Date() }: {
  a: AiAction; onDecided: (msg: string) => void; snoozed?: boolean; now?: Date;
}) {
  const t = useCopilotT();
  const [busy, setBusy] = useState<"approve" | "reject" | "snooze" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stepUp, setStepUp] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const [note, setNote] = useState("");

  const paper = a.mode === "PAPER";
  const left = timeLeft(a.expires_at, now);
  const summary = evidenceSummary(a.evidence);
  const link = brokerAppLink(a);
  const choices = snoozeChoices(now, a.expires_at);

  async function approve() {
    setBusy("approve"); setError(null);
    try { await api.aiApproveAction(a.id); onDecided(t("watch.approved")); }
    catch (e) { if (isStepUpError(e)) setStepUp(String(e)); else setError(friendlyError(e, t)); }
    finally { setBusy(null); }
  }
  async function reject() {
    setBusy("reject"); setError(null);
    try { await api.aiRejectAction(a.id, note.trim() || undefined); setRejectOpen(false); onDecided(t("watch.rejected")); }
    catch (e) { setError(friendlyError(e, t)); }
    finally { setBusy(null); }
  }
  async function snooze(body: { minutes: number } | { until: string }) {
    setBusy("snooze"); setError(null);
    try {
      const out = await api.aiSnoozeAction(a.id, body);
      setSnoozeOpen(false);
      onDecided(t("watch.snoozed", { at: out.snoozed_until ? clock(new Date(out.snoozed_until)) : "-" }));
    } catch (e) { setError(friendlyError(e, t)); }
    finally { setBusy(null); }
  }

  return (
    <TiltCard tilt={false} glow="ai" className="space-y-3" data-testid={snoozed ? "watch-snoozed-proposal" : "watch-proposal"}>
      <div className="flex items-start gap-2">
        <ShieldAlert size={18} className="mt-0.5 shrink-0 text-warn" aria-hidden />
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-semibold leading-snug text-fg">{proposalTitle(a, t)}</h3>
          <p className="text-xs text-fg-muted">{t("watch.meta", { rule: a.rule, deployment: a.deployment_id ?? "-" })}{a.trade_id ? ` · ${t("watch.position", { id: a.trade_id })}` : ""}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {a.mode && <Badge tone={a.mode === "LIVE" ? "down" : "info"}>{a.mode}</Badge>}
          <Badge tone="warn">{snoozed && a.snoozed_until ? t("watch.snoozedUntil", { at: clock(new Date(a.snoozed_until)) }) : t("watch.status.PROPOSED")}</Badge>
        </div>
      </div>

      <p className="whitespace-pre-wrap text-sm text-fg">{a.reason}</p>

      {summary.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label={t("watch.evidence")} data-testid="watch-evidence-summary">
          {summary.map((s) => <li key={s} className="rounded-md bg-surface-2 px-2 py-1 font-tabular text-xs text-fg">{s}</li>)}
        </ul>
      )}
      <details className="text-xs text-fg-muted">
        <summary className="cursor-pointer py-1 hover:text-fg">{t("watch.evidenceAll")}</summary>
        <pre className="mt-1 max-w-full overflow-x-auto rounded-lg bg-surface-2 p-2 font-mono text-xs text-fg">{JSON.stringify(a.evidence, null, 2)}</pre>
      </details>
      <RiskCheckPanel a={a} />
      <VisionReportPanel actionId={a.id} />

      <p className={`flex items-center gap-1.5 text-xs ${left && left.hours === 0 ? "text-warn" : "text-fg-muted"}`} data-testid="watch-time-left">
        <Clock size={13} aria-hidden />
        {left ? t("watch.timeLeft", { hours: left.hours, minutes: left.minutes }) : t("watch.expiredNow")}
      </p>

      <div className="grid grid-cols-2 gap-2 min-[400px]:grid-cols-3" role="group" aria-label={t("watch.decide")}>
        <Button variant="primary" className={`${ACTION_BUTTON} col-span-2 min-[400px]:col-span-1`} icon={<CheckCircle2 size={16} />} loading={busy === "approve"} disabled={busy != null}
                onClick={() => void approve()}>{paper ? t("watch.approvePaper") : t("watch.approve")}</Button>
        <Button variant="danger" className={ACTION_BUTTON} icon={<XCircle size={16} />} disabled={busy != null} onClick={() => setRejectOpen(true)}>{t("watch.reject")}</Button>
        <Button className={ACTION_BUTTON} icon={<AlarmClock size={16} />} loading={busy === "snooze"} disabled={busy != null} aria-expanded={snoozeOpen}
                aria-controls={`snooze-${a.id}`} onClick={() => setSnoozeOpen((o) => !o)}>{t("watch.snooze")}</Button>
      </div>
      {snoozeOpen && (
        <div id={`snooze-${a.id}`} className="space-y-2 rounded-lg border border-border bg-surface-2 p-2" data-testid="watch-snooze-menu">
          <div className="grid grid-cols-1 gap-2 min-[380px]:grid-cols-3">
            {choices.map((c) => (
              <Button key={c.key} className={ACTION_BUTTON} disabled={busy != null} onClick={() => void snooze(c.body)}>
                {t(`watch.snoozeFor.${c.key === "0900" ? (c.tomorrow ? "0900tomorrow" : "0900today") : c.key}`)}
              </Button>
            ))}
          </div>
          {choices.some((c) => c.cutAtExpiry) && <p className="text-xs text-fg-muted">{t("watch.snoozeCut")}</p>}
          <p className="text-xs text-fg-muted">{t("watch.snoozeHint")}</p>
        </div>
      )}

      {link && (
        <div className="space-y-1" data-testid="watch-broker-link">
          <Button className={`${ACTION_BUTTON} w-full`} icon={<ExternalLink size={16} />} onClick={() => openBrokerAppLink(link)}>
            {t("watch.brokerApp", { broker: link.brokerLabel })}
          </Button>
          <p className="text-xs text-fg-muted">{t("watch.brokerAppHint", { order: link.summary, broker: link.brokerLabel })}</p>
        </div>
      )}

      {a.action === "ENTRY" && <p className="text-xs text-fg-muted" data-testid="watch-entry-note">{t("watch.entryNote")}</p>}
      {!paper && <p className="text-xs text-fg-muted">{t("watch.liveNote")}</p>}
      {error && <p className="text-xs text-down" role="alert">{error}</p>}
      {stepUp && <StepUpDialog reason={stepUp} onCancel={() => setStepUp(null)} onVerified={() => { setStepUp(null); void approve(); }} />}
      <Dialog open={rejectOpen} onOpenChange={setRejectOpen} title={t("watch.rejectTitle")} description={t("watch.rejectHint")}
              footer={<><Button onClick={() => setRejectOpen(false)}>{t("states.cancel")}</Button><Button variant="danger" loading={busy === "reject"} onClick={() => void reject()}>{t("watch.reject")}</Button></>}>
        <label htmlFor={`reject-${a.id}`} className="mb-1 block text-xs text-fg-muted">{t("watch.rejectNote")}</label>
        <textarea id={`reject-${a.id}`} rows={3} className={`${fieldClass} py-2`} value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} />
      </Dialog>
    </TiltCard>
  );
}
