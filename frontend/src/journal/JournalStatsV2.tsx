import { useState } from "react";
import { CardV2, EmptyStateV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { TilesV2, TileV2 } from "../trading/PartsV2";
import { chipClass } from "../ui/Chip";
import { ErrorState } from "../ui/ErrorState";
import { formatINR, formatNumber, formatPct } from "../ui/format";
import { HelpPopover } from "../ui/HelpPopover";
import { ResponsiveTable, type Column } from "../ui/ResponsiveTable";
import { SubTabs } from "../ui/SubTabs";
import { PnlV2, RV2 } from "./JournalPartsV2";
import type { JournalStatsResponse, StatsGroup } from "./types";

/**
 * M62 JN-3: the statistics of his own journal (the same filters as the list) - win rate, profit factor, expectancy,
 * average win / loss, average R and MAE / MFE where stored, then by setup / timeframe / weekday. Numbers only; how each
 * is worked out sits behind the (?). M63 I-7: each jargon tile (win rate, profit factor, expectancy, R) carries its
 * glossary (?) on first appearance.
 */

export type StatsState = { status: "loading" } | { status: "error"; error: unknown } | { status: "ok"; data: JournalStatsResponse };

type By = "setup" | "timeframe" | "weekday";
type Row = StatsGroup & { name: string };

const tone = (v: number | null | undefined) => (v == null || v === 0 ? "neutral" : v > 0 ? "up" : "down") as "neutral" | "up" | "down";
const signedMoney = (v: number | null) => (v == null ? "–" : `${v > 0 ? "▲ +" : v < 0 ? "▼ " : ""}${formatINR(v)}`);
const rText = (v: number | null) => (v == null ? "–" : `${formatNumber(v, 2)}R`);
/** A coloured R figure carries its own ▲ / ▼ (never colour alone). */
const signedR = (v: number | null) => (v == null ? "–" : `${v > 0 ? "▲ +" : v < 0 ? "▼ " : ""}${formatNumber(v, 2)}R`);
const winRate = (v: number | null) => (v == null ? "–" : formatPct(v, 1).replace(/^\+/, ""));
const pf = (v: number | null) => (v == null ? "–" : formatNumber(v, 2));

const COLUMNS: readonly Column<Row>[] = [
  { key: "name", header: "Group", cell: (r) => r.name },
  { key: "trades", header: "Trades", align: "right", cell: (r) => `${formatNumber(r.closed)} / ${formatNumber(r.trades)}` },
  { key: "win", header: "Win rate", align: "right", cell: (r) => winRate(r.win_rate) },
  { key: "net", header: "Net P&L", align: "right", cell: (r) => <PnlV2 value={r.net_pnl} /> },
  { key: "pf", header: "Profit factor", align: "right", cell: (r) => pf(r.profit_factor) },
  { key: "exp", header: "Expectancy", align: "right", cell: (r) => <PnlV2 value={r.expectancy} /> },
  { key: "r", header: "Avg R", align: "right", cell: (r) => <RV2 value={r.avg_r} /> },
];

export function StatsTilesV2({ t }: { t: StatsGroup }) {
  return (
    <TilesV2 columns={4}>
      <TileV2 label="Win rate" hint="win-rate" value={winRate(t.win_rate)} sub={`${formatNumber(t.wins)} of ${formatNumber(t.closed)} closed`} testId="win-rate" />
      <TileV2 label="Profit factor" hint="profit-factor" value={pf(t.profit_factor)} sub={`${formatINR(t.gross_win)} / ${formatINR(Math.abs(t.gross_loss))}`} testId="profit-factor" />
      <TileV2 label="Expectancy" hint="expectancy" value={signedMoney(t.expectancy)} tone={tone(t.expectancy)} sub="per closed trade" testId="expectancy" />
      <TileV2 label="Net P&L" value={signedMoney(t.net_pnl)} tone={tone(t.net_pnl)} sub={`${formatNumber(t.trades)} trades · ${formatNumber(t.open)} open`} testId="net" />
      <TileV2 label="Average win" value={signedMoney(t.avg_win)} tone={tone(t.avg_win)} testId="avg-win" />
      <TileV2 label="Average loss" value={signedMoney(t.avg_loss)} tone={tone(t.avg_loss)} testId="avg-loss" />
      <TileV2 label="Average R" hint="r-multiple" value={signedR(t.avg_r)} tone={tone(t.avg_r)} sub={`${formatNumber(t.r_trades)} with a stop`} testId="avg-r" />
      <TileV2 label="MAE / MFE" value={t.excursion_trades ? `${rText(t.avg_mae_r)} / ${rText(t.avg_mfe_r)}` : "–"}
        sub={t.excursion_trades ? `${formatINR(t.avg_mae)} / ${formatINR(t.avg_mfe)} · ${formatNumber(t.excursion_trades)} trades` : "not stored yet"} testId="mae-mfe" />
    </TilesV2>
  );
}

export function JournalStatsV2({ state, onRetry }: { state: StatsState; onRetry: () => void }) {
  const [by, setBy] = useState<By>("setup");
  if (state.status === "loading") return <CardV2 title="Statistics" testId="journal-stats"><LoadingRowsV2 rows={4} /></CardV2>;
  if (state.status === "error") return <CardV2 title="Statistics" testId="journal-stats"><ErrorState message={state.error} onRetry={onRetry} variant="inline" /></CardV2>;
  const d = state.data;
  const groups: Row[] = (by === "setup" ? d.by_setup.map((g) => ({ ...g, name: g.setup }))
    : by === "timeframe" ? d.by_timeframe.map((g) => ({ ...g, name: g.timeframe }))
      : d.by_weekday.map((g) => ({ ...g, name: g.weekday })));
  const help = (
    <HelpPopover title="How these are worked out">
      <ul className="list-disc space-y-space-1 pl-space-4">
        <li>Win rate: closed trades with a profit, out of all closed trades.</li>
        <li>Profit factor: the total of the profits divided by the total of the losses (none without a loss).</li>
        <li>Expectancy: the net P&amp;L per closed trade, after charges.</li>
        <li>R: the net P&amp;L divided by the amount at risk from entry to the first stop.</li>
        <li>MAE / MFE: the furthest the price went against / for the trade while open, where it was stored.</li>
        <li>A multi-leg structure counts once. Weekdays are India time.</li>
      </ul>
    </HelpPopover>
  );
  return (
    <div className="space-y-space-4" data-journal-stats-v2>
      <CardV2 title={<span className="inline-flex items-center gap-space-1">Statistics{help}</span>} testId="journal-stats"
        badge={d.totals.outside_atp ? <span className={chipClass({ tone: "warn" })} data-outside-count>{formatNumber(d.totals.outside_atp)} outside ATP</span> : undefined}>
        {d.totals.trades === 0 ? <EmptyStateV2 text="No trades match these filters." /> : <StatsTilesV2 t={d.totals} />}
      </CardV2>
      {d.totals.trades > 0 && (
        <CardV2 title="Breakdown" testId="journal-breakdown">
          <SubTabs label="Break down by" value={by} onChange={setBy} size="sm"
            items={[{ key: "setup", label: "Setup" }, { key: "timeframe", label: "Timeframe" }, { key: "weekday", label: "Weekday" }]} />
          <ResponsiveTable caption={`By ${by}`} columns={COLUMNS} rows={groups} rowKey={(r) => r.name} phone="stack" className="mt-space-3" />
        </CardV2>
      )}
    </div>
  );
}
