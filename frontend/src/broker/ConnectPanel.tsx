import { Check, CircleCheck, Copy, ExternalLink, Info, TriangleAlert } from "lucide-react";
import { Fragment, useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api/client";
import { ApiError, NETWORK_MESSAGE } from "../api/errors";
import { loginErrorText } from "../ui/brokerSession";
import { copyRedirectUrl } from "../components/UpstoxRedirectUrl";
import type { BrokerCatalogEntry, BrokerCatalogField, BrokerGuide, BrokerGuideStep, BrokerTokenInfo } from "../types";
import { formatDateIST } from "../ui/format";
import { BTN_PRIMARY, BTN_SECONDARY, BrokerLogo, ResponsiveDialog } from "./parts";
import { entryFor, useBrokerCatalog } from "./useBrokerCatalog";
import { Skeleton } from "../components/primitives";
import UpstoxOrderLogin from "./UpstoxOrderLogin";

/**
 * P2b: the Connect panel - a small modal on a wide screen, a sheet on a phone. It holds ONLY:
 * - BRK-1 C: the broker's step-by-step guide from the catalog - open on the first connect, folded on a Renew: one line
 *   per warning, the shared SEBI line, then numbered steps, each broker-side step with its official page (new tab) and a
 *   Copy button for a value the user copies INTO the broker (ATP's redirect address, the server IP: GET /broker/specs);
 * - the fields the catalog lists for this broker (labels from the catalog, "From step N" and its short hint under each);
 * - one "Connect" button.
 * The answer is "Connected ✓" or the server's one plain sentence.
 *
 * Renew (the Broker page's row button, the Dashboard banner, the sign-in prompt) opens the same panel with only the
 * catalog's `renew_fields`, sent to POST /renew. BRK-1 B: Zerodha and Fyers ("oauth_redirect") have ONE button, "Log in":
 * the broker's own page opens in a new tab and returns to ATP's callback, which finishes the login - nothing is pasted;
 * the TOTP brokers (Angel One, Shoonya, Kotak Neo, Groww, Dhan) are logged in by ATP each morning, and Renew is one click.
 * A broker's hidden fallback way (Dhan's pasted token; the pasted login address of Zerodha / Fyers) sits behind its own
 * link. Upstox ("analytics_token"): while the saved Analytics Token is valid the panel shows "saved, valid till <date>"
 * and Replace (M85); the token field (through /connect) appears only on Replace or once no valid token is saved.
 * A Zerodha / Fyers connect stores the key and secret and answers "needs_login": the panel then moves to that
 * "Log in" step by itself.
 */

/**
 * IMAGES: the spot for the guide's pictures, per broker id - e.g. `upstox: ["/img/how-to/upstox-1.png"]`.
 * Empty on purpose (BRK-1, docs/brokers/EASIEST_LOGIN.md): no broker's page grants a licence to reuse its screenshots,
 * and mock-ups are never used. Add real, licensed files under public/ and list them here; the panel shows them under
 * the steps.
 */
export const HOW_TO_IMAGES: Readonly<Record<string, readonly string[]>> = {};

/** BRK-1 C: the values the guide asks the user to copy INTO the broker; null / missing = not known yet. */
export interface CopyValues { redirect_url?: string | null; static_ip?: string | null }

/** The catalog's guide; an older server without one gets its plain `how_to` lines as steps. */
export function guideOf(entry: Pick<BrokerCatalogEntry, "guide" | "how_to">): BrokerGuide {
  if (entry.guide) return entry.guide;
  return { warnings: [], info: "", steps: (entry.how_to ?? []).map((text) => ({ text, url: null, gives: null, copy: null, where: "atp" as const })) };
}

/** The guide step (1-based) that produces each field key ("From step N" under the field). */
export function stepNumbers(guide: Pick<BrokerGuide, "steps">): Record<string, number> {
  const out: Record<string, number> = {};
  guide.steps.forEach((st, i) => { if (st.gives && !(st.gives in out)) out[st.gives] = i + 1; });
  return out;
}

/** A guide line with its **key words** in bold (the catalog's only mark-up). */
export function boldText(text: string): ReactNode[] {
  return text.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 === 1 ? <strong key={i} className="font-semibold text-fg">{part}</strong> : <Fragment key={i}>{part}</Fragment>));
}

