import { describe, expect, it } from "vitest";
import { formatCount as chainFormatCount } from "../optionChain/model";
import { formatOi, formatSigned } from "../oi/format";
import { EMPTY, MINUS, formatCount, formatCountChange } from "./format";

/** M61 OI-1 / OI-6: ONE formatter for every OI / volume figure (the chain table's rule, now app-wide). */
describe("M61 OI-1: formatCount", () => {
  it("is plain Indian grouping below 1 lakh, lakh from 1,00,000, crore from 1,00,00,000", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(85_000)).toBe("85,000");
    expect(formatCount(99_999)).toBe("99,999");
    expect(formatCount(1_00_000)).toBe("1 L");
    expect(formatCount(12_35_000)).toBe("12.35 L");
    expect(formatCount(27_49_000)).toBe("27.49 L");
    expect(formatCount(99_99_999)).toBe("99.99 L");          // the unit follows the true value: never "100 L"
    expect(formatCount(1_00_00_000)).toBe("1 Cr");
    expect(formatCount(1_24_00_000)).toBe("1.24 Cr");
    expect(formatCount(5_04_37_270)).toBe("5.04 Cr");
    expect(formatCount(23_04_40_340)).toBe("23.04 Cr");
  });
  it("writes negatives with the minus sign and a missing value as a dash", () => {
    expect(formatCount(-4_500)).toBe(`${MINUS}4,500`);
    expect(formatCount(-21_60_470)).toBe(`${MINUS}21.6 L`);
    expect(formatCount(-0.2)).toBe("0");
    expect(formatCount(null)).toBe(EMPTY);
    expect(formatCount(Number.NaN)).toBe(EMPTY);
  });
  it("signs every change; zero stays unsigned", () => {
    expect(formatCountChange(21_60_470)).toBe("+21.6 L");
    expect(formatCountChange(-99_999)).toBe(`${MINUS}99,999`);
    expect(formatCountChange(1_00_00_000)).toBe("+1 Cr");
    expect(formatCountChange(0)).toBe("0");
    expect(formatCountChange(0.3)).toBe("0");
    expect(formatCountChange(undefined)).toBe(EMPTY);
  });
  it("is the same function behind the chain's and the OI components' names", () => {
    expect(chainFormatCount).toBe(formatCount);
    expect(formatOi).toBe(formatCount);
    expect(formatSigned).toBe(formatCountChange);
  });
});
