import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";

/** Detects the backend's step-up signal: a 403 whose detail mentions two-factor. */
export function isStepUpError(e: unknown): boolean {
  const text = String(e);
  return text.startsWith("Error: 403") && text.toLowerCase().includes("two-factor");
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
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 space-y-3">
      <div className="flex items-center gap-2 text-amber-400 font-bold text-sm"><ShieldCheck size={16} /> Two-factor check required</div>
      <p className="text-xs text-slate-300">{reason}</p>
      {needsEnrol ? (
        <p className="text-xs text-muted">Go to the Account tab, enable two-factor authentication, then come back.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="rounded bg-panel2 border border-amber-500/40 px-2 py-1.5 text-sm w-44 font-mono"
            placeholder="authenticator code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
            autoFocus
          />
          <button onClick={submit} disabled={busy || code.length < 6} className="rounded bg-amber-600 hover:bg-amber-700 text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50">Verify</button>
        </div>
      )}
      {error && <div className="text-xs text-danger">{error}</div>}
      <button onClick={onCancel} className="text-xs text-muted hover:text-slate-200">Cancel</button>
    </div>
  );
}