const COPY_LABEL: Record<NonNullable<BrokerGuideStep["copy"]>, string> = { redirect_url: "ATP's address", static_ip: "ATP's server IP" };

/** One value to copy into the broker, with its Copy button (the address is selected when the clipboard is refused). */
function CopyValue({ kind, value }: { kind: NonNullable<BrokerGuideStep["copy"]>; value: string | null | undefined }) {
  const [state, setState] = useState<"idle" | "copied" | "selected">("idle");
  const id = useId();
  if (!value) return <p data-guide-copy={kind} className="text-t12 text-fg-muted">{COPY_LABEL[kind]}: not set yet.</p>;
  const copy = async () => {
    const clipboard = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
    setState(await copyRedirectUrl(value, clipboard, () => {
      const el = document.getElementById(id);
      const range = document.createRange();
      if (el) { range.selectNodeContents(el); window.getSelection()?.removeAllRanges(); window.getSelection()?.addRange(range); }
    }));
  };
  return (
    <div data-guide-copy={kind} className="mt-space-1 flex items-center gap-space-2 rounded-control border border-subtle bg-surface-1 py-space-1 pl-space-2 pr-space-1">
      <code id={id} className="min-w-0 flex-1 break-all font-mono text-t12 text-fg">{value}</code>
      <button type="button" onClick={() => void copy()} data-copy-button={kind} aria-label={`Copy ${COPY_LABEL[kind]}`}
        className="inline-flex h-11 shrink-0 items-center gap-space-1 rounded-control px-space-3 text-t13 font-semibold text-brand hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        {state === "copied" ? <><Check size={14} aria-hidden /> Copied</> : <><Copy size={14} aria-hidden /> Copy</>}
      </button>
    </div>
  );
}

/** BRK-1 C: the guide itself - warnings first, the SEBI line, the numbered steps, the (empty) picture slot. */
export function GuideSteps({ broker, name, guide, copyValues = {} }: { broker: string; name: string; guide: BrokerGuide; copyValues?: CopyValues }) {
  const images = HOW_TO_IMAGES[broker] ?? [];
  return (
    <div className="space-y-space-3">
      {(guide.warnings.length > 0 || guide.info) && (
        <ul className="space-y-space-1 text-t13">
          {guide.warnings.map((w) => (
            <li key={w} data-guide-warning className="flex items-start gap-space-2 text-fg">
              <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" /><span>{boldText(w)}</span>
            </li>
          ))}
          {guide.info && (
            <li data-guide-info className="flex items-start gap-space-2 text-fg-muted">
              <Info size={16} aria-hidden className="mt-0.5 shrink-0 text-brand" /><span>{boldText(guide.info)}</span>
            </li>
          )}
        </ul>
      )}
      <ol className="space-y-space-1" data-guide-steps>
        {guide.steps.map((st, i) => {
          const n = i + 1;
          const newPage = !!st.url && st.url !== guide.steps[i - 1]?.url;
          return (
            <li key={`${n}-${st.text}`} data-guide-step={n} data-where={st.where} className="flex items-start gap-space-2 text-t13 text-fg-muted">
              <span aria-hidden className="mt-space-2 inline-flex size-6 shrink-0 items-center justify-center rounded-full bg-surface-3 text-t12 font-semibold tabular-nums text-fg">{n}</span>
              <div className="min-w-0 flex-1 py-space-2">
                <p><span className="sr-only">Step {n}: </span>{boldText(st.text)}</p>
                {st.copy && <CopyValue kind={st.copy} value={copyValues[st.copy]} />}
              </div>
              {st.url && (
                <a href={st.url} target="_blank" rel="noopener noreferrer" data-guide-link
                  aria-label={`Open the ${name} page for step ${n} (new tab)`}
                  className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-space-1 rounded-control px-space-2 text-t13 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                  {newPage && <span aria-hidden>Open</span>}<ExternalLink size={14} aria-hidden />
                </a>
              )}
            </li>
          );
        })}
      </ol>
      {/* IMAGES: pictures for these steps go here (HOW_TO_IMAGES above). */}
      {images.length > 0 && (
        <div data-how-to-images className="grid grid-cols-1 gap-space-2">
          {images.map((src, i) => <img key={src} src={src} alt={`${name}, step ${i + 1}`} className="w-full rounded-control border border-subtle" />)}
        </div>
      )}
    </div>
  );
}

