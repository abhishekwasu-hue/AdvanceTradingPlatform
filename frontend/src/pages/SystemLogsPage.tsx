import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card } from "../components/ui";
import type { AuditLogEntry } from "../types";

export default function SystemLogsPage() {
  const { user, loading: authLoading } = useAuth();
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    api.listAuditLogs().then(setLogs).catch((e) => setError(String(e)));
  }, [user]);

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold text-slate-100">System Logs</h1>
        <Card>
          <p className="text-sm text-muted">Log in from the Account tab to see your account's audit trail.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">System Logs</h1>
        <p className="text-sm text-muted">
          Your own security-relevant event history - register/login, broker credentials
          stored/deleted, broker authentication attempts. Private to your account, not a global
          admin view.
        </p>
      </div>

      {error && <div className="text-sm text-danger">{error}</div>}

      <Card title={`Events (${logs.length})`}>
        {logs.length === 0 ? (
          <div className="text-sm text-muted py-4 text-center">No events yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted uppercase text-[10px] tracking-wide">
                <tr className="text-left">
                  <th className="py-1 pr-3">Time</th>
                  <th className="py-1 pr-3">Event</th>
                  <th className="py-1 pr-3">Detail</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id} className="border-t border-border">
                    <td className="py-1 pr-3 text-muted whitespace-nowrap">{new Date(log.created_at).toLocaleString()}</td>
                    <td className="py-1 pr-3 font-medium text-slate-200">{log.event}</td>
                    <td className="py-1 pr-3 text-muted">{log.detail || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
