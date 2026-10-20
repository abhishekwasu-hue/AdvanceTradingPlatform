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
  it("formatINR: ₹, Indian grouping, 2 decimals", () => {
    expect(formatINR(124500)).toBe("₹1,24,500.00");
    expect(formatINR(0)).toBe("₹0.00");
    expect(formatINR(3120.456)).toBe("₹3,120.46");
    expect(formatINR(-700)).toBe(`${MINUS}₹700.00`);
  });

  it("formatINR compact: lakh and crore for tiles", () => {
    expect(formatINR(124500, { compact: true })).toBe("₹1.25 L");
    expect(formatINR(100000, { compact: true })).toBe("₹1 L");
    expect(formatINR(24000000, { compact: true })).toBe("₹2.4 Cr");
    expect(formatINR(10000000, { compact: true })).toBe("₹1 Cr");
    expect(formatINR(9999999, { compact: true })).toBe("₹1 Cr");          // 99.99999 L rounds up into crore
    expect(formatINR(85000, { compact: true })).toBe("₹85,000");
    expect(formatINR(99999.6, { compact: true })).toBe("₹1 L");
    expect(formatINR(-250000, { compact: true })).toBe(`${MINUS}₹2.5 L`);
    expect(formatINR(-0.3, { compact: true })).toBe("₹0");
  });

  it("formatSignedINR: always signed, except a true zero", () => {
    expect(formatSignedINR(3120)).toBe("+₹3,120.00");
    expect(formatSignedINR(-700)).toBe(`${MINUS}₹700.00`);
    expect(formatSignedINR(0)).toBe("₹0.00");
    expect(formatSignedINR(0.004)).toBe("₹0.00");
    expect(formatSignedINR(-0.004)).toBe("₹0.00");
    expect(formatSignedINR(124500, { compact: true })).toBe("+₹1.25 L");
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
