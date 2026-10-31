/**
 * UI2-0 (UI v2 proposal §8 "Cards and numbers"): the one number and time formatter for v2 screens. It replaces the six
 * copied money() / pct() helpers as each page moves to v2 (UI2-9); today's pages keep theirs until then.
 *
 * Rules:
 * - Indian digit grouping (1,24,500.00): the last three digits, then pairs. Written out here, not left to
 *   `toLocaleString("en-IN")`, so the output never depends on the browser's or Node's locale data.
 * - A negative number uses the true minus sign "−" (U+2212), never a hyphen.
 * - A missing or non-finite value is shown as an en dash "–", never as 0 and never as a made-up figure.
 * - Times are India Standard Time (UTC+05:30, no daylight saving) whatever the device's time zone. A timestamp
 *   without a zone (the backend writes UTC) is read as UTC.
 * - Every figure is set in `font-tabular` and right-aligned in tables (the caller's class, not this module's).
 */

export const MINUS = "−";
export const EMPTY = "–";
export const RUPEE = "₹";

type Num = number | null | undefined;

function finite(v: Num): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Indian grouping of a non-negative whole-number string: "12450000" -> "1,24,50,000". */
function groupIndian(digits: string): string {
  if (digits.length <= 3) return digits;
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  return `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${last3}`;
}

/** |v| with `decimals` places and Indian grouping, no sign: 124500 -> "1,24,500.00". From 1e21 up `toFixed` writes an
 * exponent ("1e+21"), so those whole numbers are written out through BigInt (their decimals are zero at that size). */
function magnitude(v: number, decimals: number): string {
  let fixed = Math.abs(v).toFixed(decimals);
  if (/e/i.test(fixed)) {
    const whole = BigInt(Math.round(Math.abs(v))).toString();
    fixed = decimals > 0 ? `${whole}.${"0".repeat(decimals)}` : whole;
  }
  const [whole, frac] = fixed.split(".");
  return frac === undefined ? groupIndian(whole) : `${groupIndian(whole)}.${frac}`;
}

/** True when `v` rounds to zero at `decimals` places (so "−0.00" is never shown). */
function roundsToZero(v: number, decimals: number): boolean {
  return Number(Math.abs(v).toFixed(decimals)) === 0;
}

/** A plain number with Indian grouping: formatNumber(1234567.8, 1) -> "12,34,567.8"; negatives get "−".
 * `trim` (M63 I-7) makes `decimals` an upper bound and drops trailing zeros - "at most N places", what
 * `toLocaleString("en-IN", { maximumFractionDigits: N })` showed: formatNumber(24812.5, 2, { trim: true }) -> "24,812.5",
 * formatNumber(24812, 2, { trim: true }) -> "24,812". */
export function formatNumber(v: Num, decimals = 0, opts: { trim?: boolean } = {}): string {
  if (!finite(v)) return EMPTY;
  const sign = v < 0 && !roundsToZero(v, decimals) ? MINUS : "";
  const body = magnitude(v, decimals);
  return `${sign}${opts.trim ? trimZeros(body) : body}`;
}

/** Trailing zeros of a decimal off: "2.40" -> "2.4", "3.00" -> "3". */
function trimZeros(s: string): string {
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}

/**
 * M61 OI-1: THE formatter for every OI / volume figure in the app (chain table, OI history, chips, tooltips, chart axes,
 * the PCR tables): under 1 lakh in Indian grouping ("99,999"), from 1 lakh "12.35 L", from 1 crore "1.24 Cr"; trailing
 * zeros dropped ("12.3 L", "1 Cr"); negatives with "−". The unit follows the true value, so 99,99,999 is "99.99 L",
 * never "100 L". A missing value is "–".
 */
export function formatCount(v: Num): string {
  if (!finite(v)) return EMPTY;
  const abs = Math.abs(v);
  const whole = Math.round(abs);
  const sign = v < 0 && whole !== 0 ? MINUS : "";
  if (abs >= 1e7) return `${sign}${trimZeros(magnitude(abs / 1e7, 2))} Cr`;
  if (abs >= 1e5) return `${sign}${trimZeros(Math.min(Number((abs / 1e5).toFixed(2)), 99.99).toFixed(2))} L`;
  return `${sign}${magnitude(whole, 0)}`;
}

/** M61 OI-1: a change in a count, always signed: "+21.6 L", "−4,500", "0" (a true zero, or one that rounds to 0). */
export function formatCountChange(v: Num): string {
  if (!finite(v)) return EMPTY;
  const text = formatCount(v);
  return v > 0 && Math.round(v) !== 0 ? `+${text}` : text;
}

/**
 * The money rule (owner's user-friendly pass, 23 Oct): one formatter, two precisions.
 * - Summaries, cards, tiles and sentences show WHOLE rupees with Indian grouping: formatINR(123456.4) -> "₹1,23,456".
 * - Paise only where they matter - order tickets, fills and the P&L detail of a closed trade - and only when the caller
 *   asks for them: formatINR(123456.4, { paise: true }) -> "₹1,23,456.40".
 * - `compact` (narrow tiles): whole rupees up to one crore, then "₹2.4 Cr" - the lakh shorthand is not used, so a
 *   card reads "₹1,23,456" and never "₹1.23 L".
 * Negatives use "−" ("−₹700"); a figure that rounds to zero is never signed.
 */
