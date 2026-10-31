// C12: the last server error this app saw - its reference (the `X-Request-ID` the server answered with, the same id the
// server's recent-errors store and logs keep), the screen it happened on (path only) and when. "Report a problem" offers
// to attach it. Nothing else is kept: no message, body, header or address query. Tiny on purpose: api/errors.ts records
// into it on every server error.

export interface LastServerError { id: string; page: string; at: string }

const KEY = "atp_last_server_error";
const ID = /^[A-Za-z0-9._:-]{1,64}$/;
let last: LastServerError | null = null;

function pathOnly(raw: string): string {
  const path = (raw || "/").split("#", 1)[0].split("?", 1)[0];
  return path.startsWith("/") ? path.slice(0, 200) : "/";
}

/** Called for every server error (5xx) that carried a reference. */
export function rememberServerError(id: string | null | undefined, page?: string, now: Date = new Date()): void {
  if (!id || !ID.test(id)) return;
  const where = page ?? (typeof window !== "undefined" ? window.location.pathname : "/");
  last = { id, page: pathOnly(where), at: now.toISOString() };
  try { sessionStorage.setItem(KEY, JSON.stringify(last)); } catch { /* intentional: storage unavailable: memory only */ }
}

/** The last one, from this tab (it survives a reload of the same tab, never another tab or device). */
export function lastServerError(): LastServerError | null {
  if (last) return last;
  try {
    const raw = sessionStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) as Partial<LastServerError> : null;
    if (parsed && typeof parsed.id === "string" && ID.test(parsed.id) && typeof parsed.at === "string") {
      last = { id: parsed.id, page: pathOnly(String(parsed.page ?? "/")), at: parsed.at };
    }
  } catch { /* intentional: storage unavailable or malformed: none */ }
  return last;
}

export function forgetServerError(): void {
  last = null;
  try { sessionStorage.removeItem(KEY); } catch { /* intentional: storage unavailable */ }
}
