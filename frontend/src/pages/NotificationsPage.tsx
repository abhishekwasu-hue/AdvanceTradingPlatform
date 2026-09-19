import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card } from "../components/ui";
import type { NotificationEntry, NotificationSeverity } from "../types";

function severityClasses(severity: NotificationSeverity): string {
  switch (severity) {
    case "CRITICAL":
      return "border-danger/40 text-danger";
    case "WARNING":
      return "border-amber-500/40 text-amber-400";
    default:
      return "border-sky-500/40 text-sky-400";
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
        <h1 className="text-xl font-semibold text-slate-100">Notifications</h1>
        <Card>
          <p className="text-sm text-muted">Log in from the Account tab to see your account's notification feed.</p>
        </Card>
      </div>
    );
  }

  const unreadCount = notifications.filter((n) => !n.read).length;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Notifications</h1>
          <p className="text-sm text-muted">
            Entries, exits, rejections, broker disconnects, risk/daily-loss limit breaches,
            emergency exits, and system failures - shared across your account.
          </p>
        </div>
        {unreadCount > 0 && (
          <button
            onClick={markAllRead}
            className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1.5 text-xs shrink-0"
          >
            Mark all read ({unreadCount})
          </button>
        )}
      </div>

      {error && <div className="text-sm text-danger">{error}</div>}

      <Card title={`Feed (${notifications.length})`}>
        {notifications.length === 0 ? (
          <div className="text-sm text-muted py-4 text-center">No notifications yet.</div>
        ) : (
          <div className="space-y-2">
            {notifications.map((n) => (
              <div
                key={n.id}
                className={`rounded border px-3 py-2 ${n.read ? "border-border opacity-60" : "border-border bg-panel2/50"}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${severityClasses(n.severity)}`}>
                      {n.severity}
                    </span>
                    <span className="text-[10px] text-muted uppercase tracking-wide">{n.event_type}</span>
                  </div>
                  <span className="text-[11px] text-muted whitespace-nowrap">{new Date(n.created_at).toLocaleString()}</span>
                </div>
                <div className="mt-1 text-sm font-medium text-slate-200">{n.title}</div>
                {n.message && <div className="mt-0.5 text-xs text-muted">{n.message}</div>}
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
