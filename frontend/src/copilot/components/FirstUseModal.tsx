import { ShieldCheck } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { AI_ACK_REQUIRED_EVENT, api } from "../../api/client";
import { Button, Dialog } from "../../components/primitives";
import type { AiAcknowledgement } from "../../types";
import { friendlyError } from "../aiTask";
import { useCopilotT } from "../i18n";

/**
 * P0.8-D in the redesigned Copilot: a first-use modal. Until this user accepted the current (versioned) text the
 * tabs are not rendered - nothing AI-written appears - and the acceptance is recorded server-side with its version
 * (the audit trail). A version bump, or a 428 from any AI route, brings the modal back.
 */
export default function FirstUseModal({ children, placeholder }: { children: ReactNode; placeholder?: ReactNode }) {
  const t = useCopilotT();
  const [ack, setAck] = useState<AiAcknowledgement | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [read, setRead] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    const load = () => { api.aiAcknowledgement().then((a) => { setAck(a); setOpen(true); }).catch((e) => setLoadError(friendlyError(e, t))); };
    load();
    window.addEventListener(AI_ACK_REQUIRED_EVENT, load);
    return () => window.removeEventListener(AI_ACK_REQUIRED_EVENT, load);
  }, [t]);

  async function accept() {
    if (!ack) return;
    setBusy(true); setError(null);
    try { setAck(await api.aiAcceptAcknowledgement(ack.version, "en")); } catch (e) { setError(friendlyError(e, t)); } finally { setBusy(false); }
  }

  if (loadError) return <div className="rounded-xl border border-down/40 bg-down/5 p-4 text-sm text-fg" role="alert">{loadError}</div>;
  if (!ack) return <>{placeholder}</>;
  if (ack.accepted) return <>{children}</>;
  return (
    <>
      {placeholder}
      <div className="mt-4 flex flex-col items-center gap-3 rounded-2xl border border-dashed border-border p-8 text-center">
        <ShieldCheck size={28} className="text-ai" />
        <p className="max-w-md text-sm text-fg-muted">{t("firstUse.locked")}</p>
        <Button variant="primary" onClick={() => setOpen(true)}>{t("firstUse.open")}</Button>
      </div>
      <Dialog open={open} onOpenChange={setOpen} title={t("firstUse.title")} description={t("firstUse.version", { version: ack.version })}
        footer={<Button variant="primary" disabled={!read || busy} loading={busy} onClick={() => void accept()}>{t("firstUse.accept")}</Button>}>
        <div className="space-y-3" data-testid="first-use-modal">
          <p className="rounded-lg border border-ai/30 bg-ai/5 p-3 font-semibold leading-relaxed text-fg">{t("firstUse.headline")}</p>
          <p className="leading-relaxed text-fg">{ack.text.en}</p>
          {ack.text.mr && (
            <details className="text-xs text-fg-muted">
              <summary className="cursor-pointer text-brand hover:underline">{t("firstUse.readMr")}</summary>
              {/* the server's own translation of the versioned text (data, not an interface string) */}
              <p className="mt-1 leading-relaxed" lang="mr">{ack.text.mr}</p>
            </details>
          )}
          <label className="flex items-start gap-2 text-sm text-fg">
            <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[rgb(var(--ai))]" checked={read} onChange={(e) => setRead(e.target.checked)} />
            <span>{t("firstUse.confirm")}</span>
          </label>
          {error && <div className="text-sm text-down" role="alert">{error}</div>}
        </div>
      </Dialog>
    </>
  );
}
