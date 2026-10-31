import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * M63 I-7 guard: a failure is reported (an error state or `toast.error(errorSentence(e))`), never swallowed.
 * It counts the silent catches in app source (tests, stories and fixtures excluded):
 * - a `catch { }` whose body is empty or only comments;
 * - a `.catch(() => {})` / `.catch(() => undefined)` / `.catch(() => null)` (with or without a parameter).
 * A catch that is silent on purpose (storage that throws in private mode, an optional probe, a request aborted on
 * unmount, a best-effort background call) says so with a comment that starts `intentional:` and gives the reason;
 * such catches are not counted. Each `.catch` marked this way carries the comment on the same line or the line above.
 *
 * BASELINE is a ratchet: the count may only go down. When a pass removes some, lower BASELINE to the new count
 * (the test fails when the number is lower too, so the ratchet never goes slack).
 */
const BASELINE = 14;

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "src");

function files(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) files(full, out);
    else if (/\.(tsx?|jsx?)$/.test(name) && !/\.(test|spec|stories|fixture)\.(tsx?|jsx?)$/.test(name)) out.push(full);
  }
  return out;
}

const COMMENTS = String.raw`(?:\s*(?:\/\*[\s\S]*?\*\/|\/\/[^\n]*))*\s*`;
/** `catch {}` / `catch (e) { // ... }` - an empty or comment-only body. */
const EMPTY_CATCH = new RegExp(String.raw`\bcatch\s*(?:\([^)]*\))?\s*\{(${COMMENTS})\}`, "g");
/** `.catch(() => {})`, `.catch((_e) => undefined)`, `.catch(() => null)`. */
const SILENT_PROMISE = new RegExp(
  String.raw`\.catch\(\s*(?:\(\s*[\w$]*\s*\)|[\w$]+)\s*=>\s*(?:\{(${COMMENTS})\}|undefined|null|void 0)\s*\)`, "g");

const INTENTIONAL = /intentional:\s*\S/;

interface SilentCatch { file: string; line: number; text: string }

/** The silent catches in one source text that are not marked `intentional:` with a reason. */
function silentCatches(source: string, file = "x.ts"): SilentCatch[] {
  const out: SilentCatch[] = [];
  const lines = source.split("\n");
  for (const re of [EMPTY_CATCH, SILENT_PROMISE]) {
    for (const m of source.matchAll(re)) {
      const index = m.index ?? 0;
      const line = source.slice(0, index).split("\n").length;
      const body = m[1] ?? "";
      // The reason may sit in the catch body, on the same line, or on the line just above.
      const near = `${body}\n${lines[line - 1] ?? ""}\n${lines[line - 2] ?? ""}`;
      if (INTENTIONAL.test(near)) continue;
      out.push({ file, line, text: m[0].replace(/\s+/g, " ").slice(0, 100) });
    }
  }
  return out.sort((a, b) => a.line - b.line);
}

describe("silent catch guard (M63 I-7)", () => {
  it("finds the shapes it is meant to find, and honours an intentional reason", () => {
    expect(silentCatches("try { a(); } catch { /* ignore */ }")).toHaveLength(1);
    expect(silentCatches("try { a(); } catch (e) {}")).toHaveLength(1);
    expect(silentCatches("p.catch(() => {});")).toHaveLength(1);
    expect(silentCatches("p.catch((_e) => undefined);")).toHaveLength(1);
    expect(silentCatches("p.catch(() => null);")).toHaveLength(1);
    expect(silentCatches("try { a(); } catch { /* intentional: storage may throw in private mode */ }")).toHaveLength(0);
    expect(silentCatches("// intentional: best-effort, the next poll retries\np.catch(() => {});")).toHaveLength(0);
    expect(silentCatches("try { a(); } catch (e) { toast.error(errorSentence(e)); }")).toHaveLength(0);
    expect(silentCatches("p.catch((e) => setError(e));")).toHaveLength(0);
  });

  it("the number of silent catches in app source does not grow", () => {
    const found = files(SRC).flatMap((f) => silentCatches(readFileSync(f, "utf8"), relative(ROOT, f)));
    const listing = found.map((c) => `${c.file}:${c.line} ${c.text}`).join("\n");
    if (found.length !== BASELINE) console.log(`silent catches (${found.length}):\n${listing}`);
    expect(found.length, `silent catches grew past ${BASELINE}:\n${listing}`).toBeLessThanOrEqual(BASELINE);
    expect(found.length, `fewer silent catches than BASELINE (${BASELINE}) - lower BASELINE to ${found.length}`).toBe(BASELINE);
  });
});
