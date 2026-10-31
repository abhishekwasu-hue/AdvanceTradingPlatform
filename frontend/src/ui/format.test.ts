import { describe, expect, it } from "vitest";
import {
  EMPTY, MINUS, formatChange, formatDateIST, formatDateTimeIST, formatINR, formatLots, formatNumber, formatPct, formatPrice,
  formatSignedINR, formatTimeIST, tickDecimals,
} from "./format";

describe("UI2-0 format: Indian grouping", () => {
  it("groups the last three digits, then pairs", () => {
    expect(formatNumber(0)).toBe("0");
    expect(formatNumber(999)).toBe("999");
    expect(formatNumber(1000)).toBe("1,000");
    expect(formatNumber(99999)).toBe("99,999");
    expect(formatNumber(100000)).toBe("1,00,000");
    expect(formatNumber(12450000)).toBe("1,24,50,000");
    expect(formatNumber(1234567890)).toBe("1,23,45,67,890");
    expect(formatNumber(1234567.891, 2)).toBe("12,34,567.89");
  });

  it("uses the true minus sign and never shows −0", () => {
    expect(formatNumber(-1234.5, 1)).toBe(`${MINUS}1,234.5`);
    expect(formatNumber(-0.001, 2)).toBe("0.00");
    expect(formatNumber(-1234.5, 1)).not.toContain("-");
  });

  it("trim: at most N places, trailing zeros dropped (M63 I-7)", () => {
    expect(formatNumber(24812.5, 2, { trim: true })).toBe("24,812.5");
    expect(formatNumber(24812, 2, { trim: true })).toBe("24,812");
    expect(formatNumber(1234567.891, 2, { trim: true })).toBe("12,34,567.89");
    expect(formatNumber(100000.004, 2, { trim: true })).toBe("1,00,000");
    expect(formatNumber(-1500.1, 3, { trim: true })).toBe(`${MINUS}1,500.1`);
    expect(formatNumber(-0.001, 2, { trim: true })).toBe("0");
    expect(formatNumber(1000, 0, { trim: true })).toBe("1,000");
    expect(formatNumber(null, 2, { trim: true })).toBe(EMPTY);
  });

  it("shows a dash for a missing value, never 0", () => {
    for (const v of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(formatNumber(v)).toBe(EMPTY);
      expect(formatINR(v)).toBe(EMPTY);
      expect(formatSignedINR(v)).toBe(EMPTY);
      expect(formatPct(v)).toBe(EMPTY);
      expect(formatPrice(v)).toBe(EMPTY);
      expect(formatChange(v)).toBe(EMPTY);
      expect(formatLots(v, 75)).toBe(EMPTY);
    }
  });
});

