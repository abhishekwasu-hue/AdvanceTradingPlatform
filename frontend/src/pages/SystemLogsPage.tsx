import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import ExportCard from "../components/ExportCard";
import { Card } from "../components/ui";
import type { AuditLogEntry, LoginEvent } from "../types";

export default function SystemLogsPage() {
  const { user, loading: authLoading } = useAuth();
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [logins, setLogins] = useState<LoginEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    api.listAuditLogs().then(setLogs).catch((e) => setError(String(e)));
    api.loginHistory().then(setLogins).catch(() => setLogins([]));
  }, [user]);

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-extrabold text-sky-400">System Logs</h1>
        <Card>
          <p className="text-sm text-muted">Log in from the Account tab to see your account's audit trail.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-sky-400">System Logs</h1>
        <p className="text-sm font-semibold text-sky-400/60">
          Your own security-relevant event history - register/login, broker credentials
          stored/deleted, broker authentication attempts. Private to your account, not a global
          admin view.
        </p>
      </div>

      {error && <div className="text-sm text-danger">{error}</div>}

      {user.role === "OWNER" && <ExportCard scope="tenant" />}

      <Card title={`Login history (${logins.length})`}>
        <p className="text-xs text-muted mb-2">
          Every attempt to log in as you, successful or not. Ten failures in fifteen minutes lock
          the account for fifteen minutes; a login from a device you have not used before raises
          a notification. Something you do not recognise? Log out everywhere and change your password.
        </p>
        {logins.length === 0 ? (
          <div className="text-sm text-muted py-2">No attempts recorded yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted uppercase text-[10px] tracking-wide">
                <tr className="text-left"><th className="py-1 pr-3">Time</th><th className="py-1 pr-3">Result</th><th className="py-1 pr-3">IP</th><th className="py-1 pr-3">Device</th></tr>
              </thead>
              <tbody>
                {logins.map((l) => (
                  <tr key={l.id} className="border-t border-border">
                    <td className="py-1 pr-3 text-muted whitespace-nowrap">{new Date(l.created_at).toLocaleString()}</td>
                    <td className={`py-1 pr-3 font-semibold ${l.success ? "text-accent" : "text-danger"}`}>{l.success ? `ok (${l.reason})` : `failed: ${l.reason}`}</td>
                    <td className="py-1 pr-3 text-slate-300">{l.ip_address ?? "-"}</td>
                    <td className="py-1 pr-3 text-muted truncate max-w-[20rem]" title={l.user_agent ?? ""}>{l.user_agent ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

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
