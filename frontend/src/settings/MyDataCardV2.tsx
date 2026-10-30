import { useEffect, useState } from "react";
import { downloadMyData, request } from "../api/client";
import { friendlyError } from "../api/errors";
import { Card } from "../components/ui";
import { Details } from "../ui/Details";
import { formatDateIST } from "../ui/format";

/** AUD-G1.1 (owner audit 28 Oct; DPDP): Settings -> Account - download everything stored about you, or delete your
 * account (after a grace period you can cancel in). Orders and trades stay as an anonymous id for the period the
 * regulations require. */
export interface EraseStatus { requested: boolean; requested_at: string | null; erase_after: string | null; grace_days: number }

/** CP-UX CU6: the Copilot conversation the server keeps across reloads - deleted here, on its own (ui_v2 only). */
export function CopilotConversationRow({ busy, setBusy }: { busy: boolean; setBusy: (b: boolean) => void }) {
  const [sure, setSure] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  async function forget() {
    setBusy(true); setDone(null);
    try {
      const r = await request<{ deleted: number }>("/ai/chat/history", { method: "DELETE" });
      setDone(r.deleted ? "Your Copilot conversation is deleted." : "There was no Copilot conversation to delete.");
      setSure(false);
    } catch (e) { setDone(friendlyError(e)); } finally { setBusy(false); }
  }
  return (
    <div data-copilot-conversation className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        {sure ? (
          <>
            <button type="button" disabled={busy} onClick={() => void forget()} data-copilot-forget-confirm
              className="min-h-11 rounded border border-down/40 px-3 py-1.5 text-sm text-down hover:bg-down/10">Yes, delete it</button>
            <button type="button" disabled={busy} onClick={() => setSure(false)} data-copilot-forget-cancel
              className="min-h-11 rounded border border-border px-3 py-1.5 text-sm hover:bg-surface-2">Keep it</button>
          </>
        ) : (
          <button type="button" disabled={busy} onClick={() => { setSure(true); setDone(null); }} data-copilot-forget
            className="min-h-11 rounded border border-border px-3 py-1.5 text-sm hover:bg-surface-2">Delete my Copilot conversation</button>
        )}
        <span className="text-xs text-fg-muted">{sure ? "Your questions and the Copilot's answers are removed for good." : "The questions you asked the Copilot and its answers, kept so they are there after a reload."}</span>
      </div>
      {done && <p className="text-xs text-fg-muted" role="status">{done}</p>}
    </div>
  );
}

export default function MyDataCardV2({ copilot = false }: { copilot?: boolean }) {
  const [status, setStatus] = useState<EraseStatus | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { request<EraseStatus>("/me/erase").then(setStatus).catch(() => setStatus(null)); }, []);

  async function download() {
    setBusy(true); setNote(null);
    try {
      const { blob, filename } = await downloadMyData();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { setNote(friendlyError(e)); } finally { setBusy(false); }
  }

  async function erase() {
    setBusy(true); setNote(null);
    try {
      setStatus(await request<EraseStatus>("/me/erase", { method: "POST", body: JSON.stringify({ password, confirm }) }));
      setPassword(""); setConfirm("");
    } catch (e) { setNote(friendlyError(e)); } finally { setBusy(false); }
  }

  async function cancel() {
    setBusy(true); setNote(null);
    try { setStatus(await request<EraseStatus>("/me/erase", { method: "DELETE" })); } catch (e) { setNote(friendlyError(e)); } finally { setBusy(false); }
  }

  return (
    <Card title="Your data">
      <div data-my-data className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" disabled={busy} onClick={() => void download()} data-my-data-download
            className="min-h-11 rounded border border-border px-3 py-1.5 text-sm hover:bg-surface-2">Download my data</button>
          <span className="text-xs text-fg-muted">A ZIP of your profile, settings, trades and journal.</span>
        </div>
        {copilot && <CopilotConversationRow busy={busy} setBusy={setBusy} />}
        {status?.requested ? (
          <div data-erase-pending className="rounded border border-down/40 p-3">
            <p className="text-fg">Your account will be deleted on {formatDateIST(status.erase_after)}.</p>
            <button type="button" disabled={busy} onClick={() => void cancel()} data-erase-cancel
              className="mt-2 min-h-11 rounded border border-border px-3 py-1.5 text-sm hover:bg-surface-2">Keep my account</button>
          </div>
        ) : (
          <Details id="account.delete" label="Delete my account" className="text-xs text-fg-muted">
            <p>Your personal data is erased {status?.grace_days ?? 7} days after you ask; until then you can keep the account.</p>
            <p>Orders and trades stay under an anonymous id for the period the regulations require.</p>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <input type="password" autoComplete="current-password" placeholder="Your password" aria-label="Your password"
                className="rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={password} onChange={(e) => setPassword(e.target.value)} />
              <input placeholder="Type ERASE" aria-label="Type ERASE to confirm"
                className="rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            </div>
            <button type="button" disabled={busy || !password || confirm.trim().toUpperCase() !== "ERASE"} onClick={() => void erase()} data-erase-ask
              className="mt-2 min-h-11 rounded border border-down/40 px-3 py-1.5 text-sm text-down hover:bg-down/10">Delete my account</button>
          </Details>
        )}
        {note && <p className="text-xs text-down">{note}</p>}
      </div>
    </Card>
  );
}
