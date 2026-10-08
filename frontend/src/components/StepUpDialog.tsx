import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { ApiError, api } from "../api/client";

/** Detects the backend's step-up signal: a 403 whose detail mentions two-factor. */
export function isStepUpError(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403 && e.message.toLowerCase().includes("two-factor");
}

/**
 * Asks for an authenticator code, marks the current session as MFA-verified and calls
 * `onVerified` so the caller can retry the action that was refused.
 */
export default function StepUpDialog({ reason, onVerified, onCancel }: { reason: string; onVerified: () => void; onCancel: () => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const needsEnrol = reason.toLowerCase().includes("enable it");

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.mfaStepUp(code);
      onVerified();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-warn/40 bg-warn/10 p-4 space-y-3">
      <div className="flex items-center gap-2 text-warn font-bold text-sm"><ShieldCheck size={16} /> Two-factor check required</div>
      <p className="text-xs text-fg-muted">{reason}</p>
      {needsEnrol ? (
        <p className="text-xs text-fg-muted">Go to the Account tab, enable two-factor authentication, then come back.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="rounded bg-surface-2 border border-warn/40 px-2 py-1.5 text-sm w-44 font-mono"
            placeholder="authenticator code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
            autoFocus
          />
          <button onClick={submit} disabled={busy || code.length < 6} className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-4 py-1.5 text-sm disabled:opacity-50">Verify</button>
        </div>
      )}
      {error && <div className="text-xs text-down">{error}</div>}
      <button onClick={onCancel} className="text-xs text-fg-muted hover:text-fg">Cancel</button>
    </div>
  );
}
