import { errorSentence } from "../api/errors";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Button } from "../components/primitives/Button";
import { Dialog } from "../components/primitives/Dialog";
import { appVersion } from "../pwa/events";
import { FEEDBACK_MAX_CHARS, feedbackMessage, feedbackScreen, feedbackVersion } from "./feedback";

/** PW6: "Send feedback". Shows exactly what is attached (the app version and the screen path) before anything is sent;
 * no account data, market data, tokens or settings go with it. */
export default function FeedbackDialog({ open, onOpenChange, pathname, initialText }: {
  open: boolean; onOpenChange: (open: boolean) => void; pathname: string;
  /** Z10-f: the support bot's hand-off fills the form with the question it could not answer (editable before sending). */
  initialText?: string;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const version = feedbackVersion(appVersion());
  const screen = feedbackScreen(pathname);
  const message = feedbackMessage(text);

  useEffect(() => {
    if (open) { setSent(false); setError(null); }
    if (open && initialText) setText(initialText.slice(0, FEEDBACK_MAX_CHARS));
  }, [open, initialText]);

  async function send() {
    if (!message) return;
    setSending(true);
    setError(null);
    try {
      await api.sendFeedback({ message, app_version: version, screen });
      setSent(true);
      setText("");
    } catch (e) {
      setError(errorSentence(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Send feedback"
      description="Tell us what works, what is broken or what is missing."
      footer={sent ? <Button onClick={() => onOpenChange(false)}>Close</Button> : (
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" loading={sending} disabled={!message} onClick={() => void send()}>Send</Button>
        </>
      )}
    >
      {sent ? (
        <p className="text-sm text-up" role="status">Thank you - your feedback was sent.</p>
      ) : (
        <div className="space-y-3">
          <label htmlFor="feedback-message" className="sr-only">Your feedback</label>
          <textarea
            id="feedback-message"
            autoFocus
            rows={6}
            maxLength={FEEDBACK_MAX_CHARS}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="What happened, and what did you expect?"
            className="w-full resize-y rounded border border-border bg-surface-2 px-2 py-1.5 text-sm focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
          <div className="text-right text-xs text-fg-muted">{text.length} / {FEEDBACK_MAX_CHARS}</div>
          <div className="rounded-md border border-border bg-surface-2 p-3 text-xs">
            <div className="mb-1 font-semibold text-fg">Sent with your message</div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt className="text-fg-muted">App version</dt><dd className="font-mono text-fg break-all">{version}</dd>
              <dt className="text-fg-muted">Screen</dt><dd className="font-mono text-fg break-all">{screen}</dd>
            </dl>
            <p className="mt-2 text-fg-muted">Your account and browser type are recorded with it so we can follow up. Nothing else is attached: no positions, orders, broker details or keys.</p>
          </div>
          {error && <p className="text-xs text-down" role="alert">{error}</p>}
        </div>
      )}
    </Dialog>
  );
}
