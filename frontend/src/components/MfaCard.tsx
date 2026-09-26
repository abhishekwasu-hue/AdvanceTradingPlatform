import { QRCodeSVG } from "qrcode.react";
import { KeyRound, ShieldCheck, ShieldOff } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { Card } from "./ui";
import type { MfaStatus } from "../types";

/**
 * Account-tab card for TOTP two-factor authentication: enrol (QR + manual key), confirm with
 * a code, show backup codes once, regenerate them, or disable (password + code).
 */
export default function MfaCard({ status, onChange }: { status: MfaStatus | null; onChange: () => void }) {
  const [enrol, setEnrol] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [backupCodes, setBackupCodes] = useState<string[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await fn();
      onChange();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;

  return (
    <Card title="Two-factor authentication (TOTP)">
      <div className="flex items-center gap-2 text-sm mb-2">
        {status.enabled ? <ShieldCheck size={16} className="text-accent" /> : <ShieldOff size={16} className="text-warn" />}
        <span className={status.enabled ? "text-accent font-semibold" : "text-warn font-semibold"}>
          {status.enabled ? `Enabled${status.enabled_at ? ` since ${new Date(status.enabled_at).toLocaleDateString()}` : ""}` : "Not enabled"}
        </span>
        {status.enabled && <span className="text-xs text-muted">· {status.backup_codes_remaining} backup codes left · this session {status.session_verified ? "verified" : "not yet verified"}</span>}
      </div>
      <p className="text-xs text-muted mb-3">
        Works with Google Authenticator, Authy, 1Password and any RFC 6238 app. {status.required_for_live
          ? "Your organisation requires it for live trading, broker credentials and the admin console."
          : "Strongly recommended for anyone who can trade live money."}
      </p>

      {!status.enabled && !enrol && (
        <button disabled={busy} onClick={() => run(async () => setEnrol(await api.mfaEnrol()))} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50">
          Set up two-factor authentication
        </button>
      )}

      {!status.enabled && enrol && !backupCodes && (
        <div className="grid sm:grid-cols-[auto_1fr] gap-4 items-start">
          <div className="rounded-lg bg-white p-2 w-fit"><QRCodeSVG value={enrol.otpauth_uri} size={148} /></div>
          <div className="space-y-2 text-xs">
            <div className="text-slate-300">1. Scan the QR code in your authenticator app, or enter this key manually:</div>
            <code className="block font-mono text-sm tracking-wider text-slate-100 bg-panel2 rounded px-2 py-1 select-all">{enrol.secret}</code>
            <div className="text-slate-300">2. Enter the 6-digit code the app shows now:</div>
            <div className="flex gap-2">
              <input className="rounded bg-panel2 border border-border px-2 py-1.5 text-sm font-mono w-36" value={code} onChange={(e) => setCode(e.target.value)} placeholder="123456" />
              <button disabled={busy || code.length < 6} onClick={() => run(async () => { const r = await api.mfaConfirm(code); setBackupCodes(r.backup_codes); setCode(""); setEnrol(null); })} className="rounded bg-accent hover:bg-green-600 text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50">Confirm & enable</button>
            </div>
          </div>
        </div>
      )}

      {backupCodes && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 space-y-2">
          <div className="flex items-center gap-2 text-amber-400 font-bold text-sm"><KeyRound size={14} /> Backup codes - shown once, save them now</div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-1 font-mono text-sm text-slate-100">{backupCodes.map((c) => <span key={c}>{c}</span>)}</div>
          <p className="text-xs text-muted">Each works once if you lose your phone. Store them somewhere safe, not in this browser.</p>
          <button onClick={() => setBackupCodes(null)} className="text-xs text-brand hover:underline">I have saved them</button>
        </div>
      )}

      {status.enabled && !backupCodes && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-end gap-2 text-xs">
            <div>
              <label className="block text-muted mb-1">Authenticator code</label>
              <input className="rounded bg-panel2 border border-border px-2 py-1.5 text-sm font-mono w-36" value={code} onChange={(e) => setCode(e.target.value)} placeholder="123456" />
            </div>
            {!status.session_verified && (
              <button disabled={busy || code.length < 6} onClick={() => run(async () => { await api.mfaStepUp(code); setCode(""); setMessage("This session is now verified."); })} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 disabled:opacity-50">Verify this session</button>
            )}
            <button disabled={busy || code.length < 6} onClick={() => run(async () => { const r = await api.mfaRegenerateBackupCodes(code); setBackupCodes(r.backup_codes); setCode(""); })} className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1.5 disabled:opacity-50">Regenerate backup codes</button>
          </div>
          <details className="text-xs">
            <summary className="cursor-pointer text-muted hover:text-slate-200">Disable two-factor authentication</summary>
            <div className="mt-2 flex flex-wrap items-end gap-2">
              <input type="password" autoComplete="current-password" className="rounded bg-panel2 border border-border px-2 py-1.5 text-sm w-44" placeholder="password" value={password} onChange={(e) => setPassword(e.target.value)} />
              <button disabled={busy || code.length < 6 || !password} onClick={() => run(async () => { await api.mfaDisable(password, code); setPassword(""); setCode(""); setMessage("Two-factor authentication disabled."); })} className="rounded border border-danger/40 hover:bg-danger/10 text-danger px-3 py-1.5 disabled:opacity-50">Disable (needs password + code)</button>
            </div>
          </details>
        </div>
      )}

      {error && <div className="mt-2 text-xs text-danger">{error}</div>}
      {message && <div className="mt-2 text-xs text-accent">{message}</div>}
    </Card>
  );
}