export type PanelMode = "connect" | "renew";

/** What the panel asks for and where it sends it. */
export interface PanelStep {
  endpoint: "connect" | "renew";
  fields: readonly BrokerCatalogField[];
  /** A "Log in" button that opens the broker's own login page first (oauth_redirect, or the paste way). */
  login: boolean;
  /** BRK-1 B: the broker's page returns to ATP's callback by itself - "Log in" is the one button, nothing is pasted. */
  oauth: boolean;
  /** A submit button (false: the oauth login, whose "Log in" is the only action). */
  submit: boolean;
  /** The submit button's words. */
  action: "Connect" | "Renew";
}

type StepEntry = Pick<BrokerCatalogEntry, "method" | "fields" | "renew_fields"> & Partial<Pick<BrokerCatalogEntry, "fallback">>;

/** BRK-1 B: whether this step offers the hidden fallback way (its own link): only where that way asks for fields. */
export function fallbackOffered(entry: StepEntry, mode: PanelMode, afterSave = false): boolean {
  const fb = entry.fallback;
  if (!fb || entry.method === "analytics_token") return false;
  return mode === "connect" && !afterSave ? fb.fields.length > 0 : fb.renew_fields.length > 0;
}

/** The step for a broker: connecting asks for the catalog's fields; renewing for its `renew_fields` (see above).
 * `afterSave`: a Zerodha / Fyers connect was just saved, so the next step is today's login, still called Connect.
 * `fallback` (BRK-1 B): the hidden way - its connect fields ([]: the usual ones) or its renew fields; for Zerodha / Fyers
 * that is the older login: open the page, paste the address. */
export function stepFor(entry: StepEntry, mode: PanelMode, afterSave = false, fallback = false): PanelStep {
  const fb = fallback && fallbackOffered(entry, mode, afterSave) ? entry.fallback ?? null : null;
  if (mode === "connect" && !afterSave) {
    return { endpoint: "connect", fields: fb && fb.fields.length > 0 ? fb.fields : entry.fields, login: false, oauth: false, submit: true, action: "Connect" };
  }
  if (entry.method === "analytics_token") return { endpoint: "connect", fields: entry.fields, login: false, oauth: false, submit: true, action: "Renew" };
  const oauth = entry.method === "oauth_redirect" && !fb;
  const login = entry.method === "login_code" || entry.method === "oauth_redirect";
  return { endpoint: "renew", fields: fb ? fb.renew_fields : entry.renew_fields ?? [], login, oauth, submit: !oauth, action: afterSave ? "Connect" : "Renew" };
}

/**
 * M85 U5: the saved Upstox analytics token of this account when it is still valid (its expiry, ISO), else null. The
 * renew panel then shows "saved, valid till <date>" with Replace - the token field only on Replace or after expiry.
 */
export function savedAnalytics(rows: readonly BrokerTokenInfo[] | null | undefined, broker: string, accountLabel: string,
  now: Date = new Date()): { expiresAt: string } | null {
  const row = (rows ?? []).find((r) => r.broker_name === broker && (r.account_label || "primary") === accountLabel);
  if (!row?.analytics_token_set || row.analytics_token_expired || !row.analytics_token_expires_at) return null;
  return new Date(row.analytics_token_expires_at).getTime() > now.getTime() ? { expiresAt: row.analytics_token_expires_at } : null;
}

/** M85 U5: the Upstox renew panel while its analytics token is valid - nothing to paste, nothing to log in to (BRK-1:
 * Upstox connects with the pasted Analytics Token only, no Upstox OAuth flow). Replace shows the token field. */
