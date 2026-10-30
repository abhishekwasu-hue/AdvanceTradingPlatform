import { useState } from "react";
import { ChainLadderV2 } from "../optionChain/ChainLadderV2";
import ColumnsMenuV2 from "../optionChain/ColumnsMenuV2";
import type { ColumnPrefs } from "../optionChain/model";
import RowDetailsV2 from "../optionChain/RowDetailsV2";
import SummaryStripV2 from "../optionChain/SummaryStripV2";
import type { OptionChainData } from "../optionChain/useOptionChain";
import type { ChainRow } from "../optionChain/types";
import type { TerminalSpot } from "./spot";

/**
 * TERM-2 step 2 (owner addendum 30 Oct): the Terminal's chain panel is the Option chain page's own chain, placed, not
 * copied - the summary strip (spot with its Signed change, ATM, PCR, max pain, ATM IV, days to expiry), the columns menu
 * and THE chain (ChainLadderV2: ChainTableV2 with the ATM ± N strikes), for the context bar's ONE symbol + expiry. A price
 * opens the same action sheet (Buy / Sell / Add to basket / Alert / Chart) through the page's chain trading layer; a
 * strike opens the same row details.
 */
export function TerminalChainV2({ symbol, chain, spot, prevClose, prefs, onPrefs, trading, areaMarks }: {
  symbol: string;
  chain: Pick<OptionChainData, "live" | "loading" | "problem" | "expiry">;
  spot: TerminalSpot | null;
  prevClose: number | null;
  prefs: ColumnPrefs;
  onPrefs: (prefs: ColumnPrefs) => void;
  trading: { onPickLeg?: (row: ChainRow, side: "call" | "put") => void; held: ReadonlySet<string>; centreSuffix: string };
  /** TERM-2 R3 (flag term_strike_area): each short strike's distance to the nearest area. */
  areaMarks?: ReadonlyMap<string, string>;
}) {
  const [picked, setPicked] = useState<number | null>(null);
  const live = chain.live;
  const row = live?.rows.find((r) => r.strike === picked) ?? null;
  return (
    <div className="space-y-space-3" data-terminal-chain data-symbol={symbol} data-expiry={live?.expiry ?? chain.expiry ?? ""}>
      <SummaryStripV2 summary={live?.summary ?? null} expiry={live?.expiry ?? chain.expiry} loading={chain.loading && !live} spot={spot} prevClose={prevClose} dense />
      <div className="flex justify-end"><ColumnsMenuV2 prefs={prefs} onChange={onPrefs} /></div>
      <section aria-label="Chain" data-block="chain" className="relative">
        <ChainLadderV2 symbol={symbol} live={live} loading={chain.loading} problem={chain.problem} prefs={prefs}
          centreSuffix={trading.centreSuffix} onPick={(r) => setPicked(r.strike)} onPickLeg={trading.onPickLeg} held={trading.held} areaMarks={areaMarks} />
      </section>
      <RowDetailsV2 row={row} atm={live?.summary.atm_strike ?? null} underlying={symbol} onClose={() => setPicked(null)} />
    </div>
  );
}