describe("UI2-0 format: rupees", () => {
  it("the money rule: whole rupees with Indian grouping by default (summaries, cards, sentences)", () => {
    expect(formatINR(123456)).toBe("₹1,23,456");
    expect(formatINR(124500)).toBe("₹1,24,500");
    expect(formatINR(0)).toBe("₹0");
    expect(formatINR(3120.456)).toBe("₹3,120");
    expect(formatINR(3120.5)).toBe("₹3,121");
    expect(formatINR(-700)).toBe(`${MINUS}₹700`);
    expect(formatINR(-0.4)).toBe("₹0");                                     // rounds to zero: never "−₹0"
    expect(formatINR(124500)).not.toContain(".");
  });

  it("paise only when asked for (order tickets, fills, P&L detail rows)", () => {
    expect(formatINR(124500, { paise: true })).toBe("₹1,24,500.00");
    expect(formatINR(3120.456, { paise: true })).toBe("₹3,120.46");
    expect(formatINR(-700, { paise: true })).toBe(`${MINUS}₹700.00`);
    expect(formatINR(-0.004, { paise: true })).toBe("₹0.00");
    expect(formatINR(2.4e7, { paise: true, compact: true })).toBe("₹2,40,00,000.00");   // paise win over compact
  });

  it("compact (narrow tiles): whole rupees up to a crore, then crore; never the lakh shorthand", () => {
    expect(formatINR(124500, { compact: true })).toBe("₹1,24,500");
    expect(formatINR(100000, { compact: true })).toBe("₹1,00,000");
    expect(formatINR(85000, { compact: true })).toBe("₹85,000");
    expect(formatINR(99999.6, { compact: true })).toBe("₹1,00,000");
    expect(formatINR(-250000, { compact: true })).toBe(`${MINUS}₹2,50,000`);
    expect(formatINR(24000000, { compact: true })).toBe("₹2.4 Cr");
    expect(formatINR(10000000, { compact: true })).toBe("₹1 Cr");
    expect(formatINR(9999999.6, { compact: true })).toBe("₹1 Cr");          // rounds up into a crore
    expect(formatINR(9999999, { compact: true })).toBe("₹99,99,999");
    expect(formatINR(-0.3, { compact: true })).toBe("₹0");
    expect(formatINR(124500, { compact: true })).not.toMatch(/ L\b/);
  });

  it("formatSignedINR: always signed, except a true zero", () => {
    expect(formatSignedINR(3120)).toBe("+₹3,120");
    expect(formatSignedINR(-700)).toBe(`${MINUS}₹700`);
    expect(formatSignedINR(0)).toBe("₹0");
    expect(formatSignedINR(0.4)).toBe("₹0");
    expect(formatSignedINR(-0.4)).toBe("₹0");
    expect(formatSignedINR(3120.5, { paise: true })).toBe("+₹3,120.50");
    expect(formatSignedINR(0.004, { paise: true })).toBe("₹0.00");
    expect(formatSignedINR(-0.004, { paise: true })).toBe("₹0.00");
    expect(formatSignedINR(124500, { compact: true })).toBe("+₹1,24,500");
    expect(formatSignedINR(0.2, { compact: true })).toBe("₹0");
  });
});

describe("UI2-0 format: prices, changes and percentages", () => {
  it("tick sizes set the decimals", () => {
    expect(tickDecimals(0.05)).toBe(2);
    expect(tickDecimals(0.01)).toBe(2);
    expect(tickDecimals(0.0025)).toBe(4);
    expect(tickDecimals(1)).toBe(0);
    expect(tickDecimals(0.5)).toBe(1);
    expect(tickDecimals(null)).toBe(2);
    expect(tickDecimals(0)).toBe(2);
  });

  it("formatPrice follows the tick", () => {
    expect(formatPrice(24812.35, 0.05)).toBe("24,812.35");
    expect(formatPrice(24812.35)).toBe("24,812.35");
    expect(formatPrice(83.1234, 0.0025)).toBe("83.1234");
    expect(formatPrice(124500, 1)).toBe("1,24,500");
  });

  it("formatChange is signed, zero unsigned", () => {
    expect(formatChange(12.35)).toBe("+12.35");
    expect(formatChange(-8.2)).toBe(`${MINUS}8.20`);
    expect(formatChange(0)).toBe("0.00");
  });

  it("formatPct: 2 decimals, signed, in percent units", () => {
    expect(formatPct(0.48)).toBe("+0.48%");
    expect(formatPct(-1.2)).toBe(`${MINUS}1.20%`);
    expect(formatPct(0)).toBe("0.00%");
    expect(formatPct(0.001)).toBe("0.00%");
    expect(formatPct(12.345, 1)).toBe("+12.3%");
  });
});

