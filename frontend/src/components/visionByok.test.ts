import { describe, expect, it } from "vitest";
import { capUsage, formatUsd, keyShapeError, looksLikeSessionToken, parseCap, SESSION_TOKEN_MESSAGE } from "./visionByok";

// Obviously fake keys - never real ones.
const ANTHROPIC = "sk-ant-api03-FAKEFAKE-test-only-aaaaaaaa-1111";
const OPENAI = "sk-proj-FAKEFAKE-test-only-cccccccc-3333";
const GEMINI = "AIzaFAKEFAKE-test-only-dddddddddd-4444";

describe("keyShapeError", () => {
  it("accepts a well-formed key per provider (surrounding spaces trimmed)", () => {
    expect(keyShapeError("anthropic", `  ${ANTHROPIC} `)).toBeNull();
    expect(keyShapeError("openai", OPENAI)).toBeNull();
    expect(keyShapeError("gemini", GEMINI)).toBeNull();
  });

  it("refuses the wrong provider's key, admin keys, bad lengths and characters", () => {
    expect(keyShapeError("anthropic", OPENAI)).toContain("sk-ant-");
    expect(keyShapeError("openai", ANTHROPIC)).toContain("starts with 'sk-'");
    expect(keyShapeError("gemini", OPENAI)).toContain("not a Google Gemini key");
    expect(keyShapeError("anthropic", "sk-ant-admin01-FAKEFAKE000000000000")).toContain("admin key");
    expect(keyShapeError("openai", "sk-short")).toContain("20 to 256");
    expect(keyShapeError("openai", "sk-" + "x".repeat(300))).toContain("20 to 256");
    expect(keyShapeError("openai", "sk-FAKE key with spaces 0000000000")).toContain("only letters");
    expect(keyShapeError("gemini", "  ")).toBe("Paste an API key");
  });

  it("refuses subscription sign-in, session cookies and OAuth tokens with one clear message", () => {
    for (const token of [
      "sk-ant-oat01-FAKEFAKEsubscriptionoauth0000",
      "sk-ant-sid01-FAKEFAKEclaudeaisession00000",
      "sessionKey=sk-ant-sid01-FAKEFAKE0000000000",
      "Bearer sk-ant-api03-FAKEFAKE00000000000000",
      "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJGQUtFIn0.FAKEFAKEsig",
      "ya29.a0FAKEFAKEgoogleoauth00000000000",
    ]) {
      expect(looksLikeSessionToken(token)).toBe(true);
      expect(keyShapeError("anthropic", token)).toBe(SESSION_TOKEN_MESSAGE);
    }
  });

  it("never echoes the key in a message", () => {
    for (const provider of ["anthropic", "openai", "gemini"] as const) {
      const message = keyShapeError(provider, provider === "gemini" ? OPENAI : GEMINI) ?? "";
      expect(message).not.toContain(OPENAI);
      expect(message).not.toContain(GEMINI);
    }
  });
});

describe("cap formatting", () => {
  it("formats tiny and normal USD amounts", () => {
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(0.00042)).toBe("$0.0004");
    expect(formatUsd(1.5)).toBe("$1.50");
    expect(formatUsd(Number.NaN)).toBe("$0.00");
  });

  it("shows spend against a cap and the hard stop", () => {
    expect(capUsage(0.25, 1)).toEqual({ pct: 25, reached: false, label: "$0.25 of $1.00" });
    expect(capUsage(1, 1)).toEqual({ pct: 100, reached: true, label: "$1.00 of $1.00 - cap reached, Vision is stopped" });
    expect(capUsage(3, 1).pct).toBe(100);
    expect(capUsage(0, 0).reached).toBe(true);
  });

  it("parses a typed cap", () => {
    expect(parseCap("2.5", 500)).toEqual({ value: 2.5, error: null });
    expect(parseCap("0", 500)).toEqual({ value: 0, error: null });
    expect(parseCap("", 500).error).toBe("Enter a cap in USD");
    expect(parseCap("-1", 500).error).toContain("0 or more");
    expect(parseCap("abc", 500).error).toContain("0 or more");
    expect(parseCap("900", 500).error).toBe("A cap can be at most $500");
  });
});
