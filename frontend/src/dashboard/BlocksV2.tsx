import { ArrowDownRight, ArrowUpRight, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { EMPTY, formatINR, formatPct, formatPrice, formatSignedINR } from "../ui/format";
import { TermHint } from "../ui/TermHint";
import { useColourV2 } from "../ui/colourV2";
import { DirectionChip } from "../ui/DirectionChip";
import { checkTone, toneProblem, toneSurfaceClass, type Toned } from "../ui/tone";
import { CardV2, ChipV2, EmptyStateV2, LoadFailedV2, LoadingRowsV2, toneClass } from "./CardV2";
import { WIN_RATE_DAYS, signTone, type PositionRow, type SignalRow, type TodayTiles } from "./model";
import { Signed } from "../ui/numbersFirst";
import { LiveBadge, PaperBadge } from "../ui/PaperBadge";
import { showsPaper } from "../ui/paperWords";

/**
 * UI2-2 (UI v2 proposal §5 blocks 5-7): Open positions (up to five rows, "View all" to My positions), Active signals (up
 * to three of today's, "View all" to Signals) and the Today summary (four neutral tiles). Lists, never tables, so
 * nothing scrolls sideways on a phone; figures right-aligned in tabular digits.
 */

type Rows<T> = { status: "loading" } | { status: "error" } | { status: "ok"; rows: T[]; total: number };

export function OpenPositionsV2({ data }: { data: Rows<PositionRow> }) {
  const total = data.status === "ok" ? data.total : undefined;
  return (
    <CardV2 title="Open positions" count={total} more={{ label: "View all", to: "/positions" }} testId="positions" tour="dashboard-positions"
      badge={data.status === "ok" && showsPaper(data.rows.map((p) => p.mode)) ? <PaperBadge /> : undefined}>
      {data.status === "loading" ? <LoadingRowsV2 /> : data.status === "error" ? <LoadFailedV2 what="your positions" /> : data.rows.length === 0 ? (
        <EmptyStateV2 text="All positions are closed." action={{ label: "Find setups", to: "/screener" }} />
      ) : (
        <ul className="divide-y divide-subtle">
          {data.rows.map((p, i) => {
            const tone = toneClass(signTone(p.pnl));
            return (
              <li key={p.id} className="flex min-h-12 items-center gap-space-3 py-space-3 first:pt-0 last:pb-0">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-space-2">
                    <span className="truncate text-t15 font-semibold text-fg">{p.symbol}</span>
                    {p.mode === "Live" && <LiveBadge />}
                  </div>
                  <p className="mt-px text-t13 text-fg-muted">{p.detail}</p>
                </div>
                <div className="hidden w-28 text-right sm:block">
                  <div className="flex items-center justify-end gap-space-1 text-t12 text-fg-muted">LTP{i === 0 && <TermHint term="ltp" align="end" />}</div>
                  <div className="font-tabular text-t15 font-medium text-fg">{formatPrice(p.ltp)}</div>
                </div>
                <div className="w-28 shrink-0 text-right">
                  <div className="font-tabular text-t15 font-semibold"><Signed value={p.pnl} /></div>
                  <div className={`font-tabular text-t13 ${tone}`}><Signed value={p.pnlPct} kind="pct" arrow={false} /></div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </CardV2>
  );
}

/** Day-one fix 9: with the ready strategies on (`strategy_templates`), an empty day points to the one-tap Signals watch. */
export const READY_WATCH_TEXT = "Signals show here as they come today.";

export function ActiveSignalsV2({ data, readyWatch = false }: { data: Rows<SignalRow>; readyWatch?: boolean }) {
  // Colour v2: Long / Short as chips (icon + word), "Waiting for you" amber.
  const colour = useColourV2();
  return (
    <CardV2 title="Active signals" more={{ label: "View all", to: "/signals" }} testId="signals" tour="dashboard-signals">
      {data.status === "loading" ? <LoadingRowsV2 /> : data.status === "error" ? <LoadFailedV2 what="today's signals" /> : data.rows.length === 0 ? (
        readyWatch
          ? <EmptyStateV2 text={READY_WATCH_TEXT} action={{ label: "Watch a list on Signals", to: "/signals" }} />
          : <EmptyStateV2 text="Signals show here as they come today." action={{ label: "Set up a scan", to: "/screener/custom" }} />
      ) : (
        <ul className="divide-y divide-subtle">
          {data.rows.map((s) => (
            <li key={s.id} className="flex gap-space-3 py-space-3 first:pt-0 last:pb-0">
              <span className="w-11 shrink-0 pt-px font-tabular text-t13 text-fg-muted">{s.time}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-start gap-space-2">
                  <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-space-2">
                    <span className="min-w-0 text-t15 font-semibold text-fg [overflow-wrap:anywhere]">{s.symbol}</span>
                    {colour ? <DirectionChip side={s.side === "Long" ? "long" : "short"} /> : (
                      <span className={`inline-flex items-center gap-px text-t14 font-medium ${s.side === "Long" ? "text-up" : "text-down"}`}>
                        {s.side === "Long" ? <ArrowUpRight size={14} aria-hidden /> : <ArrowDownRight size={14} aria-hidden />}{s.side}
                      </span>
                    )}
                  </div>
                  <ChipV2 kind={s.waiting ? (colour ? "warn" : "brand") : "outline"}>{s.status}</ChipV2>
                </div>
                {s.setup && <p className="mt-px text-t13 text-fg-muted">{s.setup}</p>}
                <p className="mt-px text-t12 text-fg-muted">{s.source}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </CardV2>
  );
}

/**
 * UI-H CA4: a Today card's soft tone background (colour v2) comes from the shared tone (src/ui/tone.ts) and always with
 * its cue: Booked today carries its + / − sign, Risk in use a warning icon.
 */
function cueOf(tone: "neutral" | "up" | "down" | "warn", value: string): Toned {
  // A figure that rounds to ₹0 shows no sign, so it takes no tint either.
  if ((tone === "up" || tone === "down") && toneProblem("profit", { sign: true }, value)) return {};
  if (tone === "up") return { tone: "profit", cue: { sign: true } };
  if (tone === "down") return { tone: "loss", cue: { sign: true } };
  if (tone === "warn") return { tone: "risk", cue: { icon: TriangleAlert } };
  return {};
}

function Tile({ label, hint, value, tone = "neutral", sub, children }: {
  label: string; hint?: "win-rate" | "risk-in-use"; value: string; tone?: "neutral" | "up" | "down" | "warn"; sub?: string; children?: ReactNode;
}) {
  const colour = useColourV2();
  const toned = colour ? cueOf(tone, value) : {};
  const tinted = checkTone(toned.tone, toned.cue, value);
  const Icon = toned.cue && "icon" in toned.cue ? toned.cue.icon : null;
  return (
    <div className={`min-w-0 ${tinted && toned.tone ? toneSurfaceClass(toned.tone) : "bg-surface-1"} p-space-3 md:p-space-4`} data-tone={toned.tone}>
      <div className="flex items-center gap-space-1 text-t13 text-fg-muted">{label}{hint && <TermHint term={hint} />}</div>
      <div className={`mt-space-1 flex items-center gap-space-1 font-tabular text-t20 font-semibold ${toneClass(tone)}`}>{Icon && <Icon size={18} aria-hidden className="shrink-0" />}{value}</div>
      {sub && <div className="mt-px text-t12 text-fg-muted">{sub}</div>}
      {children}
    </div>
  );
}

export function TodaySummaryV2({ tiles, loading }: { tiles: TodayTiles; loading: boolean }) {
  const risk = tiles.riskUsedPct;
  const signals = tiles.signals;
  const signalsSub = [signals.waiting ? `${signals.waiting} waiting` : "", signals.taken ? `${signals.taken} taken` : ""].filter(Boolean).join(" · ");
  return (
    <CardV2 title="Today" more={{ label: "Performance", to: "/analytics" }} testId="today">
      {loading ? <LoadingRowsV2 rows={2} /> : (
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-control border border-subtle/60 bg-border-subtle/50">
          <Tile label="Booked today" value={tiles.booked === null ? EMPTY : formatSignedINR(tiles.booked, { compact: true })}
            tone={signTone(tiles.booked)} sub={tiles.booked === null ? undefined : "closed trades since the open"} />
          <Tile label="Win rate" hint="win-rate" value={tiles.winRate === null ? EMPTY : `${tiles.winRate.toFixed(0)}%`}
            sub={tiles.closedTrades === null ? undefined : tiles.closedTrades ? `${tiles.closedTrades} closed ${tiles.closedTrades === 1 ? "trade" : "trades"} in ${WIN_RATE_DAYS} days` : `no closed trades in ${WIN_RATE_DAYS} days`} />
          <Tile label="Risk in use" hint="risk-in-use" value={risk === null ? EMPTY : `${Math.round(risk)}%`} tone={risk !== null && risk >= 80 ? "warn" : "neutral"}
            sub={tiles.riskLimit === null ? "no daily limit set" : `${formatINR(tiles.riskUsed, { compact: true })} of ${formatINR(tiles.riskLimit, { compact: true })} daily limit`}>
            {risk !== null && (
              <div aria-hidden className="mt-space-2 h-1 overflow-hidden rounded-pill bg-surface-3">
                <div className={`h-1 rounded-pill ${risk >= 80 ? "bg-warn" : "bg-fg-muted"}`} style={{ width: `${Math.min(100, Math.max(0, risk))}%` }} />
              </div>
            )}
          </Tile>
          <Tile label="Signals today" value={String(signals.count)} sub={signalsSub || (signals.count ? "" : "none yet")} />
        </div>
      )}
    </CardV2>
  );
}

export type { Rows };