export interface MoneyOptions {
  /** Two decimals: order tickets, fills and P&L detail rows only. */
  paise?: boolean;
  /** Narrow tiles: "₹2.4 Cr" from one crore up. Ignored with `paise`. */
  compact?: boolean;
}

export function formatINR(v: Num, opts: MoneyOptions = {}): string {
  if (!finite(v)) return EMPTY;
  const decimals = opts.paise ? 2 : 0;
  const abs = Math.abs(v);
  let body: string;
  if (opts.compact && !opts.paise && Math.round(abs) >= 1e7) body = `${trimZeros(magnitude(abs / 1e7, 2))} Cr`;   // 1,00,000 Cr and up stay grouped
  else body = magnitude(abs, decimals);
  const sign = v < 0 && !roundsToZero(v, decimals) ? MINUS : "";
  return `${sign}${RUPEE}${body}`;
}

/** Signed rupees, for P&L: "+₹3,120" / "−₹700" ({ paise: true }: "+₹3,120.50"). Zero after rounding is "₹0": neither
 * profit nor loss. */
export function formatSignedINR(v: Num, opts: MoneyOptions = {}): string {
  if (!finite(v)) return EMPTY;
  const plain = formatINR(v, opts);
  if (plain.startsWith(MINUS)) return plain;
  return roundsToZero(v, opts.paise ? 2 : 0) ? plain : `+${plain}`;
}

/** The decimals a tick size implies: 0.05 -> 2, 0.0025 -> 4, 1 -> 0. A missing or odd tick means 2 (NSE equity). */
export function tickDecimals(tick: Num): number {
  if (!finite(tick) || tick <= 0) return 2;
  for (let d = 0; d <= 6; d += 1) {
    if (Math.abs(Math.round(tick * 10 ** d) - tick * 10 ** d) < 1e-9) return d;
  }
  return 6;
}

/** A price: formatPrice(24812.35, 0.05) -> "24,812.35". Decimals follow the tick size. */
export function formatPrice(v: Num, tick: Num = 0.05): string {
  return formatNumber(v, tickDecimals(tick));
}

/** A signed price change: "+12.35" / "−8.20" (zero unsigned). */
export function formatChange(v: Num, tick: Num = 0.05): string {
  if (!finite(v)) return EMPTY;
  const d = tickDecimals(tick);
  const plain = formatNumber(v, d);
  return v > 0 && !roundsToZero(v, d) ? `+${plain}` : plain;
}

/** A percentage given in percent units: formatPct(0.48) -> "+0.48%", formatPct(-1.2) -> "−1.20%", 0 -> "0.00%". */
export function formatPct(v: Num, decimals = 2): string {
  if (!finite(v)) return EMPTY;
  const plain = formatNumber(v, decimals);
  return `${v > 0 && !roundsToZero(v, decimals) ? "+" : ""}${plain}%`;
}

/**
 * A position size: formatLots(150, 75) -> "2 lots (150)", formatLots(75, 75) -> "1 lot (75)".
 * Equity (no lot size, or a lot of 1): formatLots(50) -> "50 shares", "1 share".
 * A quantity that is not a whole number of lots shows the fraction: formatLots(100, 75) -> "1.33 lots (100)".
 */
export function formatLots(qty: Num, lotSize?: Num): string {
  if (!finite(qty)) return EMPTY;
  const q = Math.abs(qty);
  // The plural follows the number as shown: 74.9999 shares of a 75 lot is "1 lot (75)", never "1 lots".
  if (!finite(lotSize) || lotSize <= 1) {
    const shares = formatNumber(q, 0);
    return `${shares} ${shares === "1" ? "share" : "shares"}`;
  }
  const shown = trimZeros((q / lotSize).toFixed(2));
  return `${shown} ${shown === "1" ? "lot" : "lots"} (${formatNumber(q, 0)})`;
}

// --- Times, always in IST -------------------------------------------------------------------------------------------

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

type When = Date | string | number | null | undefined;

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
// "2026-10-08T05:12:00", "2026-10-08 05:12:00.123456", with an optional zone ("Z", "+05:30", "+0530").
const DATE_TIME = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)([zZ]|[+-]\d{2}:?\d{2})?$/;

/** The instant as epoch milliseconds. A date and time without a zone - "T" or a space between them - is UTC (what
 * the backend writes); a bare date is midnight IST. Never read in the device's own zone. NaN when unreadable. */