describe("UI2-0 format: lots and shares", () => {
  it("F&O in lots with the quantity", () => {
    expect(formatLots(150, 75)).toBe("2 lots (150)");
    expect(formatLots(75, 75)).toBe("1 lot (75)");
    expect(formatLots(-150, 75)).toBe("2 lots (150)");     // the side is shown separately (Long / Short)
    expect(formatLots(100, 75)).toBe("1.33 lots (100)");
    expect(formatLots(1800, 15)).toBe("120 lots (1,800)");
  });

  it("equity in shares", () => {
    expect(formatLots(50)).toBe("50 shares");
    expect(formatLots(1)).toBe("1 share");
    expect(formatLots(1500, 1)).toBe("1,500 shares");
  });
});

describe("UI2-0 format: times in IST whatever the device zone", () => {
  it("formats an instant at UTC+05:30", () => {
    expect(formatTimeIST("2026-10-12T05:12:05Z")).toBe("10:42 IST");
    expect(formatTimeIST("2026-10-12T05:12:05Z", { seconds: true })).toBe("10:42:05 IST");
    expect(formatTimeIST("2026-10-12T05:12:05Z", { suffix: false })).toBe("10:42");
    expect(formatTimeIST("2026-10-08T10:30:00+05:30")).toBe("10:30 IST");
    expect(formatTimeIST(Date.UTC(2026, 9, 12, 18, 45))).toBe("00:15 IST");    // crosses midnight into the next IST day
  });

  it("reads a zone-less timestamp as UTC (what the backend writes)", () => {
    expect(formatTimeIST("2026-10-08T04:00:00")).toBe("09:30 IST");
    expect(formatTimeIST("2026-10-08T04:00:00.123456")).toBe("09:30 IST");
  });

  it("dates and date-times", () => {
    expect(formatDateIST("2026-10-12T20:00:00Z")).toBe("13 Oct 2026");
    expect(formatDateIST("2026-10-13T20:00:00Z", { weekday: true })).toBe("Wed 14 Oct 2026");
    expect(formatDateIST("2026-10-08")).toBe("8 Oct 2026");
    expect(formatDateTimeIST(new Date("2026-10-08T05:00:00Z"))).toBe("8 Oct 2026, 10:30 IST");
  });

  it("shows a dash for a missing or unreadable time", () => {
    for (const v of [null, undefined, "", "not a date"]) {
      expect(formatTimeIST(v)).toBe(EMPTY);
      expect(formatDateIST(v)).toBe(EMPTY);
      expect(formatDateTimeIST(v)).toBe(EMPTY);
    }
  });
});

describe("UI2-0 format: review fixes (L1, L2)", () => {
  it("L1: a space between date and time, without a zone, is UTC - whatever the device zone", () => {
    // Node re-reads TZ when it is assigned (the app's tsconfig has no Node types, hence the cast).
    const env = (globalThis as unknown as { process: { env: Record<string, string | undefined> } }).process.env;
    const saved = env.TZ;
    try {
      for (const tz of ["UTC", "Asia/Kolkata", "America/New_York", "Pacific/Auckland", "Europe/London"]) {
        env.TZ = tz;
        expect(new Date(Date.UTC(2026, 9, 8, 12)).getTimezoneOffset() === 0, tz).toBe(tz === "UTC" || tz === "Europe/London" ? tz === "UTC" : false);
        expect(formatTimeIST("2026-10-08 05:12:00"), tz).toBe("10:42 IST");
        expect(formatTimeIST("2026-10-08T05:12:00"), tz).toBe("10:42 IST");
        expect(formatTimeIST("2026-10-08 05:12:00.123456", { seconds: true }), tz).toBe("10:42:00 IST");
        expect(formatTimeIST("2026-10-08 05:12"), tz).toBe("10:42 IST");
        expect(formatTimeIST("2026-10-08 10:42:00+05:30"), tz).toBe("10:42 IST");
        expect(formatTimeIST("2026-10-08T10:42:00+0530"), tz).toBe("10:42 IST");
        expect(formatDateIST("2026-10-08 20:00:00"), tz).toBe("9 Oct 2026");
        expect(formatDateIST("2026-10-08"), tz).toBe("8 Oct 2026");
      }
    } finally {
      if (saved === undefined) delete env.TZ; else env.TZ = saved;
    }
  });

  it("L1: a text that is not an ISO date and time is unreadable, never guessed in the device zone", () => {
    expect(formatTimeIST("Oct 8 2026 10:42")).toBe(EMPTY);
  });

  it("L2: very large numbers are written out and grouped, never in exponent form", () => {
    expect(formatNumber(1e21)).toBe("1,00,00,00,00,00,00,00,00,00,000");
    expect(formatNumber(-1e21, 2)).toBe(`${MINUS}1,00,00,00,00,00,00,00,00,00,000.00`);
    expect(formatINR(1e21)).not.toMatch(/e/);
  });

  it("L2: compact crore values stay grouped", () => {
    expect(formatINR(1e15, { compact: true })).toBe("₹10,00,00,000 Cr");
    expect(formatINR(1.5e12, { compact: true })).toBe("₹1,50,000 Cr");
    expect(formatINR(25e9, { compact: true })).toBe("₹2,500 Cr");
  });

  it("L2: the plural follows the rounded number", () => {
    expect(formatLots(74.9999, 75)).toBe("1 lot (75)");
    expect(formatLots(75.0001, 75)).toBe("1 lot (75)");
    expect(formatLots(1.0001)).toBe("1 share");
    expect(formatLots(0.9999)).toBe("1 share");
    expect(formatLots(150.004, 75)).toBe("2 lots (150)");
  });
});

