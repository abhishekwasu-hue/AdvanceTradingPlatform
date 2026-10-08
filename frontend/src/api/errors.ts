/** P1.1: every failed API call becomes an `ApiError` whose message is a sentence a trader can act on - never a raw
 * status line, a JSON body, an HTML error page from the proxy or a developer hint. The status, the server's own
 * detail and the request id stay on the error for code that branches on them (step-up, feature switched off).
 * `String(error)` gives the message alone (no "Error: " prefix), so the many `setError(String(e))` call sites show
 * the friendly text without changes. */
export class ApiError extends Error {
  readonly status: number;
  /** The server's `detail` when it sent a readable one ("" otherwise). */
  readonly detail: string;
  /** The raw response body (trimmed), for code that matches on a machine code inside it. */
  readonly body: string;
  readonly requestId: string | null;

  constructor(status: number, message: string, detail = "", body = "", requestId: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.body = body;
    this.requestId = requestId;
  }

  override toString(): string {
    return this.message;
  }
}

export const NETWORK_MESSAGE = "Cannot reach the server. Check your connection and try again.";

const GENERIC: Record<number, string> = {
  400: "The request was not accepted. Check the values and try again.",
  401: "Your session has ended. Sign in again from the Account page.",
  402: "This needs a higher plan.",
  403: "You do not have permission to do this.",
  404: "Not found. It may have been removed.",
  409: "This conflicts with the current state. Refresh and try again.",
  413: "The upload is too large.",
  422: "Some values are not valid. Check the form and try again.",
  429: "Too many requests. Wait a moment and try again.",
};

interface ValidationItem { loc?: unknown[]; msg?: string }

function fromValidation(items: ValidationItem[]): string {
  const parts = items.slice(0, 3).map((it) => {
    const field = Array.isArray(it.loc) ? String(it.loc.filter((p) => p !== "body" && p !== "query").join(".")) : "";
    const msg = String(it.msg ?? "invalid value").replace(/^Value error,\s*/i, "");
    return field ? `${field.replace(/_/g, " ")}: ${msg}` : msg;
  });
  const more = items.length > 3 ? ` (+${items.length - 3} more)` : "";
  return `Please check: ${parts.join("; ")}${more}.`;
}

/** The readable part of a FastAPI error body: `{"detail": "..."}`, `{"detail": [validation errors]}` or
 * `{"detail": {"message": ...}}`. Anything else (HTML, plain "Internal Server Error") yields "". */
export function readableDetail(body: string): string {
  const text = body.trim();
  if (!text.startsWith("{")) return "";
  try {
    const parsed = JSON.parse(text) as { detail?: unknown; message?: unknown };
    const d = parsed.detail ?? parsed.message;
    if (typeof d === "string") return d.trim();
    if (Array.isArray(d)) return fromValidation(d as ValidationItem[]);
    if (d && typeof d === "object") {
      const m = (d as { message?: unknown; detail?: unknown }).message ?? (d as { detail?: unknown }).detail;
      if (typeof m === "string") return m.trim();
    }
  } catch {
    /* not JSON */
  }
  return "";
}

/** Builds the error for a non-OK response. Server errors without a readable detail (an unhandled exception, a proxy
 * 502/504 page) get a generic sentence plus the request id, which is what support needs to find the log line. */
export async function apiErrorFrom(response: Response): Promise<ApiError> {
  let body = "";
  try { body = (await response.text()).trim(); } catch { /* body unreadable */ }
  const status = response.status;
  const requestId = response.headers.get("x-request-id");
  const detail = readableDetail(body);
  let message = detail;
  if (!message) {
    if (status >= 500) {
      message = `The server could not complete this request (error ${status}). Try again in a moment.`;
      if (requestId) message += ` Reference: ${requestId}.`;
    } else {
      message = GENERIC[status] ?? `The request failed (error ${status}).`;
    }
  }
  return new ApiError(status, message, detail, body.slice(0, 2000), requestId);
}

/** The message to show for any thrown value: an ApiError's sentence, a network failure as plain words, otherwise the
 * error's own message without the "Error: " prefix. */
export function friendlyError(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof TypeError && /fetch|network|load failed/i.test(e.message)) return NETWORK_MESSAGE;
  if (e instanceof Error) return e.message;
  return String(e).replace(/^Error:\s*/, "");
}
