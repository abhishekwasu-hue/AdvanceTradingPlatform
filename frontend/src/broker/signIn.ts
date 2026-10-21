/**
 * P2b: "this sign-in" for the broker prompt. Kept in sessionStorage (this tab, until it closes), per user, and every
 * read and write is wrapped: with storage blocked (a private window, a strict browser) a copy in memory keeps the
 * behaviour for this page load.
 *
 * - AuthContext calls `markSignIn` after each real sign-in (password, two-factor, invite, reset, new account), which
 *   gives the sign-in a new id. A session restored in a new tab gets its own id the first time it is read here.
 * - "Later" on the prompt stores the current sign-in's id under the user (`dismissForThisSignIn`); the prompt stays
 *   away until the id changes - the next sign-in.
 * - A new account is flagged once (`takeNewAccount`): the shell then opens the Broker page first.
 * - Signing out clears the sign-in (`clearSignIn`).
 */

const SIGN_IN_KEY = "atp_sign_in";
const LATER_PREFIX = "atp_broker_prompt_later:";

interface SignIn { userId: number; id: string; isNew: boolean }

const memory = new Map<string, string>();

function read(key: string): string | null {
  try {
    const v = window.sessionStorage.getItem(key);
    if (v !== null) return v;
  } catch { /* storage unavailable: memory below */ }
  return memory.get(key) ?? null;
}

function write(key: string, value: string | null): void {
  if (value === null) memory.delete(key); else memory.set(key, value);
  try {
    if (value === null) window.sessionStorage.removeItem(key); else window.sessionStorage.setItem(key, value);
  } catch { /* storage unavailable: memory only */ }
}

function newId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  } catch { /* fall through */ }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function parse(raw: string | null): SignIn | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as Partial<SignIn>;
    if (typeof v.userId === "number" && typeof v.id === "string" && v.id) return { userId: v.userId, id: v.id, isNew: v.isNew === true };
  } catch { /* unreadable: a new sign-in */ }
  return null;
}

/** A real sign-in just happened for `userId` (`isNew`: the account was just created). */
export function markSignIn(userId: number, isNew = false): void {
  write(SIGN_IN_KEY, JSON.stringify({ userId, id: newId(), isNew }));
}

/** The current sign-in's id for `userId`; one is made the first time a restored session is seen in this tab. */
export function signInId(userId: number): string {
  const current = parse(read(SIGN_IN_KEY));
  if (current && current.userId === userId) return current.id;
  const made: SignIn = { userId, id: newId(), isNew: false };
  write(SIGN_IN_KEY, JSON.stringify(made));
  return made.id;
}

/** True once, right after `userId` created the account in this tab. */
export function takeNewAccount(userId: number): boolean {
  const current = parse(read(SIGN_IN_KEY));
  if (!current || current.userId !== userId || !current.isNew) return false;
  write(SIGN_IN_KEY, JSON.stringify({ ...current, isNew: false }));
  return true;
}

/** Signed out: the next sign-in starts fresh (the prompt may show again). */
export function clearSignIn(): void {
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
