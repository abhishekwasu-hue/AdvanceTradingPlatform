import { useMemo } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { CardV2, EmptyStateV2, LoadFailedV2, LoadingRowsV2, toneClass } from "../dashboard/CardV2";
import { signTone } from "../dashboard/model";
import { dataOf, usePolled } from "../dashboard/useDashboardData";
import type { PageProps } from "../routes";
import { formatINR, formatNumber, formatSignedINR } from "../ui/format";
import { PageIntro } from "../ui/PageIntro";
import { allocationRows, bookedCurve, longShortLine, portfolioTiles, type AllocationRow } from "./model";
import { NoteV2, PageTitleV2, ShareBarV2, StateChipV2, TileV2, TilesV2 } from "./PartsV2";

const REFRESH_MS = 30_000;
const every = (ms: number) => () => ms;

/**
 * UI2-9a (UI v2 proposal §2 row 16, §5 "Exposure by symbol -> Portfolio", §8, §9): My portfolio - "Where is my money?".
 * Shown only while `ui_v2` is on (the v2 shell picks it for /portfolio, behind the broker gate); with the flag off
 * today's PortfolioPage renders exactly as before.
 *
 * Top to bottom: four tiles (money in open positions, open P&L, booked P&L, risk at the stops), the exposure's own
 * warnings, where the money sits by symbol (a list with each symbol's share of capital, never a table), and the booked
 * P&L over time. Read-only: it reads the existing /positions, /trades and /portfolio/exposure answers and changes nothing.
 */
export default function PortfolioV2(_props: PageProps = {}) {
  const { user } = useAuth();
  const signedIn = !!user;
  const [positions] = usePolled(api.listPositions, every(REFRESH_MS), signedIn);
  const [trades] = usePolled(api.listTrades, every(REFRESH_MS * 2), signedIn);
  const [exposure] = usePolled(() => api.portfolioExposure(true), every(REFRESH_MS), signedIn);

  const pos = dataOf(positions);
  const exp = dataOf(exposure);
  const done = dataOf(trades);
  const tiles = portfolioTiles(pos, done, exp);
  const rows = useMemo(() => allocationRows(pos, exp), [pos, exp]);
  const curve = useMemo(() => bookedCurve(done ?? []), [done]);
  const closedCount = (done ?? []).filter((t) => t.exit_time !== null).length;
  const firstLoad = positions.status === "loading" || exposure.status === "loading";
  const allocationFailed = exposure.status === "error" && positions.status === "error";

  if (!signedIn) return null;

  return (
    <div className="mx-auto w-full space-y-space-4 md:space-y-space-6" data-page="portfolio-v2">
      <PageIntro id="portfolio" lines={[
        "Where your money is: how much sits in open positions, in which symbols, and what you have booked so far.",
        "Each symbol shows its share of your capital, its open profit or loss and what it would lose at its stop.",
      ]} />
      <PageTitleV2 title="My portfolio" subtitle="Where your money is right now, and what you have booked so far." />

      <section aria-label="Summary" data-block="summary">
        {firstLoad ? <LoadingRowsV2 rows={2} /> : (
          <TilesV2>
            <TileV2 testId="invested" label="In open positions" hint="gross-exposure" value={formatINR(tiles.invested, { compact: true })}
              sub={tiles.investedPct === null ? undefined : `${formatNumber(tiles.investedPct, 0)}% of capital`} />
            <TileV2 testId="open-pnl" label="Open P&L" hint="open-pnl" value={formatSignedINR(tiles.openPnl, { compact: true })} tone={signTone(tiles.openPnl)}
              sub={tiles.openPnl === null && (pos?.length ?? 0) > 0 ? "waiting for live prices" : undefined} />
            <TileV2 testId="booked" label="Booked so far" value={formatSignedINR(tiles.booked, { compact: true })} tone={signTone(tiles.booked)}
              sub={done ? `${closedCount} closed ${closedCount === 1 ? "trade" : "trades"}` : undefined} />
            <TileV2 testId="risk" label="Risk at stops" hint="risk-at-stops" value={formatINR(tiles.riskAtStops, { compact: true })}
              sub={tiles.riskPct === null ? undefined : `${formatNumber(tiles.riskPct, 1)}% of capital`} />
          </TilesV2>
        )}
      </section>

      {exp && exp.warnings.length > 0 && (
        <NoteV2 kind="warn" testId="exposure-warnings">
          {exp.warnings.length === 1 ? exp.warnings[0] : (
            <span className="block space-y-space-1">{exp.warnings.map((w) => <span key={w} className="block">{w}</span>)}</span>
          )}
        </NoteV2>
      )}

      <CardV2 title="Where your money is" count={rows.length} testId="allocation">
        {firstLoad ? <LoadingRowsV2 /> : allocationFailed ? <LoadFailedV2 what="where your money is" /> : rows.length === 0 ? (
          <EmptyStateV2 text="Nothing is invested right now. Open positions show here with their share of your capital." action={{ label: "Find setups", to: "/screener" }} />
        ) : (
          <>
            {exp?.price_source === "entry" && (
              <p className="mb-space-3 text-t13 text-fg-muted">Valued at entry prices: live prices are not available right now.</p>
            )}
            {!exp && <p className="mb-space-3 text-t13 text-fg-muted">Shares of the money in open positions, at entry prices.</p>}
            <ul className="divide-y divide-subtle">
              {rows.map((r) => <AllocationRowV2 key={r.symbol} row={r} />)}
            </ul>
            {longShortLine(exp) && <p className="mt-space-3 font-tabular text-t13 text-fg-muted" data-long-short>{longShortLine(exp)}</p>}
          </>
        )}
      </CardV2>

      <CardV2 title="Booked P&L over time" testId="booked">
        {trades.status === "loading" ? <LoadingRowsV2 /> : trades.status === "error" ? <LoadFailedV2 what="your closed trades" /> : curve.length < 2 ? (
          <EmptyStateV2 text="Your booked profit and loss builds up here once you have closed two trades." />
        ) : (
          <BookedChartV2 curve={curve} />
        )}
      </CardV2>

      <p className="text-t13 text-fg-muted">
        Limits on total exposure and on how much sits in one symbol are set on{" "}
        <Link to="/risk-management" className="font-medium text-brand hover:underline">Risk limits</Link>.
      </p>
    </div>
  );
}