export function UpstoxSavedView({ expiresAt, onReplace }: { expiresAt: string; onReplace: () => void }) {
  return (
    <div className="space-y-space-4" data-upstox-saved>
      <div className="flex items-center gap-space-3">
        <BrokerLogo name="Upstox" />
        <span className="text-t15 font-semibold text-fg">Upstox</span>
      </div>
      <p role="status" className="flex items-start gap-space-2 text-t14 text-fg">
        <CircleCheck size={18} aria-hidden className="mt-0.5 shrink-0 text-up" />
        <span>Analytics token saved, valid till <span className="font-semibold tabular-nums">{formatDateIST(expiresAt, { year: true })}</span></span>
      </p>
      <button type="button" onClick={onReplace} data-upstox-replace className={`${BTN_SECONDARY} w-full`}>Replace token</button>
    </div>
  );
}

export type PanelResult = { ok: true } | { ok: false; text: string };

/**
 * A failed request (the call itself, not a P2a answer) as one fixed sentence per status (review M4, like
 * brokerSession.loginErrorText): the server's own text is never shown here, as it may name internals. A connect or
 * renew that the server answered (HTTP 200) shows its `message`, which P2a writes for display.
 */
export function failureText(e: unknown, brokerName = "the broker", action: "connect" | "remove" = "connect"): string {
  if (e instanceof ApiError) {
    if (e.status === 0 || e.message === NETWORK_MESSAGE) return NETWORK_MESSAGE;
    switch (e.status) {
      case 401: return "Your sign-in has ended. Sign in again, then try again.";
      case 403:
        if (/two-factor|step-up|authenticator/i.test(`${e.detail} ${e.body}`)) {
          return "This needs a fresh two-factor check. Confirm it with your authenticator app, then try again.";
        }
        if (/verif/i.test(`${e.detail} ${e.body}`)) return "Verify your email address first (Account page), then try again.";
        return "You do not have permission to change brokers. Ask the owner of your organisation.";
      case 404: return action === "remove" ? `${brokerName} is no longer connected.` : `${brokerName} cannot be connected here.`;
      case 409: return "This connection changed at the same moment somewhere else. Try again.";
      case 429: return "Too many attempts. Wait a minute, then try again.";
      default: return action === "remove" ? `${brokerName} could not be removed. Try again in a moment.` : "That did not go through. Try again in a moment.";
    }
  }
  if (e instanceof TypeError) return NETWORK_MESSAGE;
  return "That did not go through. Try again in a moment.";
}

/** True when every field has a value (spaces alone do not count). No fields: nothing to fill in (one click). */
export function complete(fields: readonly Pick<BrokerCatalogField, "key">[], values: Readonly<Record<string, string>>): boolean {
  return fields.every((f) => (values[f.key] ?? "").trim() !== "");
}

/**
 * UX3 fix 6 (UX audit P02, handover 8f "1-2 fields per broker"): the catalog fields a beginner should not meet first - the
 * Fyers "Redirect URL" and the Angel One / Shoonya "TOTP secret". With `ui_v2` they sit under the panel's own
 * "Advanced" fold (still required: Connect stays off until they are filled, and a line says where the missing one is).
 */
export const ADVANCED_FIELD_KEYS: ReadonlySet<string> = new Set(["redirect_uri", "totp_secret"]);

/** The panel's fields split into the ones shown first and the ones under Advanced. Nothing is folded when no field
 * would stay outside the fold. */
export function splitFields(fields: readonly BrokerCatalogField[], fold: ReadonlySet<string>): { main: BrokerCatalogField[]; advanced: BrokerCatalogField[] } {
  const main = fields.filter((f) => !fold.has(f.key));
  const advanced = fields.filter((f) => fold.has(f.key));
  return main.length === 0 ? { main: [...fields], advanced: [] } : { main, advanced };
}

/** After "Log in": the broker's page opened in a new tab (or the browser blocked it - then the link). */
export interface LoginOpened { link: string; opened: boolean }

