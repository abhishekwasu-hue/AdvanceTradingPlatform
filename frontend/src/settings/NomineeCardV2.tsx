import { useEffect, useState, type FormEvent } from "react";
import { request } from "../api/client";
import { friendlyError } from "../api/errors";
import { Card } from "../components/ui";
import { Details } from "../ui/Details";

/** P3-4-B10 (DPDP right to nominate): Settings -> Account -> "Your nominee" - the one person who may use your data
 * rights if you die or cannot act. View, change, remove. Shown only when the server answers (an older server has no route). */
export interface Nominee { name: string; relationship: string; email: string | null; phone: string | null; updated_at: string | null }
interface Reply { nominee: Nominee | null }

const FIELD = "w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm";
const BTN = "min-h-11 rounded border border-border px-3 py-1.5 text-sm hover:bg-surface-2";

export default function NomineeCardV2() {
  const [loaded, setLoaded] = useState(false);
  const [nominee, setNominee] = useState<Nominee | null>(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [relationship, setRelationship] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    request<Reply>("/me/nominee").then((r) => { setNominee(r.nominee); setLoaded(true); })
      .catch(() => setLoaded(false)); // intentional: a server without the route hides the card, nothing to show
  }, []);

  if (!loaded) return null;

  function edit() {
    setName(nominee?.name ?? ""); setRelationship(nominee?.relationship ?? "");
    setEmail(nominee?.email ?? ""); setPhone(nominee?.phone ?? "");
    setNote(null); setEditing(true);
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!email.trim() && !phone.trim()) { setNote("Give an e-mail or a phone number for your nominee."); return; }
    setBusy(true); setNote(null);
    try {
      const r = await request<Reply>("/me/nominee", { method: "PUT", body: JSON.stringify({
        name, relationship, email: email.trim() || null, phone: phone.trim() || null }) });
      setNominee(r.nominee); setEditing(false);
    } catch (err) { setNote(friendlyError(err)); } finally { setBusy(false); }
  }

  async function remove() {
    setBusy(true); setNote(null);
    try { await request<Reply>("/me/nominee", { method: "DELETE" }); setNominee(null); setEditing(false); }
    catch (err) { setNote(friendlyError(err)); } finally { setBusy(false); }
  }

  return (
    <Card title="Your nominee">
      <div data-nominee className="space-y-3 text-sm">
        {editing ? (
          <form onSubmit={(e) => void save(e)} data-nominee-form className="grid gap-2 sm:grid-cols-2">
            <label className="text-xs text-fg-muted">Name
              <input className={FIELD} value={name} maxLength={120} required autoComplete="off" data-nominee-name onChange={(e) => setName(e.target.value)} /></label>
            <label className="text-xs text-fg-muted">Relationship
              <input className={FIELD} value={relationship} maxLength={60} required autoComplete="off" placeholder="For example spouse" data-nominee-relationship
                onChange={(e) => setRelationship(e.target.value)} /></label>
            <label className="text-xs text-fg-muted">E-mail
              <input className={FIELD} type="email" value={email} maxLength={254} autoComplete="off" data-nominee-email onChange={(e) => setEmail(e.target.value)} /></label>
            <label className="text-xs text-fg-muted">Phone
              <input className={FIELD} type="tel" value={phone} maxLength={20} autoComplete="off" data-nominee-phone onChange={(e) => setPhone(e.target.value)} /></label>
            <div className="flex flex-wrap gap-3 sm:col-span-2">
              <button type="submit" disabled={busy || !name.trim() || !relationship.trim()} data-nominee-save className={BTN}>Save nominee</button>
              <button type="button" disabled={busy} onClick={() => setEditing(false)} data-nominee-cancel className={BTN}>Cancel</button>
            </div>
          </form>
        ) : nominee ? (
          <div data-nominee-view className="space-y-2">
            <p className="text-fg">{nominee.name} <span className="text-fg-muted">({nominee.relationship})</span></p>
            <p className="text-xs text-fg-muted">{[nominee.email, nominee.phone].filter(Boolean).join(" / ")}</p>
            <div className="flex flex-wrap gap-3">
              <button type="button" disabled={busy} onClick={edit} data-nominee-change className={BTN}>Change</button>
              <button type="button" disabled={busy} onClick={() => void remove()} data-nominee-remove
                className="min-h-11 rounded border border-down/40 px-3 py-1.5 text-sm text-down hover:bg-down/10">Remove</button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" onClick={edit} data-nominee-add className={BTN}>Name a nominee</button>
            <span className="text-xs text-fg-muted">One person who may use your data rights if you die or cannot act.</span>
          </div>
        )}
        {note && <p role="alert" className="text-xs text-down">{note}</p>}
        <Details id="account.nominee" label="About nominees" className="text-xs text-fg-muted">
          <p>Naming someone does not give them access to your account. If they write to us, we check who they are before we act.</p>
          <p>Your nominee&apos;s details are deleted when you delete your account, and are part of &quot;Download my data&quot;.</p>
        </Details>
      </div>
    </Card>
  );
}
