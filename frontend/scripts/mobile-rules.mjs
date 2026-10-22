// Mobile B1 (docs/design/MOBILE_APP_PLAN.md §3.2, §4.2; ADR-0028 decision 10): the rules every Android build follows,
// in one place. Plain JavaScript without dependencies, so capacitor.config.ts, vite.config.ts, the CI check
// (scripts/check-mobile-config.mjs) and the unit tests (test/mobile-rules.test.ts) all read the same code.

/** MA-6 (owner, 2026-10-20): the temporary package id of the first builds, never published to any store. Kept so a
 * release build can still refuse it (and Gradle's copy in android/app/atp-build-rules.gradle). */
export const TEMPORARY_APP_ID = "dev.atp.unpublished";

/** MA-6 (owner, 2026-10-21): the final package id, the reverse of the production domain (MA-1). Debug builds add
 * ".debug". capacitor.config.ts `appId`; android/app/build.gradle `namespace` / `applicationId`, strings.xml and the
 * MainActivity package must match (scripts/check-mobile-config.mjs checks). Permanent once on Play: changing it means a
 * new install on every phone and a new Firebase registration (MA-2). */
export const APP_ID = "tech.aialgodesk.app";

/** MA-6 (owner, 2026-10-21): the API origin of the builds that talk to a real server (internal, release) when nothing
 * else is set - the production domain, https only. At build time, in this order: VITE_API_BASE_URL (an https origin),
 * then ATP_APP_DOMAIN (a host name, the GitHub repository variable), then this default. Whatever is chosen passes the
 * same checks (https, no IP, origin only). A debug build uses none of them (ADR-0028 decision 10): it keeps the
 * `.invalid` placeholder below. */
export const PRODUCTION_API_ORIGIN = "https://aialgodesk.tech";

/** Written into the native web build (dist/) by vite.config.ts: which build kind and API origin it was made for, so
 * `cap sync`, the CI check and Gradle can refuse a mismatch (a production bundle inside a debuggable APK). */
export const NATIVE_BUILD_MARKER = "atp-native-build.json";

/** The API origin of a debuggable build. `.invalid` never resolves (RFC 6761), so such an APK can never reach a
 * server: it is a layout smoke test only (ADR-0028 decision 10: a debuggable APK never points at production). */
export const PLACEHOLDER_API_ORIGIN = "https://atp-not-configured.invalid";

/**
 * The Android build kinds (`ATP_ANDROID_BUILD`, default "debug"):
 * - debug: debuggable (Android debug key, WebView debugging, Capacitor debug logging); placeholder API origin only.
 * - internal: not debuggable, never published, for the owner's phone; the real API origin. Its Gradle build type and
 *   non-public signing key come with the sign-in PR (ADR-0028 decisions 9-10), not with B1.
 * - release: signed for distribution (B4); the real API origin and never the temporary package id.
 */
export const BUILD_KINDS = ["debug", "internal", "release"];

const APP_ID_PATTERN = /^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/;

/** True for an IPv4 or IPv6 literal host (a URL's `hostname`, which the URL parser has already normalised: shorthand
 * and decimal IPv4 forms become dotted quads, IPv6 is bracketed). */
export function isIpHost(hostname) {
  const h = String(hostname ?? "").replace(/^\[|\]$/g, "");
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(h)) return true;
  return h.includes(":");
}

/** The configured API base without surrounding blanks or trailing slashes; "" when unset. */
export function normaliseApiBase(value) {
  return String(value ?? "").trim().replace(/\/+$/, "");
}

/** Why `value` cannot be an API origin for the app (empty list = fine). An empty value is fine here; whether a build
 * may run without one is `nativeBuildProblems`' question. */