function AllocationRowV2({ row }: { row: AllocationRow }) {
  return (
    <li className="py-space-3 first:pt-0 last:pb-0" data-symbol={row.symbol}>
      <div className="flex items-start gap-space-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-space-2">
            <span className="text-t15 font-semibold text-fg">{row.symbol}</span>
            {row.concentrated && <StateChipV2 tone="warn">Concentrated</StateChipV2>}
          </div>
          <p className="mt-px text-t13 text-fg-muted">
            {row.positions} {row.positions === 1 ? "position" : "positions"}{row.strategies ? ` · ${row.strategies}` : ""}
          </p>
        </div>
        <div className="w-32 shrink-0 text-right">
          <div className="font-tabular text-t15 font-semibold text-fg">{formatINR(row.amount, { compact: true })}</div>
          <div className={`font-tabular text-t13 ${row.concentrated ? "text-warn" : "text-fg-muted"}`}>
            {formatNumber(row.pct, row.pct < 10 ? 1 : 0)}% of {row.pctOf === "capital" ? "capital" : "invested"}
          </div>
        </div>
      </div>
      <ShareBarV2 pct={row.pct} warn={row.concentrated} />
      {(row.unrealised !== null || row.risk !== null) && (
        <p className="mt-space-2 font-tabular text-t13 text-fg-muted">
          {row.unrealised !== null && <>Open P&amp;L <span className={toneClass(signTone(row.unrealised))}>{formatSignedINR(row.unrealised)}</span></>}
          {row.unrealised !== null && row.risk !== null && " · "}
          {row.risk !== null && <>Loss at the stop <span className="text-fg">{formatINR(row.risk)}</span></>}
        </p>
      )}
    </li>
  );
}

/** The running total of booked P&L: a line in the direction colour of where it ends, a dashed zero line when it crosses. */
function BookedChartV2({ curve }: { curve: number[] }) {
  const width = 720;
  const height = 200;
  const pad = 12;
  const min = Math.min(0, ...curve);
  const max = Math.max(0, ...curve);
  const range = max - min || 1;
  const x = (i: number) => pad + (i / (curve.length - 1)) * (width - pad * 2);
  const y = (v: number) => height - pad - ((v - min) / range) * (height - pad * 2);
  const path = curve.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = curve[curve.length - 1];
  const colour = last >= 0 ? "rgb(var(--up))" : "rgb(var(--down))";
  return (
    <figure>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img"
        aria-label={`Booked P&L over ${curve.length} closed trades, now ${formatSignedINR(last)}`}>
        <line x1={pad} x2={width - pad} y1={y(0)} y2={y(0)} style={{ stroke: "rgb(var(--border))" }} strokeDasharray="4 4" strokeWidth={1} />
        <path d={path} fill="none" style={{ stroke: colour }} strokeWidth={2} strokeLinejoin="round" />
      </svg>
      <figcaption className="mt-space-2 flex flex-wrap justify-between gap-space-2 font-tabular text-t13 text-fg-muted">
        <span>{curve.length} closed trades</span>
        <span>Lowest {formatSignedINR(Math.min(...curve), { compact: true })} · highest {formatSignedINR(Math.max(...curve), { compact: true })}</span>
        <span>Now <span className={toneClass(signTone(last))}>{formatSignedINR(last)}</span></span>
      </figcaption>
    </figure>
  );
}

