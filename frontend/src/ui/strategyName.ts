/**
 * Owner's user-friendly pass (23 Oct, item 6): the ONE place a strategy id becomes the name a trader reads. A slug
 * ("orb_nifty", "vwap_supertrend_5m", "custom:12") is never shown: the screen shows the strategy's proper display name
 * ("Opening Range Breakout · NIFTY 50", "VWAP + Supertrend", "My strategy").
 *
 * Where the name comes from, first match wins:
 * 1. a name the caller passes (a page that already read the Library),
 * 2. the names this browser has read from `GET /api/strategies` (the v2 shell remembers them once per sign-in:
 *    `rememberStrategyNames`; that list includes the user's own saved strategies under their own names),
 * 3. the built-in strategies' names (the same as `backend/app/strategy_engine/*`: `name = ...`),
 * 4. a template id ("template:D2:..."): "Strategy template"; a saved one ("custom:12"): "My strategy",
 *    ("custom_my_breakout": "My breakout"),
 * 5. otherwise the id in words, with the known short forms written out ("orb" -> "Opening Range Breakout", "rsi" ->
 *    "RSI"), the timeframe tail dropped ("_5m", "_d") and an index named in the id moved after a " · ".
 */

/** The built-in strategies, exactly as the backend registry names them. */
export const BUILT_IN_STRATEGY_NAMES: Readonly<Record<string, string>> = {
  swing_ema_pullback_d: "Swing EMA Pullback (daily)",
  swing_breakout_d: "Swing Breakout (daily)",
  macd_ema_trend_5m: "MACD + EMA Trend",
  bb_rsi_reversion_5m: "Bollinger + RSI Reversion",
  vwap_supertrend_5m: "VWAP + Supertrend",
  orb_15m_5m: "Opening Range Breakout",
  ema_rsi_scalper_1m: "EMA + RSI Scalper",
  supertrend_adx_scalper_1m: "Supertrend + ADX Scalper",
  rsi_adx_momentum_5m: "RSI + ADX Momentum Scalper",
  // Owner fix T1-6: orders a person placed himself, in plain words (never "Manual fno").
  manual: "Manual trade",
  manual_fno: "Manual trade",
};

/** Short forms inside an id, written out. */
const WORDS: Readonly<Record<string, string>> = {
  orb: "Opening Range Breakout", ema: "EMA", sma: "SMA", rsi: "RSI", macd: "MACD", vwap: "VWAP", adx: "ADX", atr: "ATR",
  bb: "Bollinger", obv: "OBV", oi: "OI", pcr: "PCR", sr: "Support and resistance", ma: "Moving average",
};

/** Indices named inside an id: shown after a " · ", as the market names them. */
const INDICES: Readonly<Record<string, string>> = {
  nifty: "NIFTY 50", nifty50: "NIFTY 50", banknifty: "NIFTY BANK", finnifty: "NIFTY FIN SERVICE", midcpnifty: "NIFTY MID SELECT",
  sensex: "SENSEX", bankex: "BANKEX",
};

/** A timeframe tail: "1m", "15m", "1h", "d", "daily", "intraday". */
const TIMEFRAME = /^(\d+(m|min|h)|d|day|daily|w|weekly)$/;

const remembered = new Map<string, string>();

/** The v2 shell stores the Library's names once they are read; a blank name is ignored. */
export function rememberStrategyNames(entries: Iterable<readonly [string, string | null | undefined]>): void {
  for (const [id, name] of entries) {
    const clean = (name ?? "").trim();
    if (id && clean) remembered.set(id, clean);
  }
}

/** Tests only: forget what was remembered. */
export function forgetStrategyNames(): void {
  remembered.clear();
}

/** True when the text looks like an internal id rather than a name: lower-case words joined by "_" / ":", or a
 * "custom:" / "template:" id. The guard tests use it on everything a v2 screen prints as a strategy or bot name. */
export function looksLikeSlug(text: string): boolean {
  const t = text.trim();
  if (!t) return false;
  if (/^(custom|template)[:_-]/i.test(t)) return true;
  if (/[_:]/.test(t) && /^[a-z0-9_:\-.]+$/i.test(t)) return true;
  return /^[a-z][a-z0-9]*([_-][a-z0-9]+)+$/.test(t);
}

function wordsOf(id: string): { name: string; index: string | null } {
  const tokens = id.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  let index: string | null = null;
  const out: string[] = [];
  tokens.forEach((tok, i) => {
    if (INDICES[tok]) { index = index ?? INDICES[tok]; return; }
    if (i > 0 && TIMEFRAME.test(tok)) return;
    out.push(WORDS[tok] ?? tok);
  });
  const text = out.join(" ").trim();
  return { name: text ? text.charAt(0).toUpperCase() + text.slice(1) : "", index };
}

/** The name a trader reads for strategy `id`. Never the id itself; "" only for an empty id. `index: false` leaves out an
 * index read from the id (a row that already shows its symbol). */
export function strategyDisplayName(id: string | null | undefined, names?: ReadonlyMap<string, string> | null,
  opts: { index?: boolean } = {}): string {
  const key = (id ?? "").trim();
  if (!key) return "";
  const known = names?.get(key) ?? remembered.get(key) ?? BUILT_IN_STRATEGY_NAMES[key];
  if (known) return known;
  if (/^template:/i.test(key)) {
    const template = key.split(":")[1];
    const named = template ? names?.get(`template:${template}`) ?? remembered.get(`template:${template}`) : undefined;
    return named ?? "Strategy template";
  }
  if (/^custom[:_-]/i.test(key)) {
    const tail = key.replace(/^custom[:_-]+/i, "");
    return /^\d*$/.test(tail) ? "My strategy" : wordsOf(tail).name || "My strategy";   // "custom:12": no name to show
  }
  const { name, index } = wordsOf(key);
  if (!name) return index ?? "My strategy";
  return index && opts.index !== false ? `${name} · ${index}` : name;
}

/** A bot as one line: "Opening Range Breakout · NIFTY 50". The symbol is left out when the name already holds it. */
export function botDisplayName(strategyId: string | null | undefined, symbol?: string | null, names?: ReadonlyMap<string, string> | null): string {
  const name = strategyDisplayName(strategyId, names) || "My strategy";
  const sym = (symbol ?? "").trim();
  if (!sym || name.toUpperCase().includes(sym.toUpperCase())) return name;
  return `${name} · ${sym}`;
}