export function apiBaseProblems(value) {
  const base = normaliseApiBase(value);
  if (!base) return [];
  let url;
  try {
    url = new URL(base);
  } catch {
    return [`API base "${base}" is not a URL (expected https://<domain>).`];
  }
  const problems = [];
  if (url.protocol !== "https:") problems.push(`API base "${base}" must use https://.`);
  if (isIpHost(url.hostname)) problems.push(`API base "${base}" is an IP address; use the domain name (MA-1).`);
  const host = url.hostname.toLowerCase();
  if (host === "localhost" || host.endsWith(".localhost")) {
    problems.push(`API base "${base}" is localhost, which is the app's own origin on the phone.`);
  }
  if (url.username || url.password) problems.push(`API base "${base}" must not carry a user name or password.`);
  if (url.pathname !== "/" || url.search || url.hash) {
    problems.push(`API base "${base}" must be an origin only (no path, query or fragment); the app adds /api/v1.`);
  }
  return problems;
}

/** The API base from the GitHub variable `ATP_APP_DOMAIN` (a host name such as atp.example.com): "" when unset. */
export function apiBaseFromDomain(domain) {
  const d = String(domain ?? "").trim().replace(/\.$/, "");
  if (!d) return "";
  if (/[/:@?#\s]/.test(d)) {
    throw new Error(`ATP_APP_DOMAIN "${d}" must be a host name only (no scheme, port, path or spaces).`);
  }
  return `https://${d}`;
}

/** Reads and checks `ATP_ANDROID_BUILD`. */
export function buildKindFrom(env) {
  const kind = String(env?.ATP_ANDROID_BUILD ?? "").trim() || "debug";
  if (!BUILD_KINDS.includes(kind)) {
    throw new Error(`ATP_ANDROID_BUILD "${kind}" is not one of ${BUILD_KINDS.join(", ")}.`);
  }
  return kind;
}

/** Everything that makes this Android build unsafe or wrong (empty list = fine). */
export function nativeBuildProblems({ kind, appId, apiBase }) {
  const problems = [];
  const base = normaliseApiBase(apiBase);
  if (!BUILD_KINDS.includes(kind)) problems.push(`Unknown Android build kind "${kind}".`);
  if (!APP_ID_PATTERN.test(String(appId ?? ""))) problems.push(`Package id "${appId}" is not a valid Android application id.`);
  problems.push(...apiBaseProblems(base));
  if (kind === "debug" && base && base !== PLACEHOLDER_API_ORIGIN) {
    problems.push(
      `A debug (debuggable) build must not point at a real server, but VITE_API_BASE_URL is "${base}" ` +
      "(ADR-0028 decision 10). Leave it unset for a debug build.",
    );
  }
  if (kind !== "debug" && (!base || base === PLACEHOLDER_API_ORIGIN)) {
    problems.push(`A ${kind} build needs the real API origin: leave VITE_API_BASE_URL unset (${PRODUCTION_API_ORIGIN}) or set it to the server's https origin.`);
  }
  if (kind === "release" && (appId === TEMPORARY_APP_ID || String(appId).startsWith(`${TEMPORARY_APP_ID}.`))) {
    problems.push(
      `A release build cannot use the temporary package id "${TEMPORARY_APP_ID}" (MA-6): it is never published. ` +
      "Set the final id (com.<domain>.app) first.",
    );
  }
  return problems;
}

/** The API origin a build kind uses when VITE_API_BASE_URL is not set: the placeholder for a debug build, the
 * production domain otherwise. */
export function defaultApiBase(kind) {
  return kind === "debug" ? PLACEHOLDER_API_ORIGIN : PRODUCTION_API_ORIGIN;
}

/** The checked settings of one Android build, from the environment. Throws with every problem at once.
 * API origin: VITE_API_BASE_URL, else (not for a debug build) https://<ATP_APP_DOMAIN>, else defaultApiBase(kind). */
export function resolveNativeBuild(env, appId) {
  const kind = buildKindFrom(env);
  const configured = normaliseApiBase(env?.VITE_API_BASE_URL) || (kind === "debug" ? "" : apiBaseFromDomain(env?.ATP_APP_DOMAIN));
  const apiBase = configured || defaultApiBase(kind);
  const problems = nativeBuildProblems({ kind, appId, apiBase });
  if (problems.length) throw new Error(`Android build refused:\n- ${problems.join("\n- ")}`);
  return {
    kind,
    appId,
    apiBase,
    /** Only a debug build may log bridge calls or allow WebView debugging (ADR-0028 decision 10). */
    debuggable: kind === "debug",
  };
}
