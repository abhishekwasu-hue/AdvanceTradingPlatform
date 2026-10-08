import { useEffect, useState, type ReactNode } from "react";
import { AI_ACK_REQUIRED_EVENT, api } from "../api/client";
import type { AiAcknowledgement } from "../types";
import { Card } from "./ui";

/** P0.8-D: the first-use acknowledgement of the AI Copilot, shared by every page with AI-written content. Until this
 * user accepted the current version the children are not rendered; a 428 from any AI route re-reads the state, so a
 * version bump while a page is open brings the screen back.
 * P0.9: English. The Marathi translation the server keeps with the text is behind "Read in Marathi" (closed by default). */
export default function AiAcknowledgementGate({ children }: { children: ReactNode }) {
  const [ack, setAck] = useState<AiAcknowledgement | null>(null);
  const [read, setRead] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const load = () => { api.aiAcknowledgement().then(setAck).catch((e) => setError(String(e))); };
    load();
    window.addEventListener(AI_ACK_REQUIRED_EVENT, load);
    return () => window.removeEventListener(AI_ACK_REQUIRED_EVENT, load);
  }, []);
  async function accept() {
    if (!ack) return;
    setBusy(true); setError(null);
    try { setAck(await api.aiAcceptAcknowledgement(ack.version, "en")); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  if (!ack) return error ? <Card><p className="text-sm text-danger">{error}</p></Card> : null;
  if (ack.accepted) return <>{children}</>;
  return (
    <Card title="Before you start - please read">
      <p className="text-sm text-slate-100 leading-relaxed">{ack.text.en}</p>
      <details className="mt-2 text-xs text-muted">
        <summary className="cursor-pointer text-sky-300 hover:underline">Read in Marathi</summary>
        <p className="mt-1 leading-relaxed text-slate-300" lang="mr">{ack.text.mr}</p>
      </details>
      <p className="mt-2 text-xs text-muted">Version {ack.version}</p>
      <label className="mt-3 flex items-start gap-2 text-sm text-slate-100">
        <input type="checkbox" checked={read} onChange={(e) => setRead(e.target.checked)} />
        <span>I have read and understood: the AI Copilot is not an adviser; the decision is mine.</span>
      </label>
      <button disabled={!read || busy} onClick={accept}
              className="mt-3 rounded bg-purple-600 px-4 py-1.5 text-sm font-bold text-white hover:bg-purple-500 disabled:opacity-50">
        I understand, continue
      </button>
      {error && <div className="mt-3 text-sm text-danger">{error}</div>}
    </Card>
  );
}
