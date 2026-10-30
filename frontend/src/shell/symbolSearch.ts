import { contractChartUrl, contractsApi, legName, type ContractRow } from "../charting/contracts";
import { searchSymbols, type PickedSymbol } from "../ui/SymbolPicker";
import { chartTabPath, chartsPath } from "./shellRoutes";

/**
 * UX-2 (owner order UX-2, 25 Oct): the header search finds symbols first - indices, shares and F&O contracts - then
 * pages. "nifty 22700 ce" finds that contract (nearest expiry first), "banknifty fut" the futures, "reli" the share;
 * the user's recent symbols come first. Enter opens the chart. Contracts need the `chart_contracts` flag (the search
 * answers 503 without it, and the row is simply left out).
 */

export type SymbolHit =
  | { type: "symbol"; id: string; label: string; detail: string; path: string; pick: PickedSymbol; recent: boolean }
  /** `path`: the contract on the Charts page (ChartV2, CT-1 C1); `href`: the old contract window (kept until the owner retires it). */
  | { type: "contract"; id: string; label: string; detail: string; path: string; href: string };

/** Spoken index names -> the instrument master's underlying. */
const UNDERLYING_ALIASES: Record<string, string> = {
  "NIFTY 50": "NIFTY", "NIFTY50": "NIFTY", "BANK NIFTY": "BANKNIFTY", "NIFTY BANK": "BANKNIFTY", "FIN NIFTY": "FINNIFTY",
  "NIFTY FIN SERVICE": "FINNIFTY", "MIDCAP NIFTY": "MIDCPNIFTY", "MIDCPNIFTY": "MIDCPNIFTY", "NIFTY MIDCAP SELECT": "MIDCPNIFTY",
};

const KIND_WORDS: Record<string, "CE" | "PE" | "FUT"> = { CE: "CE", CALL: "CE", PE: "PE", PUT: "PE", FUT: "FUT", FUTURE: "FUT", FUTURES: "FUT" };

export interface ContractQuery { underlying: string; kind?: "CE" | "PE" | "FUT"; strike?: number }

/** "nifty 22700 ce" -> NIFTY CE near 22700; "banknifty fut" -> BANKNIFTY FUT; "nifty22700ce" too. Only a text with
 * CE / PE / FUT (or call / put / future) is a contract query: "nifty 50" stays the index, "reliance" the share (null). Pure. */
export function parseContractQuery(text: string): ContractQuery | null {
  const spaced = text.toUpperCase().replace(/([A-Z])(\d)/g, "$1 $2").replace(/(\d)(CE|PE|FUT)\b/g, "$1 $2");
  const words = spaced.split(/\s+/).filter(Boolean);
  let kind: ContractQuery["kind"];
  let strike: number | undefined;
  const name: string[] = [];
  for (const w of words) {
    if (KIND_WORDS[w] && !kind) kind = KIND_WORDS[w];
    else if (/^\d+(\.\d+)?$/.test(w) && strike === undefined) {
      if (!name.length) return null;                                  // a strike with no underlying before it
      strike = Number(w);
    }
    else name.push(w);
  }
  if (!name.length || kind === undefined) return null;
  const joined = name.join(" ");
  const underlying = UNDERLYING_ALIASES[joined] ?? joined.replace(/\s+/g, "");
  return { underlying, kind, strike };
}

const symbolHit = (pick: PickedSymbol, recent: boolean): SymbolHit => ({
  type: "symbol", id: `s:${pick.exchange}:${pick.symbol}`, label: pick.symbol, recent,
  detail: [pick.name && pick.name.toUpperCase() !== pick.symbol ? pick.name : null, pick.exchange].filter(Boolean).join(" · "),
  path: `${chartsPath(pick.symbol)}${pick.exchange && pick.exchange !== "NSE" ? `?exchange=${encodeURIComponent(pick.exchange)}` : ""}`,
  pick,
});

export function contractHit(row: ContractRow): SymbolHit {
  return { type: "contract", id: `c:${row.key}`, label: legName({ ...row, qty: 1 }), detail: `${row.kind === "FUT" ? "Future" : "Option"} · lot ${row.lot_size}`,
    path: chartTabPath(row.tradingsymbol, row.key.split(":")[0] || "NFO"), href: contractChartUrl([row.key]) };
}

/** The recent symbols that match the text (all of them for an empty text), as rows. Pure. */
export function recentHits(recent: readonly PickedSymbol[], text: string): SymbolHit[] {
  const q = text.trim().toUpperCase();
  return recent.filter((r) => !q || r.symbol.includes(q) || (r.name ?? "").toUpperCase().includes(q)).map((r) => symbolHit(r, true));
}

/** Recent first, then the search's rows that are not already there. Pure. */
export function mergeHits(recent: readonly SymbolHit[], found: readonly SymbolHit[], max = 8): SymbolHit[] {
  const seen = new Set(recent.map((h) => h.id));
  return [...recent, ...found.filter((h) => !seen.has(h.id))].slice(0, max);
}

/** The server's rows for the text: contracts for a contract query, else indices then shares. Never throws: a failed or
 * switched-off search just adds no rows (the pages still show). */
export async function findSymbols(text: string): Promise<SymbolHit[]> {
  const q = text.trim();
  if (q.length < 2) return [];
  const contract = parseContractQuery(q);
  if (contract) {
    try {
      const res = await contractsApi.search(contract.underlying, { kind: contract.kind, near: contract.strike, limit: 6 });
      return res.contracts.map(contractHit);
    } catch {
      return [];
    }
  }
  return (await searchSymbols(q)).map((p) => symbolHit(p, false));
}
