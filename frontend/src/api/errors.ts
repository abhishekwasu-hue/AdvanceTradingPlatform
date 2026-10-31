import { rememberServerError } from "../support/lastError";

/** P1.1: every failed API call becomes an `ApiError` whose message is a sentence a trader can act on - never a raw
 * status line, a JSON body, an HTML error page from the proxy or a developer hint. The status, the server's own
 * detail and the request id stay on the error for code that branches on them (step-up, feature switched off).
 * `String(error)` gives the message alone (no "Error: " prefix), so the many `setError(errorSentence(e))` call sites show
 * the friendly text without changes. */
export class ApiError extends Error {
  readonly status: number;
  /** The server's `detail` when it sent a readable one ("" otherwise). */
  readonly detail: string;
  /** The raw response body (trimmed), for code that matches on a machine code inside it. */
  readonly body: string;
  readonly requestId: string | null;
  /** BT1: the `X-Data-Tier` header of a refused market-data read ("C": end-of-day data only on this plan), else null. */
  readonly dataTier: string | null;
  /** REV-H1: the server's machine reason (`detail.code`, e.g. "duplicate_active_bot"), else null. */
  readonly code: string | null;

  constructor(status: number, message: string, detail = "", body = "", requestId: string | null = null, dataTier: string | null = null,
    code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.body = body;
    this.requestId = requestId;
    this.dataTier = dataTier;
    this.code = code;
  }

  override toString(): string {
    return this.message;
  }
}

export const NETWORK_MESSAGE = "Cannot reach the server. Check your connection and try again.";
/** UX1 (audit problem 4): a 404 without a sentence of its own - the server's bare "Not found" never reaches a trader. */
export const NOT_AVAILABLE_MESSAGE = "This is not available right now. Try again later.";
/** UX1 (handover 8b): a feature the operator has switched off - never its flag name, never a reference id. */
export const COMING_SOON_MESSAGE = "This feature is coming soon.";
/** UX1: an unhandled server error - what happened and what to do, without the status code. */
export const SERVER_ERROR_MESSAGE = "Something went wrong on our side. Try again in a moment.";

/** The server's generic "Not found" / "Not Found" (FastAPI's unmatched route, a route with no sentence of its own). */
const BARE_NOT_FOUND = /^not\s*found\.?$/i;
/** `require_flag`'s refusal: "The '<flag>' feature is currently disabled by the platform operator" (503). */
const FEATURE_OFF = /feature is (currently )?disabled/i;

/** True for a refusal because an operator feature switch is off (503 with X-Feature-Disabled, or its sentence). */
export function isFeatureOff(status: number, detail: string, featureHeader: string | null = null): boolean {
  return status === 503 && (!!featureHeader || FEATURE_OFF.test(detail));
}

const GENERIC: Record<number, string> = {
  400: "The request was not accepted. Check the values and try again.",
  401: "Your session has ended. Sign in again from the Account page.",
  402: "This needs a higher plan.",
  403: "You do not have permission to do this.",
  404: NOT_AVAILABLE_MESSAGE,
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
      const o = d as { message?: unknown; detail?: unknown; errors?: unknown };
      const m = o.message ?? o.detail;
      if (typeof m === "string") {
        // Keep the reasons a detail object carries (e.g. a provider's list of errors), not just its headline.
        const reasons = Array.isArray(o.errors) ? o.errors.filter((x): x is string => typeof x === "string" && x.trim() !== "") : [];
        return reasons.length ? `${m.trim()}: ${reasons.slice(0, 3).join("; ")}${reasons.length > 3 ? ` (+${reasons.length - 3} more)` : ""}` : m.trim();
      }
    }
  } catch {
    /* intentional: not JSON */
  }
  return "";
}

/** REV-H1: one plain sentence, with the next step, per machine reason the server names in `detail.code`. A reason not
 * listed here shows the server's own sentence (`detail.message`). */
export const REASON_SENTENCES: Readonly<Record<string, string>> = {
  // Starting a bot (POST /deployments): each refusal has its own sentence, never "already runs" for all of them.
  no_instrument_master: "The contracts for this symbol are not loaded yet, so the bot cannot pick one. Ask your admin to sync the instrument list, or check the symbol.",
  no_broker: "Connect your broker on the Broker page first: even a trading bot on paper reads live prices through your broker.",
  broker_login_needed: "Your broker login is not active. Log in again on the Broker page, then try again.",
  broker_account_off: "This broker account is switched off. Switch it on on the Broker page, then try again.",
  duplicate_active_bot: "Nothing new was started: this strategy already runs for this symbol in your organisation. Stop that bot first, or pick another strategy or symbol.",
};

/** The machine reason of a FastAPI error body (`{"detail": {"code": "..."}}`), else null. */
export function readableCode(body: string): string | null {
  const text = body.trim();
  if (!text.startsWith("{")) return null;
  try {
    const d = (JSON.parse(text) as { detail?: unknown }).detail;
    const c = d && typeof d === "object" && !Array.isArray(d) ? (d as { code?: unknown }).code : undefined;
    return typeof c === "string" && c.trim() ? c.trim() : null;
  } catch {
    return null;
  }
}

