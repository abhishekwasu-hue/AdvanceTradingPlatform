import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { formatCompactIN, formatSignedCompactIN, meaningOf } from "./format";
import { IndianNumber, indianNumberText } from "./IndianNumber";

describe("UI-H H4: compact Indian units", () => {
  it.each([
    [15200000, "1.52 Cr"], [10000000, "1 Cr"], [600000, "6 L"], [125000, "1.25 L"], [59000, "59 K"], [1500, "1.5 K"],
    [950, "950"], [0, "0"], [-15000, "−15 K"], [99999.6, "1 L"], [9999999, "1 Cr"], [null, "–"], [Number.NaN, "–"],
  ])("formatCompactIN(%s) = %s", (v, out) => expect(formatCompactIN(v as number)).toBe(out));

  it("fixed decimals keep the zeros (a column that lines up)", () => {
    expect(formatCompactIN(600000, { fixed: true })).toBe("6.00 L");
    expect(formatCompactIN(15200000, { decimals: 1, fixed: true })).toBe("1.5 Cr");
  });

  it("signed: + for a rise, − for a fall, zero unsigned", () => {
    expect(formatSignedCompactIN(15000)).toBe("+15 K");
    expect(formatSignedCompactIN(-15000)).toBe("−15 K");
    expect(formatSignedCompactIN(0)).toBe("0");
  });

  it("meaning by sign", () => {
    expect(meaningOf(3)).toBe("up");
    expect(meaningOf(-0.001)).toBe("flat");
    expect(meaningOf(-2)).toBe("down");
    expect(meaningOf(null)).toBe("flat");
  });
});

describe("UI-H H4: IndianNumber", () => {
  it("a compact count is a button: the full value is the title and the accessible name", () => {
    const html = renderToStaticMarkup(<IndianNumber value={600000} compact />);
    expect(html).toMatch(/<button[^>]*title="6,00,000"[^>]*aria-label="6,00,000"/);
    expect(html).toContain(">6 L</button>");
    expect(html).toContain("font-tabular");
  });

  it("a figure that hides nothing is plain text", () => {
    const html = renderToStaticMarkup(<IndianNumber value={950} compact />);
    expect(html).not.toContain("<button");
    expect(html).toContain(">950</span>");
  });

  it("money follows the money rule (whole rupees; Cr from one crore up)", () => {
    expect(indianNumberText({ value: 161990, kind: "money" }).shown).toBe("₹1,61,990");
    expect(indianNumberText({ value: 24000000, kind: "money", compact: true })).toEqual({ full: "₹2,40,00,000", shown: "₹2.4 Cr" });
    expect(indianNumberText({ value: -700, kind: "money", signed: true }).shown).toBe("−₹700");
  });

  it("colour by meaning only when asked: up / down tokens", () => {
    expect(renderToStaticMarkup(<IndianNumber value={3120} kind="money" signed tone="meaning" />)).toContain("text-up");
    expect(renderToStaticMarkup(<IndianNumber value={-3120} kind="money" signed tone="meaning" />)).toContain("text-down");
    expect(renderToStaticMarkup(<IndianNumber value={24812.35} kind="price" />)).not.toMatch(/text-(up|down)/);
  });

  it("pct, price and change reuse format.ts", () => {
    expect(indianNumberText({ value: 0.48, kind: "pct" }).shown).toBe("+0.48%");
    expect(indianNumberText({ value: 24812.35, kind: "price" }).shown).toBe("24,812.35");
    expect(indianNumberText({ value: -8.2, kind: "change" }).shown).toBe("−8.20");
    expect(indianNumberText({ value: 15000, signed: true, compact: true }).shown).toBe("+15 K");
  });
});