export interface ConnectPanelViewProps {
  entry: BrokerCatalogEntry;
  step: PanelStep;
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onSubmit: () => void;
  busy: boolean;
  result: PanelResult | null;
  /** The server's sentence about the next step (a saved Zerodha / Fyers key: "now press Log in ..."). */
  note?: string | null;
  onLogin?: () => void;
  loginBusy?: boolean;
  loginOpened?: LoginOpened | null;
  onDone?: () => void;
  /** Catalog field keys shown under "Advanced" (UX3 fix 6); none by default. */
  fold?: ReadonlySet<string>;
  /** BRK-1 B: the hidden fallback way's link (its words), whether it is in use, and the switch; no link when left out. */
  fallbackLabel?: string | null;
  usingFallback?: boolean;
  onToggleFallback?: () => void;
  /** BRK-1 C: the values the guide's Copy buttons copy (ATP's redirect address, the server IP). */
  copyValues?: CopyValues;
}

const NO_FOLD: ReadonlySet<string> = new Set();

/** The panel's body - no dialog and no fetching, so tests render it directly. */
export function ConnectPanelView({ entry, step, values, onChange, onSubmit, busy, result, note = null, onLogin, loginBusy = false, loginOpened = null, onDone, fold = NO_FOLD, fallbackLabel = null, usingFallback = false, onToggleFallback, copyValues }: ConnectPanelViewProps) {
  const ids = useId();
  const submit = (e: FormEvent) => { e.preventDefault(); if (!busy) onSubmit(); };
  const guide = guideOf(entry);
  const fromStep = stepNumbers(guide);
  // BRK-1 C: open on the first connect; folded on a Renew (and on the login step after a Zerodha / Fyers save).
  const firstConnect = step.endpoint === "connect" && step.action === "Connect";
  const guideBody = guide.steps.length > 0
    ? <GuideSteps broker={entry.broker} name={entry.name} guide={guide} copyValues={copyValues} />
    : null;
  const busyWord = step.action === "Renew" ? "Renewing" : "Connecting";
  const { main, advanced } = splitFields(step.fields, fold);
  // The fields shown first are filled but one under Advanced is not: say where it is (Connect stays off until then).
  const advancedMissing = advanced.length > 0 && complete(main, values) && !complete(advanced, values);
  const field = (f: BrokerCatalogField) => {
    const i = step.fields.indexOf(f);
    return (
      <div key={f.key} className="space-y-space-1">
        <label htmlFor={`${ids}-${i}`} className="flex flex-wrap items-baseline justify-between gap-x-space-2 text-t13 font-medium text-fg">
          <span>{f.label}</span>
          {fromStep[f.key] && <span data-from-step={fromStep[f.key]} className="text-t12 font-normal text-fg-muted">From step {fromStep[f.key]}</span>}
        </label>
        <input id={`${ids}-${i}`} name={f.key} data-field={f.key}
          type={f.kind === "secret" ? "password" : "text"}
          autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false}
          aria-describedby={f.placeholder ? `${ids}-${i}-hint` : undefined}
          value={values[f.key] ?? ""}
          onChange={(e) => onChange(f.key, e.target.value)}
          className="h-11 w-full rounded-control border border-border bg-surface-2 px-space-3 text-sm text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:text-t14" />
        {f.placeholder && <p id={`${ids}-${i}-hint`} className="text-t12 text-fg-muted">{f.placeholder}</p>}
      </div>
    );
  };
  return (
    <form onSubmit={submit} className="space-y-space-4" data-connect-panel={entry.broker} data-endpoint={step.endpoint}>
      <div className="flex items-center gap-space-3">
        <BrokerLogo name={entry.name} />
        <span className="text-t15 font-semibold text-fg">{entry.name}</span>
      </div>

      {note && <p role="status" data-note className="text-t14 text-fg">{note}</p>}

      {guideBody && (firstConnect ? (
        <section data-broker-guide={entry.broker} data-guide-open aria-label={`How to connect ${entry.name}`}
          className="rounded-control border border-subtle bg-surface-2 px-space-3 py-space-3">
          {guideBody}
        </section>
      ) : (
        <details data-broker-guide={entry.broker} className="rounded-control border border-subtle bg-surface-2">
          <summary className="flex min-h-11 cursor-pointer list-none items-center px-space-3 text-t14 font-medium text-brand hover:underline [&::-webkit-details-marker]:hidden">
            Setup guide ({guide.steps.length} steps)
          </summary>
          <div className="px-space-3 pb-space-3">{guideBody}</div>
        </details>
      ))}

      {step.login && (
        <div className="space-y-space-2">
          <button type="button" onClick={onLogin} disabled={loginBusy || !!result?.ok} aria-busy={loginBusy || undefined} data-login
            data-login-way={step.oauth ? "callback" : "paste"} className={`${step.oauth ? BTN_PRIMARY : BTN_SECONDARY} w-full`}>
            <ExternalLink size={16} aria-hidden /> Log in
          </button>
          {step.oauth && !loginOpened && !result?.ok && (
            <p className="text-t13 text-fg-muted">Sign in on the {entry.name} page that opens. ATP finishes the login by itself.</p>
          )}
          {loginOpened && !result?.ok && (
            <p className="text-t13 text-fg-muted" data-login-opened>
              {step.oauth
                ? (loginOpened.opened
                  ? `The ${entry.name} login page opened in a new tab. Sign in there; ATP finishes the login by itself and this panel updates when you come back.`
                  : <>Your browser blocked the new tab. <a href={loginOpened.link} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">Open the {entry.name} login page</a> and sign in; ATP finishes the login by itself.</>)
                : (loginOpened.opened
                  ? `The ${entry.name} login page opened in a new tab. After you sign in, copy the address from the browser bar and paste it below.`
                  : <>Your browser blocked the new tab. <a href={loginOpened.link} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">Open the {entry.name} login page</a>, sign in, then paste the address from the browser bar below.</>)}
            </p>
          )}
        </div>
      )}

      {main.map(field)}

      {advanced.length > 0 && (
        <details className="rounded-control border border-subtle" data-advanced data-advanced-fields>
          <summary className="flex min-h-11 cursor-pointer list-none items-center px-space-3 text-t14 font-medium text-fg [&::-webkit-details-marker]:hidden">
            Advanced ({advanced.length === 1 ? "1 more field" : `${advanced.length} more fields`})
          </summary>
          <div className="space-y-space-3 px-space-3 pb-space-3">{advanced.map(field)}</div>
        </details>
      )}

      {result && (result.ok ? (
        <p role="status" data-result="ok" className="text-t14 font-semibold text-fg">Connected ✓</p>
      ) : (
        <p role="alert" data-result="error" className="flex items-start gap-space-2 rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn">
          <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0" /> <span>{result.text}</span>
        </p>
      ))}

      {advancedMissing && !result?.ok && (
        <p className="text-t13 text-fg-muted" data-advanced-missing>One more field is under Advanced.</p>
      )}

      {result?.ok ? (
        <button type="button" onClick={onDone} className={`${BTN_SECONDARY} w-full`}>Done</button>
      ) : step.submit ? (
        <button type="submit" disabled={busy || !complete(step.fields, values)} aria-busy={busy || undefined}
          className={`${BTN_PRIMARY} w-full`}>
          {busy ? `${busyWord}…` : step.action}
        </button>
      ) : null}

      {/* BRK-1 B: the hidden fallback way (Dhan's pasted token, the pasted login address) - only behind this link. */}
      {fallbackLabel && onToggleFallback && !result?.ok && (
        <button type="button" onClick={onToggleFallback} data-fallback-toggle={usingFallback ? "on" : "off"}
          className="inline-flex min-h-11 w-full items-center justify-center text-center text-t13 text-fg-muted underline-offset-2 hover:text-fg hover:underline">
          {usingFallback ? "Back to the usual way" : fallbackLabel}
        </button>
      )}
    </form>
  );
}

/** The panel for one broker: loads the catalog entry, sends connect / renew, and tells the caller when a connection
 * changed (`onChanged`, e.g. to re-read the connections). */
export default function ConnectPanel({ broker, mode, accountLabel, open, onOpenChange, onChanged }: {
  broker: string | null;
  mode: PanelMode;
  /** The account to connect or renew ("primary" when left out). */
  accountLabel?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged?: () => void;
}) {
  const { entries, failed, retry } = useBrokerCatalog();
  const entry = broker ? entryFor(entries, broker) : null;
  const label = accountLabel || "primary";
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PanelResult | null>(null);
  const [afterSave, setAfterSave] = useState<string | null>(null);   // the server's "now press Log in" sentence
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginOpened, setLoginOpened] = useState<LoginOpened | null>(null);
  // M85: the saved Upstox analytics token (renew only); undefined = not read yet, null = none valid.
  const [saved, setSaved] = useState<{ expiresAt: string } | null | undefined>(undefined);
  const [replacing, setReplacing] = useState(false);
  // Owner 6 Nov ("analytics token expire zalyashivay magu naye, yethe normal loginchich card open vhayla pahije"): every
  // Upstox panel - Connect, Connect again or Renew, from any page - first reads the saved Analytics Token; while it is
  // valid the normal Upstox login card opens instead of the token field (Replace stays one tap away).
  const upstoxRenew = entry?.method === "analytics_token";
  // BRK-1 C: the exact values the guide's Copy buttons copy (ATP's redirect address for this broker, the server IP).
  const [copyValues, setCopyValues] = useState<CopyValues>({});
  const needsCopy = !!entry?.guide?.steps.some((st) => st.copy);
  useEffect(() => {
    setCopyValues({});                                      // never another broker's address
    if (!open || !broker || !needsCopy) return;
    let live = true;
    api.brokerSpecs()
      .then(({ brokers }) => {
        const row = brokers.find((b) => b.name === broker);
        if (live) setCopyValues({ redirect_url: row?.readonly?.redirect_url ?? null, static_ip: row?.readonly?.server_ip ?? null });
      })
      .catch(() => { if (live) setCopyValues({}); });      // unknown: "not set yet" under the step
    return () => { live = false; };
  }, [open, broker, needsCopy]);
  useEffect(() => {
    setReplacing(false);
    if (!open || !upstoxRenew || !broker) { setSaved(undefined); return; }
    let live = true;
    api.brokerTokenStatus()
      .then((rows) => { if (live) setSaved(savedAnalytics(rows, broker, label)); })
      .catch(() => { if (live) setSaved(null); });          // unknown: the token field, as before
    return () => { live = false; };
  }, [open, upstoxRenew, broker, label]);

  // BRK-1 B: the hidden fallback way (its own link), off each time the panel opens.
  const [fallback, setFallback] = useState(false);

  // A new broker, or the panel opened again: start empty (secrets are never kept once the panel closes).
  useEffect(() => {
    setValues({}); setResult(null); setBusy(false); setAfterSave(null); setLoginBusy(false); setLoginOpened(null); setFallback(false);
  }, [broker, mode, open]);

  const step = entry ? stepFor(entry, mode, afterSave !== null, fallback) : null;
  const offersFallback = entry ? fallbackOffered(entry, mode, afterSave !== null) : false;

  // BRK-1 B: after "Log in" (Zerodha / Fyers), the broker returns to ATP's callback in the other tab; when this tab is
  // looked at again, the connection is read once, and the panel says "Connected ✓" when the login went through.
  const waitingForCallback = !!(open && entry && step?.oauth && loginOpened && !result?.ok);
  useEffect(() => {
    if (!waitingForCallback || !entry) return;
    let live = true;
    const check = () => {
      if (document.visibilityState === "hidden") return;
      api.brokerConnections()
        .then((rows) => {
          const row = rows.find((c) => c.broker === entry.broker && (c.account_label || "primary") === label);
          if (live && row?.status === "connected") { setResult({ ok: true }); onChanged?.(); }
        })
        .catch(() => {
          // Not known: say so once; the Broker page shows the outcome when it is read again.
          if (live) setResult({ ok: false, text: "Could not check the login just now. The Broker page shows it in a moment." });
        });
    };
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    return () => { live = false; window.removeEventListener("focus", check); document.removeEventListener("visibilitychange", check); };
  }, [waitingForCallback, entry, label, onChanged]);

  async function submit() {
    if (!entry || !step) return;
    setBusy(true);
    setResult(null);
    try {
      const fields = Object.fromEntries(step.fields.map((f) => [f.key, (values[f.key] ?? "").trim()]));
      const r = step.endpoint === "renew"
        ? await api.brokerRenew(entry.broker, fields, accountLabel)
        : await api.brokerConnect(entry.broker, fields, accountLabel);
      setValues({});
      onChanged?.();
      if (r.status === "connected") setResult({ ok: true });
      else if (r.status === "needs_login" && step.endpoint === "connect" && (entry.method === "login_code" || entry.method === "oauth_redirect")) {
        setAfterSave(r.message || `Saved. Now press Log in and sign in to ${entry.name}.`);
      } else setResult({ ok: false, text: r.message || "Not connected yet. Check the details and try again." });
    } catch (e) {
      setResult({ ok: false, text: failureText(e, entry.name) });
    } finally {
      setBusy(false);
    }
  }

  // "Log in": the tab opens inside the click (a tab opened after an await is blocked as a popup), then goes to the
  // broker's own login page. BRK-1 B: Zerodha / Fyers through GET /broker/{name}/oauth/start (the page returns to ATP's
  // callback, nothing pasted); the fallback way and older servers through GET /broker/{name}/login-url (then a paste).
  function login() {
    if (!entry || !step) return;
    setLoginBusy(true);
    setResult(null);
    let tab: Window | null = null;
    try { tab = window.open("about:blank", "_blank"); if (tab) tab.opener = null; } catch { tab = null; }
    const start = step.oauth && (entry.broker === "zerodha" || entry.broker === "fyers")
      ? api.brokerOAuthStart(entry.broker, label)
      : api.brokerLoginUrl(entry.broker, label);
    start
      .then(({ authorization_url }) => {
        if (tab) tab.location.href = authorization_url;
        setLoginOpened({ link: authorization_url, opened: !!tab });
      })
      .catch((e: unknown) => { tab?.close(); setResult({ ok: false, text: loginErrorText(e, entry.broker) }); })
      .finally(() => setLoginBusy(false));
  }

  const name = entry?.name ?? "";
  const title = `${mode === "renew" ? "Renew" : "Connect"} ${name}`.trim();
  if (upstoxRenew && saved && !replacing) {
    return <UpstoxOrderLogin open={open} onOpenChange={onOpenChange} accountLabel={label} onChanged={onChanged}
      savedUntil={saved.expiresAt} onReplaceToken={() => setReplacing(true)} />;
  }
  return (
    <ResponsiveDialog open={open} onOpenChange={onOpenChange} title={title} marker="connect">
      {upstoxRenew && saved === undefined ? (
        <Skeleton className="h-32" label="Loading" />
      ) : entry && step ? (
        <ConnectPanelView entry={entry} step={step} values={values} busy={busy} result={result} note={afterSave}
          onChange={(key, value) => setValues((v) => ({ ...v, [key]: value }))}
          onSubmit={() => void submit()} onDone={() => onOpenChange(false)}
          onLogin={login} loginBusy={loginBusy} loginOpened={loginOpened} copyValues={copyValues}
          fallbackLabel={offersFallback ? entry.fallback?.label ?? null : null} usingFallback={fallback}
          onToggleFallback={() => { setFallback((f) => !f); setValues({}); setResult(null); setLoginOpened(null); }} />
      ) : entries && broker ? (
        <p className="text-t14 text-fg-muted">This broker cannot be connected here yet.</p>
      ) : failed ? (
        <div className="space-y-space-3">
          <p className="text-t14 text-fg-muted">The list of brokers could not be loaded.</p>
          <button type="button" onClick={retry} className={BTN_SECONDARY}>Try again</button>
        </div>
      ) : (
        <Skeleton className="h-32" label="Loading" />
      )}
    </ResponsiveDialog>
  );
}
