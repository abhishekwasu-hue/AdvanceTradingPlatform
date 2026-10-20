import { AlarmClock, Bell, CheckCircle2, ChevronRight, Clock, MessageCircle, Satellite, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import { Badge, EmptyState, type Tone } from "../../components/primitives";
import { isSnoozed } from "../../proposals/snooze";
import type { AiAction, NotificationEntry, TelegramInboundStatus } from "../../types";
import { useCopilotData } from "../data";
import { useCopilotT } from "../i18n";
import { useDeepLinkTarget } from "../../utils/useDeepLinkTarget";
import { useLiveEvents } from "../../live/useLiveEvents";
import { signalsLinkFor, watchtowerSplit } from "../../signals/watchtower";
import { useUiV2 } from "../../ui/useFeatures";
import ProposalCard from "./ProposalCard";
import { Panel } from "./shared";

/**
 * Watchtower: what the monitoring agent proposed (pause a deployment, exit a position, reduce risk, review) as a
 * timeline - the trader approves, rejects or snoozes each one (PW5 card); a LIVE deployment or position asks for the
 * authenticator code first (server-enforced; the step-up dialog retries the approval). The agent never acts on its own
 * (ADR-0006). Snoozed proposals wait in their own folded list until they wake. Below: the latest alerts and whether
 * Telegram approvals are linked.
 */
const STATUS_TONE: Record<string, Tone> = { PROPOSED: "warn", APPROVED: "info", EXECUTED: "up", REJECTED: "neutral", EXPIRED: "neutral", FAILED: "down" };
const SEVERITY_TONE: Record<string, Tone> = { INFO: "info", WARNING: "warn", CRITICAL: "down", EMERGENCY: "down" };

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "-";
}

export default function WatchtowerTab() {
  const t = useCopilotT();
  const { pending: allPending, reloadActions } = useCopilotData();
  // UI2-3 (UI v2 §3 M7): with `ui_v2` on, entry approvals are decided in Signals (the same /ai/actions data); this tab
  // keeps the agent's other proposals (pause, exit, reduce risk, review) and links there. Flag off: unchanged.
  const v2 = useUiV2();
  const { entries, others: pending } = watchtowerSplit(allPending, v2);
  const [history, setHistory] = useState<AiAction[]>([]);
  const [alerts, setAlerts] = useState<NotificationEntry[] | null>(null);
  const [telegram, setTelegram] = useState<TelegramInboundStatus | null | "unavailable">(null);
  const [message, setMessage] = useState<string | null>(null);
  const [snoozedAll, setSnoozed] = useState<AiAction[]>([]);
  const snoozed = watchtowerSplit(snoozedAll, v2).others;
  const [now, setNow] = useState(() => new Date());

  // One read serves the timeline (decided proposals) and the snoozed list (PW5: hidden from the pending list).
  const loadHistory = () => {
    api.aiActions().then((all) => {
      const at = new Date();
      setHistory(all.filter((a) => a.status !== "PROPOSED").slice(0, 20));
      setSnoozed(all.filter((a) => isSnoozed(a, at)));
    }).catch(() => { setHistory([]); setSnoozed([]); });
  };
  useEffect(() => {
    loadHistory();
    api.listNotifications().then((n) => setAlerts(n.slice(0, 8))).catch(() => setAlerts([]));
    api.telegramInboundStatus().then(setTelegram).catch(() => setTelegram("unavailable"));
    const tick = window.setInterval(() => setNow(new Date()), 30_000);      // the "time left" line stays current
    return () => window.clearInterval(tick);
  }, []);

  const decided = (msg: string) => { setMessage(msg); reloadActions(); loadHistory(); };
  // PW4: approved or rejected on Telegram (or in another tab) -> this list follows within about 2 s (PW5: snoozes too).
  useLiveEvents(["ai_actions"], () => { reloadActions(); loadHistory(); });
  // PW3: a push or Telegram link opens /copilot/watchtower?action=<id> - that proposal is scrolled to and outlined
  // (a snoozed one opens the folded snoozed list).
  const target = useDeepLinkTarget("action", "proposal", pending.length + snoozed.length > 0);
  const targetSnoozed = target != null && snoozed.some((a) => a.id === target);

  return (
    <div className="space-y-4" data-testid="tab-panel-watchtower">
      <Panel title={t("watch.title", { count: pending.length })} icon={<Satellite size={15} />}>
        <p className="mb-3 text-xs text-fg-muted">{t("watch.intro")}</p>
        {message && <p className="mb-3 rounded-lg border border-ai/30 bg-ai/5 px-3 py-2 text-xs text-fg" role="status">{message}</p>}
        {v2 && (
          <Link to={signalsLinkFor(entries)} data-testid="watch-signals-link"
            className="mb-3 flex min-h-[44px] items-center gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-fg hover:bg-surface-3">
            <Zap size={15} aria-hidden className="shrink-0 text-brand" />
            <span className="min-w-0 flex-1">{t("watch.signalsNote", { count: entries.length })}</span>
            <span className="inline-flex shrink-0 items-center font-semibold text-brand">{t("watch.signalsLink")}<ChevronRight size={15} aria-hidden /></span>
          </Link>
        )}
        {pending.length === 0 ? (
          <EmptyState icon={<CheckCircle2 size={22} />} title={t("watch.emptyTitle")} body={t("watch.emptyBody")} />
        ) : <div className="space-y-3">{pending.map((a) => (
          <div key={a.id} id={`proposal-${a.id}`} className={target === a.id ? "rounded-2xl ring-2 ring-brand" : undefined}>
            <ProposalCard a={a} onDecided={decided} now={now} />
          </div>
        ))}</div>}
        {snoozed.length > 0 && (
          <details className="mt-3 rounded-xl border border-border px-3 py-2" open={targetSnoozed || undefined} data-testid="watch-snoozed">
            <summary className="flex min-h-[44px] cursor-pointer items-center gap-2 text-sm text-fg-muted hover:text-fg">
              <AlarmClock size={15} aria-hidden />{t("watch.snoozedList", { count: snoozed.length })}
            </summary>
            <div className="mt-2 space-y-3">{snoozed.map((a) => (
              <div key={a.id} id={`proposal-${a.id}`} className={target === a.id ? "rounded-2xl ring-2 ring-brand" : undefined}>
                <ProposalCard a={a} onDecided={decided} snoozed now={now} />
              </div>
            ))}</div>
          </details>
        )}
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
