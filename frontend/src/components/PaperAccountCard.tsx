import { errorSentence } from "../api/errors";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { PaperAccount, PaperLadder } from "../types";
import { Card, StatTile, signClass } from "./ui";
import { criterionProgress, criterionText, signedMoney } from "./paperLadder";
import LadderEvidence, { DisciplineWeeks } from "../progression/LadderEvidence";
import { formatDateTimeIST, formatINR, formatNumber } from "../ui/format";
import { Details, KeyValueTable, Signed } from "../ui/numbersFirst";
import { useUiV2 } from "../ui/useFeatures";
import { CapitalAsk } from "../settings/CapitalAsk";
import { NOT_SET } from "../settings/tradeSettings";

/**
 * Z3-b (beta spec, Phase 0): the signed-in user's own PAPER account - balance, equity, P&L after charges, the
 * PAPER-scope pause - and the progression ladder card (PAPER stage; LIVE is locked in Phase 0). Every number comes from
 * the server, computed from this user's own PAPER trades. Hidden when the `paper_account` flag is off (the API answers 404).
 * N2-1 (flag `ladder_evidence`): the discipline criterion's weekly scores and the "Your figures" block, when sent.
 */
/** PAPER-NR (owner, 1 Nov): the words beside a practice balance below zero. */
const BELOW_ZERO = "below zero - practice only";

/** PAPER-NR (2): a short practice balance never refuses a PAPER entry, so the balance may go below zero - then it is red
 * and says so (v2 pages). */
export function PaperBalanceTile({ balance, v2 }: { balance: number; v2: boolean }) {
  const below = v2 && balance < 0;
  return (
    <StatTile label="Balance" tone={below ? "down" : "default"} valueText={formatNumber(balance, 2)} value={below
      ? <span className="flex flex-col"><span>{formatNumber(balance, 2)}</span>
          <span className="text-t12 font-semibold text-down" data-testid="paper-below-zero">{BELOW_ZERO}</span></span>
      : formatNumber(balance, 2)} />
  );
}

export default function PaperAccountCard({ canTrade }: { canTrade: boolean }) {
  // UX-11 (v2 pages): numbers first - the breakdown as a key-value table and every note under one Details.
  const v2 = useUiV2();
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
    catch (e) { setMessage(errorSentence(e)); }
    finally { setBusy(false); }
  }

  const r = account.realised;
  return (
    <Card title="PAPER account" className="space-y-3">
      <div data-testid="paper-account" className="space-y-3">
        {/* M68 PR-7 (D4): no starting capital chosen yet - ATP pre-fills none; he sets his own here (or at his first order). */}
        {account.capital_needed && canTrade && v2 && <CapitalAsk autoFocus={false} id="paper-account-capital" onSaved={() => load()} />}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {account.balance === null ? <StatTile label="Balance" value={NOT_SET} /> : <PaperBalanceTile balance={account.balance} v2={v2} />}
          <StatTile label="Equity (with open marks)" value={account.equity === null ? NOT_SET : formatNumber(account.equity, 2)} />
          <StatTile label="Net P&L (after charges)" value={v2 ? <Signed value={r.net} kind="count" decimals={2} /> : signedMoney(r.net)} tone={v2 ? "default" : r.net > 0 ? "up" : r.net < 0 ? "down" : "default"} />
          <StatTile label="Closed / open" value={`${account.closed_trades} / ${account.open_positions}`} />
        </div>
{!v2 && <>
        <p className="text-xs text-fg-muted">
          Start {account.starting_capital === null ? NOT_SET : formatNumber(account.starting_capital)} · gross <span className={signClass(r.gross)}>{signedMoney(r.gross)}</span> − charges {formatNumber(r.charges, 2)} = net{" "}
          <span className={signClass(r.net)}>{signedMoney(r.net)}</span> · open marks <span className={signClass(account.unrealised)}>{signedMoney(account.unrealised)}</span> ·
          {" "}proposals: {account.proposals.approved_by_you} approved, {account.proposals.rejected_by_you} rejected, {account.proposals.pending} waiting ·
          {" "}{account.not_taken.length} not taken (would-have records)
        </p>
        <p className="text-xs text-fg-muted">{account.note}{account.excluded_trades > 0 ? ` (${account.excluded_trades} left out.)` : ""}</p>
        </>}

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
            {(account.capital_needed || account.closed_trades + account.open_positions + account.excluded_trades === 0) && !(account.capital_needed && v2) && <>
            <input type="number" min={1} placeholder="Starting capital" value={capital} onChange={(e) => setCapital(e.target.value)}
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
                    {c.key === "discipline_score" && c.status && <DisciplineWeeks criterion={c} />}
                  </li>
                );
              })}
            </ul>
            {ladder.targets_note && <p className="text-xs text-fg-muted" data-testid="ladder-targets-note">{ladder.targets_note}</p>}
            {ladder.evidence && <LadderEvidence evidence={ladder.evidence} />}
            {!v2 && <p className="text-xs text-fg-muted">Figures from your own PAPER trades, as of {formatDateTimeIST(ladder.as_of)}.</p>}
          </div>
        )}

        {v2 && (
          <Details id="mytrades.paper-account">
            <KeyValueTable caption="How the net is worked out" rows={[
              { label: "Starting capital", value: account.starting_capital === null ? NOT_SET : formatINR(account.starting_capital, { paise: true }) },
              ...(account.charges_model ? [{ label: "Charges on a new paper trade", value: account.charges_model.words }] : []),
              { label: "Gross P&L", value: <Signed value={r.gross} paise /> },
              { label: "Charges", value: formatINR(r.charges, { paise: true }) },
              { label: "Net P&L", value: <Signed value={r.net} paise /> },
              { label: "Open marks", value: <Signed value={account.unrealised} paise /> },
              { label: "Proposals approved / rejected / waiting", value: `${account.proposals.approved_by_you} / ${account.proposals.rejected_by_you} / ${account.proposals.pending}` },
              { label: "Not taken (would-have records)", value: account.not_taken.length },
            ]} />
            <p>{account.note}{account.excluded_trades > 0 ? ` (${account.excluded_trades} left out.)` : ""}</p>
            {ladder && <p>Figures from your own PAPER trades, as of {formatDateTimeIST(ladder.as_of)}.</p>}
          </Details>
        )}
      </div>
    </Card>
  );
}