// M63 I-7 (one number formatter): the cases the pages moved onto this module rely on.
describe("M63 I-7: the cases the moved pages rely on", () => {
  it("money: whole rupees on cards, paise on charges and AI cost, never a hyphen for a loss", () => {
    expect(formatINR(12345.4)).toBe("₹12,345");                    // Portfolio / Dashboard tiles
    expect(formatINR(-700)).toBe(`${MINUS}₹700`);
    expect(formatINR(0.4)).toBe("₹0");                             // rounds to zero: never "−₹0"
    expect(formatINR(-0.4)).toBe("₹0");
    expect(formatINR(42.5, { paise: true })).toBe("₹42.50");       // contract-note charges, a premium below ₹100
    expect(formatINR(0.07, { paise: true })).toBe("₹0.07");        // one AI call
    expect(formatINR(null)).toBe(EMPTY);
    expect(formatSignedINR(3120.4)).toBe("+₹3,120");               // a position's unrealised P&L
    expect(formatSignedINR(-700)).toBe(`${MINUS}₹700`);
    expect(formatSignedINR(0.2)).toBe("₹0");
  });

  it("plain figures: Indian grouping, fixed decimals, the true minus, a dash when missing", () => {
    expect(formatNumber(1234567)).toBe("12,34,567");                // token and chain counts
    expect(formatNumber(12.345, 1)).toBe("12.3");                   // percent units
    expect(formatNumber(-0.456, 2)).toBe(`${MINUS}0.46`);           // a z-score, beta, a Greek
    expect(formatNumber(-0.004, 2)).toBe("0.00");                   // never "−0.00"
    expect(formatNumber(0.00012, 4)).toBe("0.0001");                // a USD cost
    expect(formatNumber(undefined, 2)).toBe(EMPTY);
    expect(formatNumber(null)).toBe(EMPTY);
    expect(formatNumber(Number.NaN, 1)).toBe(EMPTY);
  });

  it("prices: two decimals at the default tick, grouped", () => {
    expect(formatPrice(24812.3)).toBe("24,812.30");                 // a close, an entry price, a 1 sd band
    expect(formatPrice(123.456)).toBe("123.46");
    expect(formatPrice(null)).toBe(EMPTY);
  });

  it("times: a zone-less timestamp is UTC, shown in IST on the 24-hour clock", () => {
    expect(formatTimeIST("2026-10-08T05:12:00")).toBe("10:42 IST");
  });
});