/** The sentence for a machine reason, or null when it has none of its own. */
export function reasonSentence(code: string | null | undefined): string | null {
  return code && Object.prototype.hasOwnProperty.call(REASON_SENTENCES, code) ? REASON_SENTENCES[code] : null;
}

/** Builds the error for a non-OK response. Server errors without a readable detail (an unhandled exception, a proxy
 * 502/504 page) get a generic sentence plus the request id, which is what support needs to find the log line. */
export async function apiErrorFrom(response: Response): Promise<ApiError> {
  let body = "";
  try { body = (await response.text()).trim(); } catch { /* intentional: body unreadable */ }
  const status = response.status;
  const requestId = response.headers.get("x-request-id");
  const detail = readableDetail(body);
  const featureOff = isFeatureOff(status, detail, response.headers.get("x-feature-disabled"));
  // UX1: a bare "Not found" and a switched-off feature never show the server's text (the detail and body stay on the
  // error for code that branches on them). REV-H1: a named machine reason shows its own sentence.
  const code = readableCode(body);
  let message = featureOff ? COMING_SOON_MESSAGE : status === 404 && BARE_NOT_FOUND.test(detail) ? NOT_AVAILABLE_MESSAGE
    : reasonSentence(code) ?? detail;
  if (!message) {
    message = status >= 500 ? SERVER_ERROR_MESSAGE : GENERIC[status] ?? `The request failed (error ${status}).`;
  }
  // Every server error carries the reference support needs to find the log line, readable detail or not - except a
  // switched-off feature, which is not an error (handover 8b: "Coming soon", never a reference id).
  if (status >= 500 && requestId && !featureOff) {
    message += `${/[.!?]$/.test(message) ? "" : "."} Reference: ${requestId}.`;
    rememberServerError(requestId);        // C12: "Report a problem" can attach it
  }
  return new ApiError(status, message, detail, body.slice(0, 2000), requestId, response.headers.get("x-data-tier"), code);
}

/** The message to show for any thrown value: an ApiError's sentence, a network failure as plain words, otherwise the
 * error's own message without the "Error: " prefix. */
export function friendlyError(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof TypeError && /fetch|network|load failed/i.test(e.message)) return NETWORK_MESSAGE;
  if (e instanceof Error) return e.message;
  return String(e).replace(/^Error:\s*/, "");
}

/** UX1: the one sentence an error state shows for anything a page caught - friendlyError, plus the raw texts an older
 * caller may still hold as a string (a bare "Not found", require_flag's sentence with the flag name). */
export function errorSentence(e: unknown): string {
  const reason = e instanceof ApiError ? reasonSentence(e.code ?? readableCode(e.body)) : null;
  if (reason) return reason;
  const text = friendlyError(e).trim();
  if (!text || BARE_NOT_FOUND.test(text) || /^not found\. it may have been removed\.?$/i.test(text)) return NOT_AVAILABLE_MESSAGE;
  if (FEATURE_OFF.test(text)) return COMING_SOON_MESSAGE;
  // REV-1 M2: a raw exception line anywhere in the text ("No candles: NIFTY: TypeError: ...") is developer text.
  if (RAW_EXCEPTION.test(text) || RAW_EXCEPTION_INSIDE.test(text)) return SOMETHING_FAILED_MESSAGE;
  return text;
}

/** REV-1 M2: what a page shows when the only text it holds is a raw exception. */
export const SOMETHING_FAILED_MESSAGE = "Something went wrong. Try again in a moment.";
/** A raw exception line ("TypeError: x is undefined", "BrokerAPIError: 401 ...") or a stack trace - never shown. */
const RAW_EXCEPTION = /^\s*[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Timeout)\b\s*(:|$)|Traceback \(most recent call last\)/;
/** The same class-name line after other words ("...: KeyError: 'x'"). */
const RAW_EXCEPTION_INSIDE = /[:;]\s+[A-Z]\w*(Error|Exception)\s*:/;
/** Machine codes a batch answer uses as a per-item `error` ("budget used"): callers branch on them, never show them. */
const MACHINE_CODE = /^[a-z_]+( [a-z_]+)?$/;

/** REV-1 M2: the server's own per-item reason (a symbol's `error` in a batch answer) when it is a sentence a trader can
 * read, else "" - a raw exception, a stack trace, a machine code or an empty value never reaches the screen. Callers
 * show the reason, or their own plain fallback when this is "" (see `serverReasonOr`). */
export function serverReason(reason: unknown): string {
  if (typeof reason !== "string") return "";
  const text = reason.trim();
  if (!text || RAW_EXCEPTION.test(text) || RAW_EXCEPTION_INSIDE.test(text) || MACHINE_CODE.test(text) || BARE_NOT_FOUND.test(text)) return "";
  if (FEATURE_OFF.test(text)) return COMING_SOON_MESSAGE;
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

/** The server's reason as the one sentence to show, or `fallback` when it sent none a trader can read. */
export function serverReasonOr(reason: unknown, fallback: string): string {
  return serverReason(reason) || fallback;
}
