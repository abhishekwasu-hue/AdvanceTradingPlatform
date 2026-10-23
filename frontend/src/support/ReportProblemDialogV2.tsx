import { useEffect, useState } from "react";
import { friendlyError } from "../api/client";
import { Button } from "../components/primitives/Button";
import { Dialog } from "../components/primitives/Dialog";
import { FEEDBACK_MAX_CHARS, feedbackScreen, feedbackVersion } from "../feedback/feedback";
import { appVersion } from "../pwa/events";
import { deskApi, reportBody } from "./desk";
import { lastServerError, type LastServerError } from "./lastError";

const time = (iso: string) => new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/**
 * C12: "Report a problem". The user says what went wrong; the app attaches the page, the time, the app version and -
 * while the box stays ticked - the reference of the last server error this tab saw (and where and when). Everything
 * attached is listed before sending. The report lands in the support inbox as an open ticket.
 */
export default function ReportProblemDialogV2({ open, onOpenChange, pathname }: {
  open: boolean; onOpenChange: (open: boolean) => void; pathname: string;
}) {
  const [text, setText] = useState("");
  const [withError, setWithError] = useState(true);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastError, setLastError] = useState<LastServerError | null>(null);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    if (!open) return;
    setSent(null); setError(null); setWithError(true);
    setLastError(lastServerError());
    setNow(new Date());
  }, [open]);

  const version = feedbackVersion(appVersion());
  const screen = feedbackScreen(pathname);
  const body = reportBody(text, pathname, appVersion(), withError ? lastError : null, now);

  async function send() {
    if (!body) return;
    setSending(true);
    setError(null);
    try {
      const out = await deskApi.report({ ...body, client_time: new Date().toISOString() });
      setSent(out.id);
      setText("");
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Report a problem"
      description="Tell us what went wrong. We read every report."
      footer={sent !== null ? <Button onClick={() => onOpenChange(false)}>Close</Button> : (
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" loading={sending} disabled={!body} onClick={() => void send()}>Send report</Button>
        </>
      )}
    >
      {sent !== null ? (
        <div className="space-y-space-2" role="status" data-report-sent={sent}>
          <p className="text-t14 font-medium text-up">Thank you - your report was sent.</p>
          <p className="text-t14 text-fg-muted">Your report number is <span className="font-semibold text-fg">#{sent}</span>. Mention it if you write to us.</p>
        </div>
      ) : (
        <div className="space-y-space-3" data-report-form>
          <label htmlFor="report-message" className="text-t14 font-medium text-fg">What went wrong?</label>
          <textarea
            id="report-message"
            autoFocus
            rows={5}
            maxLength={FEEDBACK_MAX_CHARS}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="What did you do, what happened, and what did you expect?"
            className="w-full resize-y rounded-control border border-border bg-surface-2 px-space-2 py-space-2 text-t14 text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
          <div className="rounded-control border border-border bg-surface-2 p-space-3 text-t12" data-report-attached>
            <div className="mb-space-1 font-semibold text-fg">Sent with your report</div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-space-3 gap-y-space-1">
              <dt className="text-fg-muted">Page</dt><dd className="break-all text-fg">{screen}</dd>
              <dt className="text-fg-muted">Time</dt><dd className="text-fg">{time(now.toISOString())}</dd>
              <dt className="text-fg-muted">App version</dt><dd className="break-all text-fg">{version}</dd>
            </dl>
            {lastError && (
              <label className="mt-space-2 flex min-h-11 cursor-pointer items-start gap-space-2 md:min-h-0" data-report-error>
                <input type="checkbox" checked={withError} onChange={(e) => setWithError(e.target.checked)} className="mt-0.5 h-4 w-4 accent-brand" />
                <span className="text-fg">
                  The last error you saw: reference <span className="break-all font-semibold">{lastError.id}</span>
                  <span className="text-fg-muted"> on {lastError.page}, {time(lastError.at)}</span>
                </span>
              </label>
            )}
            <p className="mt-space-2 text-fg-muted">
              Nothing else is attached: no passwords, keys, positions or orders, and nothing you typed into other forms. We remove
              anything in your message that looks like a password, a key or someone else's e-mail address.
            </p>
          </div>
          {error && <p className="text-t12 text-down" role="alert">{error}</p>}
        </div>
      )}
    </Dialog>
  );
}
