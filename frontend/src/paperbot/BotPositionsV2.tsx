import { useState } from "react";
import { Link } from "react-router";
import type { PortfolioExposure, TradeRecord } from "../types";
import { heldRows } from "../trading/model";
import { BUTTON_SECONDARY } from "../trading/PartsV2";
import { Chip } from "../ui/Chip";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { formatINR } from "../ui/format";
import { Signed } from "../ui/Signed";

/**
 * UI-28 U2.4 (owner, 28 Oct): a Trading bot's own open positions on its row - the open P&L (Signed), one chip per leg that
 * opens My positions, and one tap "Exit this bot's positions" with the ₹ impact in its confirm. Exits are never blocked
 * (ADR-0004): it calls the same Exit now as My positions, one position after another, for PAPER positions only.
 */
export function botPositions(positions: readonly TradeRecord[] | null, botId: number): TradeRecord[] {
  return (positions ?? []).filter((p) => p.deployment_id === botId && !p.exit_time);
}

/** The open P&L of the bot's positions; null when any of them has no live price (never a partial sum shown as whole). */
export function botOpenPnl(positions: readonly TradeRecord[], exposure: PortfolioExposure | null): number | null {
  if (!positions.length) return null;
  const rows = heldRows([...positions], exposure);
  if (rows.some((r) => r.pnl === null)) return null;
  return rows.reduce((s, r) => s + (r.pnl as number), 0);
}

export function exitBotConsequence(count: number, pnl: number | null): string {
  const what = `${count} paper position${count === 1 ? "" : "s"}`;
  return pnl === null ? `This closes ${what} at the current price.`
    : `This closes ${what} at the current price. Open P&L now: ${pnl >= 0 ? "+" : "−"}${formatINR(Math.abs(pnl))}.`;
}

export function BotPositionsV2({ positions, exposure, canTrade, onExit }: {
  positions: TradeRecord[]; exposure: PortfolioExposure | null; canTrade: boolean;
  /** Exit every PAPER position of the bot; resolves to the number that closed. */
  onExit: (positions: TradeRecord[]) => Promise<number>;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (!positions.length) return null;
  const pnl = botOpenPnl(positions, exposure);
  const paper = positions.filter((p) => p.mode === "PAPER");
  async function exitAll() {
    setBusy(true);
    try {
      const closed = await onExit(paper);
      setNote(closed === paper.length ? "Closed." : `${closed} of ${paper.length} closed - check My positions.`);
    } finally { setBusy(false); setAsking(false); }
  }
  return (
    <div className="space-y-space-2" data-bot-positions>
      <div className="flex flex-wrap items-center gap-space-2">
        <span className="text-t13 text-fg-muted">Open P&amp;L</span>
        {pnl === null
          ? <Chip tone="warn" variant="meaning" icon="!" name="no-price">Waiting for a live price</Chip>
          : <Signed value={pnl} kind="money" className="text-t15 font-semibold" />}
      </div>
      <p className="flex flex-wrap gap-space-1" data-bot-legs>
        {positions.map((p) => (
          <Link key={p.id} to="/positions" className="rounded-pill focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
            <Chip tone="neutral" variant="meaning" name="bot-leg">{p.symbol}</Chip>
          </Link>
        ))}
      </p>
      {canTrade && paper.length > 0 && (
        <button type="button" className={BUTTON_SECONDARY} onClick={() => setAsking(true)} disabled={busy} data-bot-exit>
          Exit this bot's positions
        </button>
      )}
      {note && <p role="status" className="text-t13 text-fg-muted">{note}</p>}
      <ConfirmDialog open={asking} onOpenChange={setAsking} title="Exit this bot's positions?" busy={busy}
        consequence={exitBotConsequence(paper.length, pnl)} confirmLabel="Exit positions" onConfirm={() => void exitAll()} />
    </div>
  );
}