function instant(v: When): number {
  if (v === null || v === undefined || v === "") return Number.NaN;
  if (v instanceof Date) return v.getTime();
  if (typeof v === "number") return v;
  const text = v.trim();
  if (DATE_ONLY.test(text)) return Date.parse(`${text}T00:00:00+05:30`);
  const m = DATE_TIME.exec(text);
  if (!m) return Number.NaN;
  const [, date, time, zone] = m;
  const fraction = time.split(".");
  const ms = fraction[1] ? `.${fraction[1].slice(0, 3).padEnd(3, "0")}` : "";
  const hms = fraction[0].length === 5 ? `${fraction[0]}:00` : fraction[0];
  const offset = !zone || /z/i.test(zone) ? "Z" : zone.length === 5 ? `${zone.slice(0, 3)}:${zone.slice(3)}` : zone;
  return Date.parse(`${date}T${hms}${ms}${offset}`);
}

/** The IST wall-clock fields of an instant (read off a shifted UTC date, so the device zone never matters). */
function istParts(ms: number) {
  const d = new Date(ms + IST_OFFSET_MS);
  return { y: d.getUTCFullYear(), mo: d.getUTCMonth(), day: d.getUTCDate(), wd: d.getUTCDay(),
           h: d.getUTCHours(), mi: d.getUTCMinutes(), s: d.getUTCSeconds() };
}

const pad = (n: number) => String(n).padStart(2, "0");

/** "10:42 IST" (24-hour). `seconds` adds ":05"; `suffix: false` drops " IST" where the screen already says it. */
export function formatTimeIST(v: When, opts: { seconds?: boolean; suffix?: boolean } = {}): string {
  const ms = instant(v);
  if (!Number.isFinite(ms)) return EMPTY;
  const p = istParts(ms);
  const time = `${pad(p.h)}:${pad(p.mi)}${opts.seconds ? `:${pad(p.s)}` : ""}`;
  return opts.suffix === false ? time : `${time} IST`;
}

/** "8 Oct 2026" in IST; `weekday` gives "Thu 8 Oct 2026". */
export function formatDateIST(v: When, opts: { weekday?: boolean } = {}): string {
  const ms = instant(v);
  if (!Number.isFinite(ms)) return EMPTY;
  const p = istParts(ms);
  return `${opts.weekday ? `${WEEKDAYS[p.wd]} ` : ""}${p.day} ${MONTHS[p.mo]} ${p.y}`;
}

/** "8 Oct 2026, 10:42 IST". */
export function formatDateTimeIST(v: When, opts: { seconds?: boolean } = {}): string {
  const ms = instant(v);
  if (!Number.isFinite(ms)) return EMPTY;
  return `${formatDateIST(ms)}, ${formatTimeIST(ms, opts)}`;
}

// --- Compact Indian units (UI-H Wave 0, H4) -------------------------------------------------------------------------

/**
 * A count in compact Indian units, for OI, volume and other non-money figures: 1,52,00,000 -> "1.52 Cr",
 * 6,00,000 -> "6 L", 59,000 -> "59 K", 950 -> "950". Below 1,000 the number is shown whole (or with `decimals` if
 * asked by `small`). `decimals` (default 2) fixes the places of the scaled figure; trailing zeros are dropped unless
 * `fixed` is set (a table column that must line up). Negatives use "−". Pair it with the full value on tap / hover
 * (IndianNumber does this), since a compact figure hides the last digits.
 */
export function formatCompactIN(v: Num, opts: { decimals?: number; fixed?: boolean; small?: number } = {}): string {
  if (!finite(v)) return EMPTY;
  const decimals = opts.decimals ?? 2;
  const abs = Math.abs(v);
  const scale = (n: number, unit: string) => {
    const text = magnitude(abs / n, decimals);
    return `${opts.fixed ? text : trimZeros(text)} ${unit}`;
  };
  let body: string;
  // The unit follows the ROUNDED figure, so 99,999.6 is "1 L", never "100 K".
  if (Number((abs / 1e7).toFixed(decimals)) >= 1) body = scale(1e7, "Cr");
  else if (Number((abs / 1e5).toFixed(decimals)) >= 1) body = scale(1e5, "L");
  else if (Number((abs / 1e3).toFixed(decimals)) >= 1) body = scale(1e3, "K");
  else body = magnitude(abs, opts.small ?? 0);
  const zero = !/[1-9]/.test(body);
  return `${v < 0 && !zero ? MINUS : ""}${body}`;
}

/** Signed compact count, for a change in OI: "+15 K" / "−15 K" (zero unsigned). */
export function formatSignedCompactIN(v: Num, opts: { decimals?: number; fixed?: boolean } = {}): string {
  if (!finite(v)) return EMPTY;
  const plain = formatCompactIN(v, opts);
  return v > 0 && /[1-9]/.test(plain) ? `+${plain}` : plain;
}

/** The meaning of a signed figure, for its colour: up (above zero), down (below), flat (zero or missing). */
export type Meaning = "up" | "down" | "flat";
export function meaningOf(v: Num, decimals = 2): Meaning {
  if (!finite(v) || roundsToZero(v, decimals)) return "flat";
  return v > 0 ? "up" : "down";
}
