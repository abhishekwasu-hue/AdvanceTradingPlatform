import { useEffect, useState, type ReactNode } from "react";
import { AI_ACK_REQUIRED_EVENT, api } from "../api/client";
import type { AiAcknowledgement } from "../types";
import { Card } from "./ui";

/** P0.8-D: the first-use acknowledgement of the AI Copilot, shared by every page with AI-written content. Until this
 * user accepted the current version the children are not rendered; a 428 from any AI route re-reads the state, so a
 * version bump while a page is open brings the screen back. */
export default function AiAcknowledgementGate({ lang, setLang, children }: {
  lang: "en" | "mr"; setLang?: (l: "en" | "mr") => void; children: ReactNode;
}) {
  const [ack, setAck] = useState<AiAcknowledgement | null>(null);
  const [read, setRead] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<"en" | "mr">(lang);
  useEffect(() => setShown(lang), [lang]);
  useEffect(() => {
    const load = () => { api.aiAcknowledgement().then(setAck).catch((e) => setError(String(e))); };
    load();
    window.addEventListener(AI_ACK_REQUIRED_EVENT, load);
    return () => window.removeEventListener(AI_ACK_REQUIRED_EVENT, load);
  }, []);
  const pick = (l: "en" | "mr") => { setShown(l); setLang?.(l); };
  async function accept() {
    if (!ack) return;
    setBusy(true); setError(null);
    try { setAck(await api.aiAcceptAcknowledgement(ack.version, shown)); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  if (!ack) return error ? <Card><p className="text-sm text-danger">{error}</p></Card> : null;
  if (ack.accepted) return <>{children}</>;
  return (
    <Card title={shown === "mr" ? "सुरू करण्याआधी - कृपया वाचा" : "Before you start - please read"}>
      <div className="flex justify-end mb-2">
        <div className="flex overflow-hidden rounded-lg border border-border text-xs">
          {(["mr", "en"] as const).map((l) => (
            <button key={l} onClick={() => pick(l)} className={`px-2.5 py-1 ${shown === l ? "bg-purple-500/20 text-purple-100" : "text-muted"}`}>{l === "mr" ? "मराठी" : "English"}</button>
          ))}
        </div>
      </div>
      <p className="text-sm text-slate-100 leading-relaxed">{ack.text[shown]}</p>
      <p className="mt-2 text-xs text-muted">{shown === "mr" ? "आवृत्ती" : "Version"} {ack.version}</p>
      <label className="mt-3 flex items-start gap-2 text-sm text-slate-100">
        <input type="checkbox" checked={read} onChange={(e) => setRead(e.target.checked)} />
        <span>{shown === "mr" ? "मी वाचले आणि समजले: AI Copilot सल्लागार नाही; निर्णय माझा." : "I have read and understood: the AI Copilot is not an adviser; the decision is mine."}</span>
      </label>
      <button disabled={!read || busy} onClick={accept}
              className="mt-3 rounded bg-purple-600 px-4 py-1.5 text-sm font-bold text-white hover:bg-purple-500 disabled:opacity-50">
        {shown === "mr" ? "समजले, पुढे जा" : "I understand, continue"}
      </button>
      {error && <div className="mt-3 text-sm text-danger">{error}</div>}
    </Card>
  );
}
