import { describe, expect, it } from "vitest";
import formats from "./__fixtures__/key_formats.json";
import { KEY_FORMATS, MAY_TRAIN_ON_FREE_TIER, cleanKey, detectProvider, refusalFor, splitAccountId } from "./keyFormats";
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

  it("sessions and admin keys come first; OpenRouter and Groq are their own providers, never OpenAI", () => {
    const kinds = KEY_FORMATS.map((r) => r.kind);
    expect(kinds.indexOf("provider")).toBeGreaterThan(kinds.lastIndexOf("session"));
    const prefixes = KEY_FORMATS.map((r) => r.prefix);
    expect(prefixes.indexOf("sk-or-")).toBeLessThan(prefixes.indexOf("sk-"));
    const openrouter = detectProvider(["sk", "-or-", "v1-", "FAKEFAKE0000000000000000"].join(""));
    expect(openrouter).toEqual({ kind: "provider", provider: "openrouter", name: "OpenRouter" });
    expect(refusalFor(openrouter)).toBeNull();
    expect(detectProvider(["gsk", "_", "FAKEFAKE0000000000000000"].join(""))).toEqual({ kind: "provider", provider: "groq", name: "Groq" });
    expect(refusalFor(detectProvider(["xai", "-", "FAKEFAKE0000000000000000"].join("")))).toContain(
      "AiAlgoDesk cannot use xAI keys. Use a key from Google Gemini, Claude, OpenAI, OpenRouter, Groq, Mistral, Z.ai or Cloudflare Workers AI.");
  });

  it("MO2A-2: a Cloudflare token next to its Account ID splits the same way as on the server", () => {
    expect(formats.account_id_cases.length).toBeGreaterThanOrEqual(5);
    for (const c of formats.account_id_cases) {
      expect({ paste: c.paste, ...splitAccountId(c.paste) }).toEqual({ paste: c.paste, accountId: c.account_id, rest: c.rest });
    }
    const global = detectProvider(["cfk", "_", "FAKEFAKE0000000000000000"].join(""));
    expect(global.kind).toBe("admin");
    expect(refusalFor(global)).toBe("This is your Cloudflare Global API Key, which opens your whole Cloudflare account. Create a Workers AI API token instead.");
    expect(detectProvider(["cfut", "_", "FAKEFAKE0000000000000000"].join(""))).toEqual({ kind: "provider", provider: "cloudflare", name: "Cloudflare Workers AI" });
  });

  it("a paste is trimmed of spaces, line breaks and quotes", () => {
    expect(cleanKey("  'abc'\n")).toBe("abc");
    expect(cleanKey(undefined)).toBe("");
  });

  it("the free tiers that may train on what they are sent are marked (unknown counts as yes)", () => {
    expect(MAY_TRAIN_ON_FREE_TIER).toEqual({ gemini: true, anthropic: false, openai: false, openrouter: true, groq: false, mistral: true, zai: true, cloudflare: false });
  });
});

describe("plain error sentences", () => {
  it("one sentence per kind, the ones the order names", () => {
    expect(plainError("auth")).toBe("This key did not work. Check that you copied all of it.");
    expect(plainError("no_credit")).toBe("This key has no credit left.");
    expect(plainError("rate")).toBe("Too many requests. Try again in a minute.");
    expect(plainError("no_vision")).toBe("This provider cannot read charts. Vision needs Gemini, Claude, OpenAI, OpenRouter, Mistral or Cloudflare Workers AI.");
    expect(plainError("something new")).toBe(PLAIN_ERRORS.other);
    expect(plainError(null)).toBe(PLAIN_ERRORS.other);
  });
});
