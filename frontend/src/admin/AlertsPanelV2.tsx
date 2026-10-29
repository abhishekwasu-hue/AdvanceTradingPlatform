import { BellRing, Mail, MessageCircle, RefreshCw, Send, ShieldCheck, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { friendlyError } from "../api/client";
import { BUTTON, Panel, Pill } from "./AdminSectionsV2";
import { ago } from "./consoleApi";
import { opsApi, type OpsAlerts, type OpsState } from "./opsApi";
import { SkeletonRows } from "../components/primitives";

/**
 * Production readiness A2: Admin > System > Alerts. The same checks the server's schedule (`atp.sh ops-check`, every
 * 5 minutes) judges and messages the operator about - backups, disk, the certificate, the trading worker, the data
 * feed, errors, broker logins, database size, user alert delivery and failed sign-ins - with where the messages go
 * (the operator's Telegram chat, e-mail) and the error tracker's state. Problems first; "Check now" runs the checks
 * once from here (the same dedup, so nothing is sent twice); "Send a test" proves the channels.
 */

const TONE: Record<OpsState, "ok" | "warn" | "bad" | "off"> = { ok: "ok", warn: "warn", fail: "bad", unknown: "off" };
const WORD: Record<OpsState, string> = { ok: "OK", warn: "Warning", fail: "Failing", unknown: "Not known" };
const ORDER: Record<OpsState, number> = { fail: 0, warn: 1, unknown: 2, ok: 3 };

export function sortChecks<T extends { state: OpsState }>(checks: T[]): T[] {
  return checks.map((c, i) => ({ c, i })).sort((a, b) => ORDER[a.c.state] - ORDER[b.c.state] || a.i - b.i).map(({ c }) => c);
}

export function summaryLine(data: OpsAlerts): string {
  const fail = data.checks.filter((c) => c.state === "fail").length;
  const warn = data.checks.filter((c) => c.state === "warn").length;
  if (!data.checked_at) return "No check has run yet.";
  if (fail) return `${fail} failing${warn ? `, ${warn} warning${warn === 1 ? "" : "s"}` : ""}`;
  if (warn) return `${warn} warning${warn === 1 ? "" : "s"}, nothing failing`;
  return "Everything checked is OK";
}

export function AlertsPanelView({ data, busy, note, onRun, onTest, onLink, onUnlink }: {
  data: OpsAlerts; busy: boolean; note: string | null;
  onRun: () => void; onTest: () => void; onLink: () => void; onUnlink: () => void;
}) {
  const tg = data.channels.telegram;
  const email = data.channels.email;
  const anyChannel = tg.connected || (email.available && email.recipients > 0);
  return (
    <Panel title="Alerts" id="ops-alerts"
      note={data.checked_at ? `${summaryLine(data)} · checked ${ago(data.checked_at)}${data.source === "admin" ? " (from here)" : ""}` : summaryLine(data)}>
      {data.schedule.late && (
        <p role="status" data-ops-late className="mb-space-3 flex items-start gap-space-2 rounded-control bg-warn-tint px-space-3 py-space-2 text-t14 text-fg">
          <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" />
          <span>
            {data.schedule.server_reported_at
              ? `The server's scheduled check last reported ${ago(data.schedule.server_reported_at)}.`
              : "The server's scheduled check has not reported yet."}{" "}
            Backups, disk and the certificate are not being watched until it does. The next deploy installs the schedule.
          </span>
        </p>
      )}
      {!anyChannel && (
        <p role="status" data-ops-no-channel className="mb-space-3 flex items-start gap-space-2 rounded-control bg-warn-tint px-space-3 py-space-2 text-t14 text-fg">
          <BellRing size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" />
          <span>Nobody is told about a problem yet. Connect your Telegram below, or set up e-mail on the server.</span>
        </p>
      )}
      <ul className="grid gap-space-2 md:grid-cols-2" data-ops-checks>
        {sortChecks(data.checks).map((c) => (
          <li key={c.key} data-ops-check={c.key} data-state={c.state} className="rounded-control border border-subtle px-space-3 py-space-2">
            <div className="flex items-center justify-between gap-space-2">
              <span className="text-t14 font-medium text-fg">{c.label}</span>
              <Pill tone={TONE[c.state]}>{WORD[c.state]}</Pill>
            </div>
            <p className="mt-space-1 text-t12 text-fg-muted">
              {c.detail}
              {c.state !== "ok" && c.state !== "unknown" && c.since && <> · since {ago(c.since)}</>}
            </p>
          </li>
        ))}
      </ul>

      <h3 className="mt-space-4 text-t14 font-semibold text-fg">Where alerts go</h3>
      <ul className="mt-space-2 grid gap-space-2 md:grid-cols-3" data-ops-channels>
        <li className="flex items-start gap-space-2 rounded-control border border-subtle px-space-3 py-space-2" data-ops-channel="telegram">
          <MessageCircle size={16} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
          <span className="min-w-0 text-t12">
            <span className="block text-t14 font-medium text-fg">Telegram</span>
            <span className="text-fg-muted">
              {!tg.available ? "The platform bot is not set up on the server yet. Set ATP_TELEGRAM_BOT_TOKEN in the server's .env; until then users see no Connect Telegram or Send to my Telegram."
                : tg.pending ? "Waiting for you to press Start in Telegram…"
                : tg.connected ? (tg.from === "env" ? "Connected (chat set on the server)." : "Your chat is connected.")
                : "Not connected."}
            </span>
          </span>
        </li>
        <li className="flex items-start gap-space-2 rounded-control border border-subtle px-space-3 py-space-2" data-ops-channel="email">
          <Mail size={16} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
          <span className="min-w-0 text-t12">
            <span className="block text-t14 font-medium text-fg">E-mail</span>
            <span className="text-fg-muted">
              {!email.available ? "E-mail (SMTP) is not set up on the server." : `Sends to ${email.recipients} address${email.recipients === 1 ? "" : "es"}.`}
            </span>
          </span>
        </li>
        <li className="flex items-start gap-space-2 rounded-control border border-subtle px-space-3 py-space-2" data-ops-channel="error-tracker">
          <ShieldCheck size={16} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
          <span className="min-w-0 text-t12">
            <span className="block text-t14 font-medium text-fg">Error tracker</span>
            <span className="text-fg-muted">{data.error_tracker.on ? "On: server errors are sent with personal data removed." : "Off."}</span>
          </span>
        </li>
      </ul>

      <div className="mt-space-4 flex flex-wrap gap-space-2">
        <button type="button" className={BUTTON} onClick={onRun} disabled={busy} data-ops-run><RefreshCw size={16} aria-hidden />Check now</button>
        <button type="button" className={BUTTON} onClick={onTest} disabled={busy || !anyChannel} data-ops-test><Send size={16} aria-hidden />Send a test</button>
        {tg.available && (tg.connected && tg.from === "linked"
          ? <button type="button" className={BUTTON} onClick={onUnlink} disabled={busy} data-ops-unlink>Disconnect Telegram</button>
          : !tg.connected && <button type="button" className={BUTTON} onClick={onLink} disabled={busy} data-ops-link><MessageCircle size={16} aria-hidden />Connect Telegram</button>)}
      </div>
      {note && <p role="status" aria-live="polite" className="mt-space-2 text-t12 text-fg-muted" data-ops-note>{note}</p>}
    </Panel>
  );
}

export default function AlertsPanelV2() {
  const [data, setData] = useState<OpsAlerts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => opsApi.view().then(setData, (e) => setError(friendlyError(e))), []);
  useEffect(() => { void load(); }, [load]);
  // While a Telegram code waits, ask every 3 seconds so "connected" shows as soon as Start was pressed.
  const pending = data?.channels.telegram.pending ?? false;
  useEffect(() => {
    if (!pending) return;
    const id = window.setInterval(() => { void load(); }, 3000);
    return () => window.clearInterval(id);
  }, [pending, load]);

  const act = async (work: () => Promise<void>) => {
    setBusy(true);
    setNote(null);
    try { await work(); } catch (e) { setNote(friendlyError(e)); } finally { setBusy(false); }
  };

  if (!data) {
    return (
      <Panel title="Alerts" id="ops-alerts">
        {error ? <p className="text-t12 text-down">Could not load: {error}</p> : <SkeletonRows rows={2} label="Loading" />}
      </Panel>
    );
  }
  return (
    <AlertsPanelView data={data} busy={busy} note={note}
      onRun={() => act(async () => { setData(await opsApi.run()); setNote("Checked just now."); })}
      onTest={() => act(async () => {
        const r = await opsApi.test();
        const parts = [r.telegram !== null && `Telegram: ${r.telegram ? "sent" : "failed"}`, r.email !== null && `e-mail: ${r.email ? "sent" : "failed"}`];
        setNote(parts.filter(Boolean).join(" · ") || "Nothing to send to.");
      })}
      onLink={() => act(async () => {
        const { url } = await opsApi.link();
        window.open(url, "_blank", "noopener,noreferrer");
        setNote("Telegram opened: press Start there. This card updates by itself.");
        await load();
      })}
      onUnlink={() => act(async () => { setData(await opsApi.unlink()); setNote("Telegram disconnected."); })}
    />
  );
}
