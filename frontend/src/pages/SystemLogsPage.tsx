import { errorSentence } from "../api/errors";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import ExportCard from "../components/ExportCard";
import { Card } from "../components/ui";
import type { AuditLogEntry, LoginEvent } from "../types";
import { PageHeader, SkeletonRows } from "../components/primitives";
import { EmptyStateV2 } from "../dashboard/CardV2";
import { formatDateTimeIST } from "../ui/format";
import { Details } from "../ui/Details";

export default function SystemLogsPage() {
  const { user, loading: authLoading } = useAuth();
  // M63 I-7: null until the first answer - the lists show placeholder rows, never "No ... yet" before the read.
  const [logs, setLogs] = useState<AuditLogEntry[] | null>(null);
  const [logins, setLogins] = useState<LoginEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    api.listAuditLogs().then(setLogs).catch((e) => { setLogs([]); setError(errorSentence(e)); });
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

      <Card title={logins ? `Login history (${logins.length})` : "Login history"}>
        <Details id="system-logs.login-history" className="mt-0 mb-2"><p>
          Every attempt to log in as you, successful or not. Ten failures in fifteen minutes lock
          the account for fifteen minutes; a login from a device you have not used before raises
          a notification. Something you do not recognise? Log out everywhere and change your password.
        </p></Details>
        <LoginRows logins={logins} />
      </Card>

      <Card title={logs ? `Events (${logs.length})` : "Events"}>
        <EventRows logs={logs} />
      </Card>
    </div>
  );
}

/** M63 I-7: the login history - placeholder rows while it loads, one line when there is none. */
export function LoginRows({ logins }: { logins: LoginEvent[] | null }) {
  if (logins === null) return <SkeletonRows rows={3} label="Loading your login history" />;
  return logins.length === 0 ? (
    <EmptyStateV2 text="No login attempts yet." />
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
  );
}

/** M63 I-7: the account's events - placeholder rows while they load, one line when there is none. */
export function EventRows({ logs }: { logs: AuditLogEntry[] | null }) {
  if (logs === null) return <SkeletonRows rows={3} label="Loading your events" />;
  return logs.length === 0 ? (
    <EmptyStateV2 text="No events yet." />
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
  );
}
