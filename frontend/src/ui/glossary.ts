/**
 * UI2-0 (UI v2 proposal §9 rule 1): the one glossary behind every "?" tooltip on v2 screens. Each entry is the term as
 * the screen shows it and one or two plain-English sentences a new trader understands - no developer wording, no
 * advice. A page names a key (`glossary("pcr")`), never its own copy of the text, so a definition is fixed in one place.
 */
export interface GlossaryEntry {
  /** The term exactly as it appears on screen. */
  term: string;
  /** Plain English, at most two sentences. */
  definition: string;
}

export const GLOSSARY = {
  atm: { term: "ATM", definition: "At the money: the strike price closest to the current price of the index or stock." },
  atr: { term: "ATR", definition: "Average true range: the average size of one bar's move from high to low over recent bars." },
  basis: { term: "Basis", definition: "The gap between the price of a future and the price of the index or stock itself." },
  delta: { term: "Delta", definition: "How much an option's price moves when the index or stock moves by one point." },
  expectancy: { term: "Expectancy", definition: "The average result per closed trade: wins and losses added together, divided by the number of trades." },
  gamma: { term: "Gamma", definition: "How much an option's delta changes when the index or stock moves by one point." },
  kelly: { term: "Kelly reference", definition: "A formula's reading of your past wins and losses: the share of capital per losing trade that grew them fastest. It is not a position size." },
  "profit-factor": { term: "Profit factor", definition: "Money made on winning trades divided by money lost on losing trades. Above 1 means the wins added up to more." },
  "r-multiple": { term: "R", definition: "One R is the amount a trade risks from its entry to its stop. A result of 2R is twice that amount." },
  "risk-of-ruin": { term: "Risk of ruin", definition: "The chance, worked out from your past results, of the account ever falling a set amount below its start." },
  theta: { term: "Theta", definition: "How much an option's price changes in one day from time passing alone, with everything else the same." },
  vega: { term: "Vega", definition: "How much an option's price changes when implied volatility moves by one point." },
  breakeven: { term: "Breakeven", definition: "A price of the index or stock at expiry where the strategy ends with neither a profit nor a loss." },
  dte: { term: "DTE", definition: "Days to expiry: how many days are left until the contract's expiry." },
  margin: { term: "Margin", definition: "The money your broker blocks in your account while you hold a futures or options position." },
  "oi-change": { term: "OI change", definition: "How much open interest rose or fell since an earlier reading. A rise means contracts were added; a fall means some were closed." },
  pop: { term: "Probability of profit", definition: "A model's estimate of the chance that the strategy ends with a profit at expiry. It is an estimate, not a promise." },
  premium: { term: "Net premium", definition: "The price of an option is its premium. Net premium is what the legs collect minus what they pay." },
  sl: { term: "SL", definition: "A trigger order with a price: once the price reaches your trigger price, a limit order at your limit price is sent." },
  "sl-m": { term: "SL-M", definition: "A trigger order at market: once the price reaches your trigger price, a market order is sent." },
  "api-key": { term: "API key", definition: "A key your broker gives you so this app can read your account and prices. You create it on your broker's website." },
  "booked-pnl": { term: "Booked P&L", definition: "Profit or loss on trades you have already closed today. It no longer changes with the price." },
  cnc: { term: "CNC", definition: "Cash and carry: a delivery trade in shares that you can hold beyond today." },
  "cool-down": { term: "Cool-down", definition: "A wait after a stop-out before a new entry in the same symbol is allowed." },
  "daily-loss-limit": { term: "Daily loss limit", definition: "The most you can lose in one day. Once your losses reach it, new trades pause until the next day." },
  drawdown: { term: "Drawdown", definition: "How far your account fell from its highest point before it recovered." },
  ema: { term: "EMA", definition: "Exponential moving average: the average closing price of recent bars, with the newest bars counting most." },
  expiry: { term: "Expiry", definition: "The last day a futures or options contract trades. After it, the contract settles and closes." },
  greeks: { term: "Greeks", definition: "Delta, gamma, theta and vega: how an option's price moves with the index or stock, time and volatility. Worked out from its last price." },
  "gross-exposure": { term: "Gross exposure", definition: "The value of everything you hold, with long and short positions added together." },
  "india-vix": { term: "India VIX", definition: "A measure of how much the market expects the main NSE index to move over the next 30 days. Higher means more nervous." },
  iv: { term: "IV", definition: "Implied volatility: how much movement option prices are expecting. Higher IV makes options more expensive." },
  lot: { term: "Lot", definition: "The fixed number of units in one futures or options contract. You trade in whole lots." },
  ltp: { term: "LTP", definition: "Last traded price: the price of the most recent trade." },
  "max-loss": { term: "Max loss", definition: "The most this trade can lose if the price reaches your stop, including costs." },
  "max-pain": { term: "Max pain", definition: "The strike at which option holders as a whole would lose the most at expiry. Some traders watch it near expiry." },
  mis: { term: "MIS", definition: "Intraday trade: your broker closes it before the market closes on the same day." },
  nrml: { term: "NRML", definition: "A futures or options position you can hold overnight until expiry." },
  oi: { term: "OI", definition: "Open interest: the number of futures or options contracts still open. Rising OI means new positions are being added." },
  "open-pnl": { term: "Open P&L", definition: "Profit or loss on positions you still hold. It changes with the price until you close them." },
  paper: { term: "Paper", definition: "Practice trading with pretend money at real prices. No order reaches your broker." },
  pcr: { term: "PCR", definition: "Put-call ratio: open interest in puts divided by open interest in calls. Above 1 means more puts are open than calls." },
  "redirect-url": { term: "Redirect URL", definition: "The address your broker sends you back to after you log in. It must match the one in your broker app exactly." },
  "risk-in-use": { term: "Risk in use", definition: "How much of today's loss limit your losses so far have used. At 100% new trades pause until the next day." },
  "risk-at-stops": { term: "Risk at stops", definition: "What you would lose if every open position reached its stop now." },
  "reward-risk": { term: "R:R", definition: "Reward to risk: how much the trade can make at its target for every rupee it can lose at its stop." },
  rsi: { term: "RSI", definition: "Relative strength index: a line from 0 to 100 that shows how strongly the price moved lately. Above 70 or below 30 is a stretched move." },
  session: { term: "Broker session", definition: "Your login with your broker for today. Brokers end it every trading day, so you log in again each morning." },
  sma: { term: "SMA", definition: "Simple moving average: the average closing price of the last bars, each bar counting the same." },
  "static-ip": { term: "Static IP", definition: "A fixed internet address. Indian brokers require orders from apps to come from an address you registered with them." },
  stop: { term: "Stop", definition: "The price at which a trade is closed to limit the loss." },
  strike: { term: "Strike", definition: "The price at which an option can be exercised." },
  target: { term: "Target", definition: "The price at which a trade is closed to take the profit." },
  "todays-pnl": { term: "Today's P&L", definition: "Your profit or loss since the market opened: what you booked on closed trades plus what open positions show now." },
  totp: { term: "TOTP", definition: "A six-digit code that changes every 30 seconds, from an authenticator app. Some brokers need it to log in." },
  volume: { term: "Volume", definition: "How many shares or contracts traded in each bar. For an index, it is the volume of its near-month future." },
  vwap: { term: "VWAP", definition: "Volume-weighted average price: the average price of the day, weighted by how much traded at each price." },
  "win-rate": { term: "Win rate", definition: "The share of your closed trades that made a profit." },
} as const satisfies Record<string, GlossaryEntry>;

export type GlossaryKey = keyof typeof GLOSSARY;

/** The entry for a key. Typed, so a page cannot name a term that has no definition. */
export function glossary(key: GlossaryKey): GlossaryEntry {
  return GLOSSARY[key];
}
