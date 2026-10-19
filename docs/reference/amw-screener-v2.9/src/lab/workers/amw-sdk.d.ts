// AMW Strategy SDK typings (TypeScript). Series index length-1 = LAST COMPLETED BAR; missing values are null.
type Series = (number | null)[];
interface Meta { symbol: string; ticker: string; exchange: string; timeframe: string; timezone: string; provider: string;
  data_kind: string; data_status: string; last_completed_bar: string; bars: number; schema_version: number; }
interface Swing { index: number; kind: 'H' | 'L'; label: string; price: number; confirmed_index: number; }
interface StructEvent { index: number; type: 'BOS' | 'CHoCH'; direction: 'bullish' | 'bearish'; level: number; }
interface Zone { kind: 'demand' | 'supply'; name: string; top: number; bottom: number; status: string; }
interface SRLevel { level: number; role: 'support' | 'resistance'; touches: number; }
interface HTF { trend_score: number | null; trend_label: string; structure_trend: string | null; close: number;
  ema20: number | null; ema50: number | null; rsi14: number | null; }
interface Context {
  meta: Meta;
  series: { time: string[]; open: Series; high: Series; low: Series; close: Series; volume: Series; ema20: Series; ema50: Series;
    ema200: Series; sma20: Series; sma50: Series; rsi14: Series; macd: Series; macd_signal: Series; macd_hist: Series; atr14: Series;
    adx: Series; plus_di: Series; minus_di: Series; rvol: Series; vwap: Series; bb_up: Series; bb_low: Series; };
  structure: { trend_score: number | null; trend_label: string; structure_trend: string | null; swings: Swing[];
    events: StructEvent[]; zones: Zone[]; sr: SRLevel[]; };
  htf: { [tf: string]: HTF | null };
  params: { [name: string]: number | boolean | string };
}
interface Rule { name: string; passed: boolean | null; detail?: string; }
interface Result { status: 'qualified' | 'rejected'; bias?: 'long' | 'short' | 'neutral'; classification?: string; rules?: Rule[];
  entry?: string | null; invalidation?: number | null; stop?: number | null; target?: number | null; explanation?: string; }
declare const amw: {
  isna(x: number | null | undefined): boolean;
  last(s: Series, n?: number): number | null;
  prev(s: Series, n?: number): number | null;
  highest(s: Series, n: number): number | null;
  lowest(s: Series, n: number): number | null;
  crossover(a: Series, b: Series): boolean | null;
  crossunder(a: Series, b: Series): boolean | null;
};
declare const console: { log(...args: unknown[]): void };
