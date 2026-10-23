/**
 * MO3-A1: the admin's read-only "View as user", the client side of it. While a view is open, the access token in
 * memory (api/client.ts) is the view's token, which reads as the person and is refused every change by the server; this
 * module holds what the banner shows. Nothing is stored: a reload ends the view in this tab (the admin's own sign-in
 * cookie brings the admin back), and so does any token refresh.
 */

export interface ViewInfo {
  id: number;
  userId: number;
  /** The person, masked the way the admin console masks e-mail addresses. */
  name: string;
  /** ISO time the server ends the view. */
  expiresAt: string;
}

let current: ViewInfo | null = null;
const listeners = new Set<() => void>();

export function currentView(): ViewInfo | null {
  return current;
}

export function setView(next: ViewInfo | null): void {
  current = next;
  listeners.forEach((fn) => fn());
}

export function subscribeView(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** "12 min left" / "45 s left" for the banner (now passed in for tests). */
export function timeLeft(expiresAt: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((new Date(expiresAt).getTime() - now) / 1000));
  return seconds >= 60 ? `${Math.ceil(seconds / 60)} min left` : `${seconds} s left`;
}
