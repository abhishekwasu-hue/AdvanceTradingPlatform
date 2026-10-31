import { errorSentence } from "../api/errors";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import ExportCard from "../components/ExportCard";
import { Card } from "../components/ui";
import type { AuditLogEntry, LoginEvent } from "../types";
import { PageHeader } from "../components/primitives";
import { formatDateTimeIST } from "../ui/format";
import { Details } from "../ui/Details";

export default function SystemLogsPage() {
  const { user, loading: authLoading } = useAuth();
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [logins, setLogins] = useState<LoginEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    api.listAuditLogs().then(setLogs).catch((e) => setError(errorSentence(e)));
    api.loginHistory().then(setLogins).catch(() => setLogins([]));
  }, [user]);

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <PageHeader title="System Logs" />
        <Card>
          <p className="text-sm text-fg-muted">Log in from the Account tab to see your account's audit trail.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader title="System Logs" description="Your own security-relevant event history - register/login, broker credentials stored/deleted, broker authentication attempts. Private to your account, not a global admin view." />

      {error && <div className="text-sm text-down">{error}</div>}

      {user.role === "OWNER" && <ExportCard scope="tenant" />}

      <Card title={`Login history (${logins.length})`}>
        <Details id="system-logs.login-history" className="mt-0 mb-2"><p>
          Every attempt to log in as you, successful or not. Ten failures in fifteen minutes lock
          the account for fifteen minutes; a login from a device you have not used before raises
          a notification. Something you do not recognise? Log out everywhere and change your password.
        </p></Details>
        {logins.length === 0 ? (
          <div className="text-sm text-fg-muted py-2">No attempts recorded yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-fg-muted uppercase text-xs tracking-wide">
                <tr className="text-left"><th className="py-1 pr-3">Time</th><th className="py-1 pr-3">Result</th><th className="py-1 pr-3">IP</th><th className="py-1 pr-3">Device</th></tr>
              </thead>
              <tbody>
                {logins.map((l) => (
                  <tr key={l.id} className="border-t border-border">
                    <td className="py-1 pr-3 text-fg-muted whitespace-nowrap">{formatDateTimeIST(l.created_at)}</td>
                    <td className={`py-1 pr-3 font-semibold ${l.success ? "text-up" : "text-down"}`}>{l.success ? `ok (${l.reason})` : `failed: ${l.reason}`}</td>
                    <td className="py-1 pr-3 text-fg-muted">{l.ip_address ?? "-"}</td>
                    <td className="py-1 pr-3 text-fg-muted truncate max-w-[20rem]" title={l.user_agent ?? ""}>{l.user_agent ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title={`Events (${logs.length})`}>
        {logs.length === 0 ? (
          <div className="text-sm text-fg-muted py-4 text-center">No events yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-fg-muted uppercase text-xs tracking-wide">
                <tr className="text-left">
                  <th className="py-1 pr-3">Time</th>
                  <th className="py-1 pr-3">Event</th>
                  <th className="py-1 pr-3">Detail</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id} className="border-t border-border">
                    <td className="py-1 pr-3 text-fg-muted whitespace-nowrap">{formatDateTimeIST(log.created_at)}</td>
                    <td className="py-1 pr-3 font-medium text-fg">{log.event}</td>
                    <td className="py-1 pr-3 text-fg-muted">{log.detail || "-"}</td>
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
