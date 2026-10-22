import { ExternalLink, TriangleAlert } from "lucide-react";
import { useEffect, useId, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { ApiError, NETWORK_MESSAGE } from "../api/errors";
import { loginErrorText } from "../ui/brokerSession";
import { useFeatures } from "../ui/useFeatures";
import type { BrokerCatalogEntry, BrokerCatalogField } from "../types";
import { BTN_PRIMARY, BTN_SECONDARY, BrokerLogo, ResponsiveDialog } from "./parts";
import { entryFor, useBrokerCatalog } from "./useBrokerCatalog";

/**
 * P2b: the Connect panel - a small modal on a wide screen, a sheet on a phone. It holds ONLY:
 * - the fields the catalog lists for this broker (labels from the catalog, its short hint under each field);
 * - "Where do I get this?", which opens the catalog's 3 steps (and the broker's own page);
 * - one "Connect" button.
 * The answer is "Connected ✓" or the server's one plain sentence.
 *
 * Renew (the Broker page's row button, the Dashboard banner, the sign-in prompt) opens the same panel with only the
 * catalog's `renew_fields`, sent to POST /renew: Dhan pastes today's token; Zerodha and Fyers ("login_code") press
 * "Log in" (the broker's own login page, in a new tab) and paste the address they land on; Angel One and Shoonya
 * (no renew fields) are one button. Upstox ("analytics_token") renews by pasting an Analytics Token through
 * /connect - the server's own message asks for exactly that, and /renew has nothing to take for Upstox.
 * A Zerodha / Fyers connect stores the key and secret and answers "needs_login": the panel then moves to that
 * "Log in" step by itself.
 */

/**
 * IMAGES: the spot for "Where do I get this?" pictures, per broker id - e.g. `upstox: ["/img/how-to/upstox-1.png"]`.
 * Empty on purpose: screenshots of the brokers' websites could not be captured for this change. Add files under
 * public/ and list them here; the panel shows them under the steps.
 */
export const HOW_TO_IMAGES: Readonly<Record<string, readonly string[]>> = {};

export type PanelMode = "connect" | "renew";

/** What the panel asks for and where it sends it. */
export interface PanelStep {
  endpoint: "connect" | "renew";
  fields: readonly BrokerCatalogField[];
  /** A "Log in" button that opens the broker's own login page first (login_code). */
  login: boolean;
  /** The submit button's words. */
  action: "Connect" | "Renew";
}

/** The step for a broker: connecting asks for the catalog's fields; renewing for its `renew_fields` (see above).
 * `afterSave`: a login_code connect was just saved, so the next step is today's login, still called Connect. */
export function stepFor(entry: Pick<BrokerCatalogEntry, "method" | "fields" | "renew_fields">, mode: PanelMode, afterSave = false): PanelStep {
  if (mode === "connect" && !afterSave) return { endpoint: "connect", fields: entry.fields, login: false, action: "Connect" };
  if (entry.method === "analytics_token") return { endpoint: "connect", fields: entry.fields, login: false, action: "Renew" };
  return { endpoint: "renew", fields: entry.renew_fields ?? [], login: entry.method === "login_code", action: afterSave ? "Connect" : "Renew" };
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
}

const NO_FOLD: ReadonlySet<string> = new Set();

/** The panel's body - no dialog and no fetching, so tests render it directly. */
export function ConnectPanelView({ entry, step, values, onChange, onSubmit, busy, result, note = null, onLogin, loginBusy = false, loginOpened = null, onDone, fold = NO_FOLD }: ConnectPanelViewProps) {
  const ids = useId();
  const submit = (e: FormEvent) => { e.preventDefault(); if (!busy) onSubmit(); };
  const images = HOW_TO_IMAGES[entry.broker] ?? [];
  const busyWord = step.action === "Renew" ? "Renewing" : "Connecting";
  const { main, advanced } = splitFields(step.fields, fold);
  // The fields shown first are filled but one under Advanced is not: say where it is (Connect stays off until then).
  const advancedMissing = advanced.length > 0 && complete(main, values) && !complete(advanced, values);
  const field = (f: BrokerCatalogField) => {
    const i = step.fields.indexOf(f);
    return (
      <div key={f.key} className="space-y-space-1">
        <label htmlFor={`${ids}-${i}`} className="block text-t13 font-medium text-fg">{f.label}</label>
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

      {step.login && (
        <div className="space-y-space-2">
          <button type="button" onClick={onLogin} disabled={loginBusy} aria-busy={loginBusy || undefined} data-login
            className={`${BTN_SECONDARY} w-full`}>
            <ExternalLink size={16} aria-hidden /> Log in
          </button>
          {loginOpened && (
            <p className="text-t13 text-fg-muted">
              {loginOpened.opened
                ? `The ${entry.name} login page opened in a new tab. After you sign in, copy the address from the browser bar and paste it below.`
                : <>Your browser blocked the new tab. <a href={loginOpened.link} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">Open the {entry.name} login page</a>, sign in, then paste the address from the browser bar below.</>}
            </p>
          )}
        </div>
      )}

      {main.map(field)}

      {step.fields.length > 0 && entry.how_to.length > 0 && (
        <details className="rounded-control border border-subtle" data-how-to>
          <summary className="flex min-h-11 cursor-pointer list-none items-center px-space-3 text-t14 font-medium text-brand hover:underline [&::-webkit-details-marker]:hidden">
            Where do I get this?
          </summary>
          <div className="space-y-space-3 px-space-3 pb-space-3">
            <ol className="list-decimal space-y-space-1 pl-space-5 text-t13 text-fg-muted">
              {entry.how_to.map((s) => <li key={s}>{s}</li>)}
            </ol>
            {/* IMAGES: pictures for these steps go here (HOW_TO_IMAGES above). */}
            {images.length > 0 && (
              <div data-how-to-images className="grid grid-cols-1 gap-space-2">
                {images.map((src, i) => <img key={src} src={src} alt={`${entry.name}, step ${i + 1}`} className="w-full rounded-control border border-subtle" />)}
              </div>
            )}
            {entry.doc_url && (
              <a href={entry.doc_url} target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-space-1 text-t13 text-brand hover:underline">
                Open the {entry.name} page <ExternalLink size={14} aria-hidden />
              </a>
            )}
          </div>
        </details>
      )}

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
      ) : (
        <button type="submit" disabled={busy || !complete(step.fields, values)} aria-busy={busy || undefined}
          className={`${BTN_PRIMARY} w-full`}>
          {busy ? `${busyWord}…` : step.action}
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
  const fold = useFeatures().on("ui_v2") ? ADVANCED_FIELD_KEYS : NO_FOLD;
  const entry = broker ? entryFor(entries, broker) : null;
  const label = accountLabel || "primary";
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PanelResult | null>(null);
  const [afterSave, setAfterSave] = useState<string | null>(null);   // the server's "now press Log in" sentence
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginOpened, setLoginOpened] = useState<LoginOpened | null>(null);

  // A new broker, or the panel opened again: start empty (secrets are never kept once the panel closes).
  useEffect(() => {
    setValues({}); setResult(null); setBusy(false); setAfterSave(null); setLoginBusy(false); setLoginOpened(null);
  }, [broker, mode, open]);

  const step = entry ? stepFor(entry, mode, afterSave !== null) : null;

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
      else if (r.status === "needs_login" && step.endpoint === "connect" && entry.method === "login_code") {
        setAfterSave(r.message || `Saved. Now press Log in, sign in to ${entry.name} and paste the address you land on.`);
      } else setResult({ ok: false, text: r.message || "Not connected yet. Check the details and try again." });
    } catch (e) {
      setResult({ ok: false, text: failureText(e, entry.name) });
    } finally {
      setBusy(false);
    }
  }

  // "Log in" (login_code): the tab opens inside the click (a tab opened after an await is blocked as a popup), then
  // goes to the broker's own login page (the existing GET /broker/{name}/login-url).
  function login() {
    if (!entry) return;
    setLoginBusy(true);
    setResult(null);
    let tab: Window | null = null;
    try { tab = window.open("about:blank", "_blank"); if (tab) tab.opener = null; } catch { tab = null; }
    api.brokerLoginUrl(entry.broker, label)
      .then(({ authorization_url }) => {
        if (tab) tab.location.href = authorization_url;
        setLoginOpened({ link: authorization_url, opened: !!tab });
      })
      .catch((e: unknown) => { tab?.close(); setResult({ ok: false, text: loginErrorText(e, entry.broker) }); })
      .finally(() => setLoginBusy(false));
  }

  const name = entry?.name ?? "";
  const title = `${mode === "renew" ? "Renew" : "Connect"} ${name}`.trim();
  return (
    <ResponsiveDialog open={open} onOpenChange={onOpenChange} title={title} marker="connect">
      {entry && step ? (
        <ConnectPanelView entry={entry} step={step} values={values} busy={busy} result={result} note={afterSave}
          onChange={(key, value) => setValues((v) => ({ ...v, [key]: value }))}
          onSubmit={() => void submit()} onDone={() => onOpenChange(false)}
          onLogin={login} loginBusy={loginBusy} loginOpened={loginOpened} fold={fold} />
      ) : entries && broker ? (
        <p className="text-t14 text-fg-muted">This broker cannot be connected here yet.</p>
      ) : failed ? (
        <div className="space-y-space-3">
          <p className="text-t14 text-fg-muted">The list of brokers could not be loaded.</p>
          <button type="button" onClick={retry} className={BTN_SECONDARY}>Try again</button>
        </div>
      ) : (
        <div aria-busy="true" className="h-32 animate-pulse rounded-control bg-surface-2"><span className="sr-only">Loading</span></div>
      )}
    </ResponsiveDialog>
  );
}
