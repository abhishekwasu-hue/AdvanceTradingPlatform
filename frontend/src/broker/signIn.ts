/**
 * P2b: "this sign-in" for the broker prompt. The session is an HttpOnly cookie shared by every tab of this browser, so
 * the sign-in record lives in localStorage too (shared by those tabs; review M1): a new tab or a reload is the same
 * sign-in, and "Later" holds there as well. Every read and write is wrapped: with storage blocked (a private window, a
 * strict browser) a copy in memory keeps the behaviour for this page load.
 *
 * - Only `markSignIn` makes a sign-in id - AuthContext calls it after each real sign-in (password, two-factor, invite,
 *   reset, new account). A restored session reuses the stored id; with none stored (storage cleared, or a session from
 *   before this change) it reads as one fixed "restored" sign-in per user, until the next real one.
 * - "Later" on the prompt stores the current sign-in's id under the user (`dismissForThisSignIn`); the prompt stays
 *   away until the id changes - the next sign-in.
 * - A new account is flagged once (`takeNewAccount`): the shell then opens the Broker page first.
 * - Signing out clears the record and that user's "Later" (`clearSignIn`).
 */

const SIGN_IN_KEY = "atp_sign_in";
const LATER_PREFIX = "atp_broker_prompt_later:";

interface SignIn { userId: number; id: string; isNew: boolean }

const memory = new Map<string, string>();

function read(key: string): string | null {
  try {
    const v = window.localStorage.getItem(key);
    if (v !== null) return v;
  } catch { /* intentional: storage unavailable: memory below */ }
  return memory.get(key) ?? null;
}

function write(key: string, value: string | null): void {
  if (value === null) memory.delete(key); else memory.set(key, value);
  try {
    if (value === null) window.localStorage.removeItem(key); else window.localStorage.setItem(key, value);
  } catch { /* intentional: storage unavailable: memory only */ }
}

function newId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  } catch { /* intentional: no crypto API: the fallback id below */ }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function parse(raw: string | null): SignIn | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as Partial<SignIn>;
    if (typeof v.userId === "number" && typeof v.id === "string" && v.id) return { userId: v.userId, id: v.id, isNew: v.isNew === true };
  } catch { /* intentional: unreadable: no record */ }
  return null;
}

/** A real sign-in just happened for `userId` (`isNew`: the account was just created). The only place an id is made. */
export function markSignIn(userId: number, isNew = false): void {
  write(SIGN_IN_KEY, JSON.stringify({ userId, id: newId(), isNew }));
}

/** The current sign-in's id for `userId`: the stored one, or a fixed "restored" id when none is stored for this user. */
export function signInId(userId: number): string {
  const current = parse(read(SIGN_IN_KEY));
  return current && current.userId === userId ? current.id : `restored:${userId}`;
}

/** True once, right after `userId` created the account. */
export function takeNewAccount(userId: number): boolean {
  const current = parse(read(SIGN_IN_KEY));
  if (!current || current.userId !== userId || !current.isNew) return false;
  write(SIGN_IN_KEY, JSON.stringify({ ...current, isNew: false }));
  return true;
}

/** Signed out: the next sign-in starts fresh (the prompt may show again). */
export function clearSignIn(): void {
  const current = parse(read(SIGN_IN_KEY));
  if (current) write(`${LATER_PREFIX}${current.userId}`, null);
  write(SIGN_IN_KEY, null);
}

/** "Later": hide the prompt for the rest of this sign-in. */
export function dismissForThisSignIn(userId: number): void {
  write(`${LATER_PREFIX}${userId}`, signInId(userId));
}

/** True when "Later" was chosen during this sign-in. */
export function dismissedThisSignIn(userId: number): boolean {
  const stored = read(`${LATER_PREFIX}${userId}`);
  return stored !== null && stored === signInId(userId);
}

/** Tests only: forget the in-memory copy. */
export function resetSignInMemoryForTests(): void {
  memory.clear();
}
