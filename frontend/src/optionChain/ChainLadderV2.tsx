import { useMemo, useState, type ReactNode } from "react";
import { Skeleton } from "../components/primitives";
import { cx } from "../components/primitives/cx";
import { Info, TriangleAlert } from "lucide-react";
import { useColourV2 } from "../ui/colourV2";
import ChainTableV2 from "./ChainTableV2";
import { TEXT, problemText, staleText, type ColumnKey, type ColumnPrefs } from "./model";
import { STRIKE_RANGES, rowsAround } from "./spotChange";
import type { ChainProblem } from "./useOptionChain";
import type { ChainRow, LiveChain } from "./types";

/**
 * TERM-2 step 2 / CT-1 C3 (owner addendum 30 Oct: "the option chain on the Terminal is not good"): THE option chain -
 * Calls | strike | Puts with the ATM row centred, ITM shading, OI and OI-change bars, LTP / change, IV, the optional
 * Greeks and quotes from the columns menu, the "ATM ± N" strike range and the tick flash (ChainTableV2). The Option chain
 * page and the Terminal place this one component; neither keeps a table of its own.
 */
export function ChainLadderV2({ symbol, live, loading, problem, problemReason = "", prefs, centreSuffix = "", onPick, onPickLeg, held, setup, toggle, areaMarks, hide }: {
  symbol: string;
  live: LiveChain | null;
  loading: boolean;
  problem: ChainProblem | null;
  /** REV-1 M2: the server's own reason for `problem` (useOptionChain `problemReason`); "" shows the plain text. */
  problemReason?: string;
  prefs: ColumnPrefs;
  /** The chain trading layer's recentre suffix (a tap on "ATM" recentres). */
  centreSuffix?: string;
  onPick: (row: ChainRow) => void;
  onPickLeg?: (row: ChainRow, side: "call" | "put") => void;
  held?: ReadonlySet<string>;
  setup?: ReadonlyMap<string, string>;
  /** TERM-2 R3 (optional): the distance mark under a short strike (ChainTableV2 `areaMarks`). */
  areaMarks?: ReadonlyMap<string, string>;
  /** The page's own control above the strike column (the chain page's Chain / OI view toggle). */
  toggle?: (placed: "empty" | "above") => ReactNode;
  /** M58 T4-5 (optional): columns left out for a narrow panel (the Terminal's chain: IV, then OI change). */
  hide?: readonly ColumnKey[];
}) {
  const colour = useColourV2();          // colour v2: the Calls / Puts headings in their side's colour
  // UI-28 U7.1: every strike, or ATM ± N (the choice is kept for the visit).
  const [range, setRange] = useState<number>(0);
  const shownLive = useMemo(() => (live && range ? { ...live, rows: rowsAround(live.rows, live.summary?.atm_strike, range) } : live), [live, range]);
  return (
    <>
      {!(live && live.rows.length > 0) && toggle?.("empty")}
      {problem && !live && <Note tone="warn">{problemText(problem, problemReason)}</Note>}
      {loading && !live && !problem && (
        <div aria-busy="true" className="space-y-space-2 rounded-panel border border-subtle bg-surface-1 p-space-4">
          <span className="sr-only">Loading the option chain</span>
          {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-6" />)}
        </div>
      )}
      {live && live.rows.length === 0 && <Note tone="neutral">{TEXT.empty}</Note>}
      {live && live.rows.length > 0 && (
        <>
          <div aria-hidden className="mb-space-2 flex justify-between text-t12 font-semibold uppercase text-fg-muted">
            <span className={colour ? "text-down" : undefined}>Calls</span><span className={colour ? "text-up" : undefined}>Puts</span>
          </div>
          {toggle?.("above")}
          {/* UI-28 polish (owner 30 Oct): one row on a phone - "All / ± 5 / ± 10 / ± 15" after a quiet Strikes label;
              the full names stay in each chip's aria-label. CT-1: a chip never breaks inside; in a narrower place (the
              F&O desk's card on a 360 phone) the chips go to a second row instead. */}
          <div role="group" aria-label="Strikes shown" data-strike-range className="mb-space-2 flex flex-wrap items-center gap-space-2">
            <span aria-hidden className="text-t12 text-fg-muted">Strikes</span>
            {STRIKE_RANGES.map((n) => (
              <button key={n} type="button" aria-pressed={range === n} onClick={() => setRange(n)} aria-label={n === 0 ? "All strikes" : `ATM ± ${n} strikes`}
                className={cx("inline-flex min-h-11 items-center whitespace-nowrap rounded-pill border px-space-3 text-t13 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8",
                  range === n ? "border-brand bg-brand-tint font-semibold text-fg" : "border-border text-fg-muted hover:text-fg")}>
                {n === 0 ? "All" : `± ${n}`}
              </button>
            ))}
          </div>
          <ChainTableV2 live={shownLive ?? live} prefs={prefs} centreKey={`${symbol}|${live.expiry ?? ""}${centreSuffix}`} onPick={onPick}
            onPickLeg={onPickLeg} held={held} setup={setup} areaMarks={areaMarks} hide={hide} />
          {problem && <p data-chain-stale className="mt-space-2 text-t12 text-warn">{staleText(live, problem, problemReason)}</p>}
        </>
      )}
    </>
  );
}

/** A one-line chain note (closed market, a failed read). */
export function Note({ tone, children }: { tone: "neutral" | "warn"; children: string }) {
  const Icon = tone === "warn" ? TriangleAlert : Info;
  return (
    <p role="status" className={`flex items-start gap-space-2 rounded-control px-space-3 py-space-2 text-t13 ${tone === "warn" ? "bg-warn-tint text-warn" : "bg-surface-2 text-fg"}`}>
      <Icon size={16} aria-hidden className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}
