import { describe, expect, it } from "vitest";
import formats from "./__fixtures__/key_formats.json";
import { KEY_FORMATS, MAY_TRAIN_ON_FREE_TIER, cleanKey, detectProvider, refusalFor } from "./keyFormats";
import { PLAIN_ERRORS, plainError } from "./errors";

/** Settings redesign A (review R5): the browser reads the same key-format table as the server. The fixture is a
 * byte-identical copy of backend/tests/fixtures/key_formats.json (backend/tests/test_settings_a_connect_ai.py checks
 * the copy and runs the same cases against app/ai/key_formats.py). */

describe("the shared key-format table", () => {
  it("every case of the shared fixture detects the same kind and provider as on the server", () => {
    expect(formats.cases.length).toBeGreaterThan(15);
    for (const c of formats.cases) {
      const found = detectProvider(c.key);
      expect({ key: c.key, kind: found.kind, provider: found.provider }).toEqual({ key: c.key, kind: c.kind, provider: c.provider });
    }
  });

  it("sessions and admin keys come first; OpenRouter and Groq are never OpenAI", () => {
    const kinds = KEY_FORMATS.map((r) => r.kind);
    expect(kinds.indexOf("provider")).toBeGreaterThan(kinds.lastIndexOf("session"));
    expect(kinds.indexOf("provider")).toBeGreaterThan(kinds.lastIndexOf("unsupported"));
    const openrouter = detectProvider(["sk", "-or-", "v1-", "FAKEFAKE0000000000000000"].join(""));
    expect(openrouter).toEqual({ kind: "unsupported", provider: null, name: "OpenRouter" });
    expect(refusalFor(openrouter)).toBe("This looks like an OpenRouter key. AiAlgoDesk cannot use OpenRouter keys yet. Use a Claude, OpenAI or Google Gemini key.");
    expect(refusalFor(detectProvider(["gsk", "_", "FAKEFAKE0000000000000000"].join("")))).toContain("Groq keys yet");
  });

  it("a paste is trimmed of spaces, line breaks and quotes", () => {
    expect(cleanKey("  'abc'\n")).toBe("abc");
    expect(cleanKey(undefined)).toBe("");
  });

  it("only Google Gemini is marked as a free tier that may train on what it is sent", () => {
    expect(MAY_TRAIN_ON_FREE_TIER).toEqual({ anthropic: false, openai: false, gemini: true });
  });
});

describe("plain error sentences", () => {
  it("one sentence per kind, the ones the order names", () => {
    expect(plainError("auth")).toBe("This key did not work. Check that you copied all of it.");
    expect(plainError("no_credit")).toBe("This key has no credit left.");
    expect(plainError("rate")).toBe("Too many requests. Try again in a minute.");
    expect(plainError("no_vision")).toBe("This provider cannot read charts. Vision needs Gemini, Claude or OpenAI.");
    expect(plainError("something new")).toBe(PLAIN_ERRORS.other);
    expect(plainError(null)).toBe(PLAIN_ERRORS.other);
  });
});
