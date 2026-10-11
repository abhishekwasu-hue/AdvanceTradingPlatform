import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { PaperAccount, PaperLadder } from "../types";
import { Card, StatTile, signClass } from "./ui";
import { criterionProgress, criterionText, signedMoney } from "./paperLadder";

/**
 * Z3-b (beta spec, Phase 0): the signed-in user's own PAPER account - balance, equity, P&L after charges, the
 * PAPER-scope pause - and the progression ladder card (PAPER stage; LIVE is locked in Phase 0). Every number comes from
 * the server, computed from this user's own PAPER trades. Hidden when the `paper_account` flag is off (the API answers 404).
 */
export default function PaperAccountCard({ canTrade }: { canTrade: boolean }) {
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [ladder, setLadder] = useState<PaperLadder | null>(null);
  const [hidden, setHidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [capital, setCapital] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  function load() {
    Promise.all([api.paperAccount(), api.paperLadder()])
      .then(([a, l]) => { setAccount(a); setLadder(l); setHidden(false); })
      .catch(() => setHidden(true));
  }
  useEffect(load, []);

  if (hidden || !account) return null;

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true); setMessage(null);
    try { await action(); setMessage(done); load(); }
    catch (e) { setMessage(String(e)); }
    finally { setBusy(false); }
  }

  const r = account.realised;
  return (
    <Card title="PAPER account" className="space-y-3">
      <div data-testid="paper-account" className="space-y-3">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label="Balance" value={account.balance.toLocaleString("en-IN", { maximumFractionDigits: 2 })} />
          <StatTile label="Equity (with open marks)" value={account.equity.toLocaleString("en-IN", { maximumFractionDigits: 2 })} />
          <StatTile label="Net P&L (after charges)" value={signedMoney(r.net)} tone={r.net > 0 ? "up" : r.net < 0 ? "down" : "default"} />
          <StatTile label="Closed / open" value={`${account.closed_trades} / ${account.open_positions}`} />
        </div>
        <p className="text-xs text-fg-muted">
          Start {account.starting_capital.toLocaleString("en-IN")} · gross <span className={signClass(r.gross)}>{signedMoney(r.gross)}</span> − charges {r.charges.toFixed(2)} = net{" "}
          <span className={signClass(r.net)}>{signedMoney(r.net)}</span> · open marks <span className={signClass(account.unrealised)}>{signedMoney(account.unrealised)}</span> ·
          {" "}proposals: {account.proposals.approved_by_you} approved, {account.proposals.rejected_by_you} rejected, {account.proposals.pending} waiting ·
          {" "}{account.not_taken.length} not taken (would-have records)
        </p>
        <p className="text-[11px] text-fg-muted">{account.note}{account.excluded_trades > 0 ? ` (${account.excluded_trades} left out.)` : ""}</p>

        {canTrade && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {account.paused ? (
              <>
                <span className="rounded bg-warn/15 px-2 py-1 text-warn" data-testid="paper-paused">New PAPER entries paused{account.paused_reason ? ` (${account.paused_reason})` : ""}</span>
                <button disabled={busy} onClick={() => void run(() => api.paperResume(), "New PAPER entries are on again.")}
                        className="min-h-[44px] rounded border border-border px-2 py-1 text-fg hover:bg-surface-2 disabled:opacity-50 sm:min-h-0">Resume</button>
              </>
            ) : (
              <button disabled={busy} onClick={() => void run(() => api.paperPause(), "New PAPER entries paused; exits keep running.")}
                      className="min-h-[44px] rounded border border-border px-2 py-1 text-fg hover:bg-surface-2 disabled:opacity-50 sm:min-h-0">Pause new PAPER entries</button>
            )}
            {account.closed_trades + account.open_positions + account.excluded_trades === 0 && <>
            <input type="number" min={10000} placeholder="Starting capital" value={capital} onChange={(e) => setCapital(e.target.value)}
                   className="min-h-[44px] w-36 rounded border border-border bg-surface-2 px-2 py-1 sm:min-h-0" aria-label="Starting capital" />
            <button disabled={busy || !capital} onClick={() => void run(() => api.setPaperCapital(Number(capital)), "Starting capital saved.")}
                    className="min-h-[44px] rounded border border-border px-2 py-1 text-fg hover:bg-surface-2 disabled:opacity-50 sm:min-h-0">Set</button>
            </>}
            {message && <span className="text-fg-muted" role="status">{message}</span>}
          </div>
        )}

        {ladder && (
          <div className="space-y-2 rounded-lg border border-border p-3" data-testid="paper-ladder">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-semibold text-fg">Progression ladder</span>
              {ladder.stages.map((s, i) => (
                <span key={s.key} className="flex items-center gap-2">
                  {i > 0 && <span className="text-fg-muted" aria-hidden>→</span>}
                  <span className={`rounded px-2 py-0.5 ${s.status === "current" ? "bg-info/15 text-info" : "bg-surface-2 text-fg-muted"}`}>
                    {s.label}{s.status === "locked" ? ` · ${s.note ?? "locked"}` : s.status === "current" ? " · current" : ""}
                  </span>
                </span>
              ))}
              <span className="text-fg-muted">{ladder.met} of {ladder.measured} measured criteria met</span>
            </div>
            <ul className="space-y-1.5">
              {ladder.criteria.map((c) => {
                const p = criterionProgress(c);
                return (
                  <li key={c.key} className="text-xs" data-testid={`ladder-${c.key}`}>
                    <div className="flex justify-between gap-2">
                      <span className="text-fg">{c.label}</span>
                      <span className={c.met === true ? "text-up" : "text-fg-muted"}>{criterionText(c)}{c.met === true ? " ✓" : ""}</span>
                    </div>
                    {p != null && (
                      <div className="mt-1 h-1.5 w-full rounded bg-surface-2" role="progressbar" aria-label={c.label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(p * 100)}>
                        <div className={`h-1.5 rounded ${c.met ? "bg-up" : "bg-info"}`} style={{ width: `${Math.round(p * 100)}%` }} />
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
            <p className="text-[11px] text-fg-muted">Figures from your own PAPER trades, as of {new Date(ladder.as_of).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST. Phase 0 has PAPER only.</p>
          </div>
        )}
      </div>
    </Card>
  );
}
