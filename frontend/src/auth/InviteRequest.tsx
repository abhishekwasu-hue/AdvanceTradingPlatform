import { useId, useState, type FormEvent } from "react";
import { request } from "../api/client";
import { friendlyError } from "../api/errors";
import { Button } from "../components/primitives";

/**
 * M95 PR-3 (M95-Q3 approved): "Ask for an invite" on the sign-in screen while sign-up is closed (`self_signup` OFF).
 * An e-mail address and an optional note go to `POST /api/auth/invite-request` (no sign-in, per-IP limited); the
 * request is stored for the admin (Admin › Users & business › Invite requests). No e-mail is sent. The answer is the
 * same sentence whether or not the address asked before.
 */

export const NOTE_MAX = 500;
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const inputClass = "w-full rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg "
  + "placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/40";
const linkClass = "text-t14 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand rounded-control";

export const sendInviteRequest = (email: string, note: string) =>
  request<{ message: string }>("/auth/invite-request", {
    method: "POST", body: JSON.stringify({ email: email.trim(), note: note.trim() ? note.trim() : null }),
  });

export default function InviteRequest({ startOpen = false }: { startOpen?: boolean }) {
  const ids = { email: useId(), note: useId(), noteCount: useId() };
  const [open, setOpen] = useState(startOpen);
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!EMAIL_SHAPE.test(email.trim())) { setError("Please enter a valid e-mail address."); return; }
    setBusy(true);
    setError(null);
    try {
      const res = await sendInviteRequest(email, note);
      setDone(res.message);
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex w-full flex-col items-center gap-space-2" data-invite-request={done ? "sent" : open ? "open" : "closed"}>
      <span>New here? Ask your team owner for an invite link.</span>
      {done ? (
        <p role="status" className="text-center text-t14 text-up">{done}</p>
      ) : !open ? (
        <button type="button" onClick={() => setOpen(true)} className={linkClass}>Ask for an invite</button>
      ) : (
        <form onSubmit={submit} noValidate className="w-full space-y-space-2 text-left" aria-label="Ask for an invite">
          <div className="space-y-space-1">
            <label htmlFor={ids.email} className="block text-t14 font-medium text-fg">Your e-mail</label>
            <input id={ids.email} type="email" inputMode="email" autoComplete="email" required maxLength={320}
              className={`${inputClass} h-11`} value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="space-y-space-1">
            <label htmlFor={ids.note} className="block text-t14 font-medium text-fg">Note <span className="font-normal text-fg-muted">(optional)</span></label>
            <textarea id={ids.note} rows={3} maxLength={NOTE_MAX} aria-describedby={ids.noteCount} value={note}
              onChange={(e) => setNote(e.target.value)} placeholder="Who you are or who told you about us"
              className={`${inputClass} resize-y py-space-2`} />
            <p id={ids.noteCount} className="text-right text-t12 text-fg-muted">{note.length} / {NOTE_MAX}</p>
          </div>
          {error && <p role="alert" className="text-t14 text-down">{error}</p>}
          <Button type="submit" variant="primary" loading={busy} className="h-11 w-full text-t14">
            {busy ? "Sending…" : "Send request"}
          </Button>
        </form>
      )}
    </div>
  );
}

/**
 * The sign-in screen's slot: the "Ask for an invite" form only while sign-up is closed; nothing while it is open. New
 * user-facing work goes behind `ui_v2` (CLAUDE.md section 7): without it the slot is the old invite-link line, unchanged.
 */
export function InviteRequestSlot({ signUpOpen, v2 = true }: { signUpOpen: boolean; v2?: boolean }) {
  if (signUpOpen) return null;
  return v2 ? <InviteRequest /> : <span>New here? Ask your team owner for an invite link.</span>;
}
