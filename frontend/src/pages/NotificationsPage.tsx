import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card } from "../components/ui";
import type { NotificationEntry, NotificationSeverity, VisionReportsResponse } from "../types";
import { VisionReportView } from "../components/VisionReportPanel";
import VisionDailyReview from "../components/VisionDailyReview";
import { byNotification, single } from "../components/visionReport";
import { PageHeader, Tabs } from "../components/primitives";
import { AlertRulesPanel } from "../components/AlertRulesPanel";
import { useDeepLinkTarget } from "../utils/useDeepLinkTarget";
import { useLiveEvents } from "../live/useLiveEvents";
import { formatDateTimeIST } from "../ui/format";

function severityClasses(severity: NotificationSeverity): string {
  switch (severity) {
    case "CRITICAL":
      return "border-down/40 text-down";
    case "WARNING":
      return "border-warn/40 text-warn";
    default:
      return "border-info/40 text-info";
  }
}

export default function NotificationsPage() {
  const { user, loading: authLoading } = useAuth();
  const [notifications, setNotifications] = useState<NotificationEntry[]>([]);
  // PW3: a push opens /notifications?n=<id> - that notification is scrolled to and outlined.
  const target = useDeepLinkTarget("n", "notification", notifications.length > 0);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState("feed");
  // BYOK-V3: the caller's own Vision reports on their scan alerts and their daily review. Null while the flags are off
  // (the API answers 404) - then nothing about Vision is shown.
  const [scanVision, setScanVision] = useState<VisionReportsResponse | null>(null);
  const [dailyVision, setDailyVision] = useState<VisionReportsResponse | null>(null);

  function refresh() {
    if (!user) return;
    api.listNotifications().then(setNotifications).catch((e) => setError(String(e)));
    api.byokReportsBy({ kind: "scan_alert" }).then(setScanVision).catch(() => setScanVision(null));
    api.byokReportsBy({ kind: "daily_review" }).then(setDailyVision).catch(() => setDailyVision(null));
  }

  useEffect(refresh, [user]);
  useLiveEvents(["notifications"], refresh);                 // PW4: a new alert appears without a reload

  async function markRead(id: number) {
    try {
      await api.markNotificationRead(id);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function markAllRead() {
    try {
      await api.markAllNotificationsRead();
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <PageHeader title="Notifications" />
        <Card>
          <p className="text-sm text-fg-muted">Log in from the Account tab to see your account's notification feed.</p>
        </Card>
      </div>
    );
  }

  const unreadCount = notifications.filter((n) => !n.read).length;
  // Review LOW-2: byNotification is empty while the in-app channel is off (Telegram may still carry the report).
  const scanReports = byNotification(scanVision);
  const feed = (
      <Card className="mt-3">
        {notifications.length === 0 ? (
          <div className="text-sm text-fg-muted py-4 text-center">No notifications yet.</div>
        ) : (
          <div className="space-y-2">
            {notifications.map((n) => (
              <div
                key={n.id}
                id={`notification-${n.id}`}
                className={`rounded border px-3 py-2 ${n.read ? "border-border opacity-80" : "border-border bg-surface-2/50"} ${target === n.id ? "ring-2 ring-brand" : ""}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span className={`rounded border px-1.5 py-0.5 text-xs font-semibold uppercase ${severityClasses(n.severity)}`}>
                      {n.severity}
                    </span>
                    <span className="text-xs text-fg-muted uppercase tracking-wide">{n.event_type}</span>
                  </div>
                  <span className="text-xs text-fg-muted whitespace-nowrap">{formatDateTimeIST(n.created_at)}</span>
                </div>
                <div className="mt-1 text-sm font-medium text-fg">{n.title}</div>
                {n.message && <div className="mt-0.5 text-xs text-fg-muted">{n.message}</div>}
                {scanVision && scanVision.in_app && scanReports.has(n.id) && (
                  <div className="mt-1.5"><VisionReportView resp={single(scanVision, scanReports.get(n.id)!)} onChanged={refresh} /></div>
                )}
                {!n.read && (
                  <button
                    onClick={() => markRead(n.id)}
                    className="mt-1.5 text-xs text-brand hover:underline"
                  >
                    Mark read
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <PageHeader title="Notifications" description="Entries, exits, rejections, broker disconnects, risk/daily-loss limit breaches, emergency exits, and system failures - shared across your account." />
        {unreadCount > 0 && (
          <button
            onClick={markAllRead}
            className="rounded border border-border hover:bg-surface-2 text-fg px-3 py-1.5 text-xs shrink-0"
          >
            Mark all read ({unreadCount})
          </button>
        )}
      </div>

      {error && <div className="text-sm text-down">{error}</div>}

      <Tabs value={tab} onChange={setTab} items={[
        { value: "feed", label: `Feed (${notifications.length})`, content: feed },
        { value: "rules", label: "Rules & delivery", content: <div className="pt-3"><AlertRulesPanel /></div> },
        ...(dailyVision && dailyVision.in_app ? [{ value: "vision-daily", label: "Vision daily review",
                                                   content: <Card className="mt-3"><VisionDailyReview resp={dailyVision} onChanged={refresh} /></Card> }] : []),
      ]} />
    </div>
  );
}
