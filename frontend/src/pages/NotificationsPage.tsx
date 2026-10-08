import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card } from "../components/ui";
import type { NotificationEntry, NotificationSeverity } from "../types";
import { PageHeader } from "../components/primitives";

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
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    if (!user) return;
    api.listNotifications().then(setNotifications).catch((e) => setError(String(e)));
  }

  useEffect(refresh, [user]);

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

      <Card title={`Feed (${notifications.length})`}>
        {notifications.length === 0 ? (
          <div className="text-sm text-fg-muted py-4 text-center">No notifications yet.</div>
        ) : (
          <div className="space-y-2">
            {notifications.map((n) => (
              <div
                key={n.id}
                className={`rounded border px-3 py-2 ${n.read ? "border-border opacity-80" : "border-border bg-surface-2/50"}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${severityClasses(n.severity)}`}>
                      {n.severity}
                    </span>
                    <span className="text-[10px] text-fg-muted uppercase tracking-wide">{n.event_type}</span>
                  </div>
                  <span className="text-[11px] text-fg-muted whitespace-nowrap">{new Date(n.created_at).toLocaleString()}</span>
                </div>
                <div className="mt-1 text-sm font-medium text-fg">{n.title}</div>
                {n.message && <div className="mt-0.5 text-xs text-fg-muted">{n.message}</div>}
                {!n.read && (
                  <button
                    onClick={() => markRead(n.id)}
                    className="mt-1.5 text-[11px] text-brand hover:underline"
                  >
                    Mark read
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
