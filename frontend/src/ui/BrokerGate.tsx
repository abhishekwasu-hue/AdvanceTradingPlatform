import { ExternalLink, KeyRound, LogIn, Plug, TriangleAlert, UserCircle2 } from "lucide-react";
import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { gateShowing } from "../live/gateShowing";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import { BROKER_PATH } from "../broker/paths";
import { Button } from "../components/primitives";
import type { BrokerTokenInfo } from "../types";
import {
  BROWSER_LOGIN_DEPS, brokerDisplayName, loginErrorText, loginNote, startBrokerLogin, useBrokerSession,
  type BrokerGateState,
} from "./brokerSession";

/**
 * UI2-0: the broker gate - the foundation of "Connect your broker" / "Log in to <Broker>" on every data page
 * (owner brief ATP_SESSION_NO_SAMPLE_DATA; UI v2 proposal §5 empty states, §9 wording, §10 daily login).
 *
 * RULE - NO SAMPLE DATA: never show sample, demo, placeholder or made-up data in place of the trader's real data.
 * A data page renders its content only when a broker session is usable ("connected"). In every other state - signed
 * out, still checking, the check failed, no broker stored, login needed - the page's content is NOT rendered and this
 * gate says what is missing and offers the one action that fixes it. A page must never fall back to generated
 * candles, a seeded chain or example positions "until the broker connects".
 *
 * States:
 * - no broker stored  -> "Connect your broker", one primary action to the Connect flow (the Broker page, P2b);
 * - stored, no usable session today -> "Log in to <Broker>", which runs that broker's existing login path:
 *   Upstox OAuth start, the login link for Zerodha / Fyers (then paste the code it returns), authenticate for others;
 * - connected -> the children, unchanged.
 * Wording: plain English, neutral styling; amber only for a real warning (a login needed, a failed check or login).
 * The "we never see or store your broker password" sentence is never shown for TOTP brokers (Angel One, Shoonya).
 *
 * Shown only on v2 pages (flag `ui_v2`); UI2-1 wraps the pages in src/ui/dataPages.ts with it. Places no orders.
 */

/** The Connect flow: the Broker page (P2b). (A literal, not routes.ts, so this component does not pull every page
 * loader into its chunk; the unit test pins it to pathFor("broker").) */
export const CONNECT_BROKER_PATH = BROKER_PATH;
/** The sign-in page for a signed-out visitor (UI-14); pinned to pathFor("account") by the unit test. */
export const SIGN_IN_PATH = "/account";

/** After "Log in to Zerodha / Fyers": the broker's page is open and the trader pastes the code it returns. */
export interface CodeStep { broker: BrokerTokenInfo; link: string; opened: boolean }

export interface BrokerGateViewProps {
  state: BrokerGateState;
  /** The page's real content, rendered only when connected. */
  children?: ReactNode;
  onConnect?: () => void;
  onSignIn?: () => void;
  onLogin?: (broker: BrokerTokenInfo) => void;
  onRetry?: () => void;
  /** "<broker>/<label>" of the login in progress. */
  busy?: string | null;
  /** A failed login or check, already in plain words. */
  error?: string | null;
  codeStep?: CodeStep | null;
  onSubmitCode?: (code: string) => void;
  /** UI2-9a: the page lists the trader's own positions under the gate itself (My positions), so the gate's sentences
   * say only what the broker adds - live prices - and not "your positions". Left out: every other page's wording. */
  positionsShown?: boolean;
}

const keyOf = (t: BrokerTokenInfo) => `${t.broker_name}/${t.account_label ?? "primary"}`;
const primaryClass = "h-11 w-full px-space-5 text-t14 sm:w-auto";

function GateCard({ state, icon, tone = "neutral", title, children }: {
  state: BrokerGateState["kind"]; icon: ReactNode; tone?: "neutral" | "warn"; title: string; children: ReactNode;
}) {
  const titleId = useId();
  return (
    <section
      aria-labelledby={titleId}
      data-broker-gate={state}
      className="mx-auto w-full max-w-[480px] rounded-panel border border-subtle bg-surface-1 p-space-5 text-center shadow-card"
    >
      <div
        aria-hidden
        className={`mx-auto mb-space-3 flex h-11 w-11 items-center justify-center rounded-pill ${tone === "warn" ? "bg-warn-tint text-warn" : "bg-surface-2 text-fg-muted"}`}
      >
        {icon}
      </div>
      <h2 id={titleId} className="text-t18 font-semibold text-fg">{title}</h2>
      <div className="mt-space-2 space-y-space-3 text-t14 text-fg-muted">{children}</div>
    </section>
  );
}

function ErrorNote({ text }: { text: string }) {
  return (
    <p role="alert" className="flex items-start gap-space-2 rounded-control bg-warn-tint px-space-3 py-space-2 text-left text-t13 text-warn">
      <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0" />
      <span>{text}</span>
    </p>
  );
}

/** The paste-the-code step of "Log in to Zerodha / Fyers" (also used by the v2 dashboard's Needs attention row). */
export function CodeForm({ step, onSubmit, busy }: { step: CodeStep; onSubmit?: (code: string) => void; busy: boolean }) {
  const name = brokerDisplayName(step.broker.broker_name);
  const fieldId = useId();
  const [code, setCode] = useState("");
  const submit = (e: FormEvent) => { e.preventDefault(); if (code.trim()) onSubmit?.(code.trim()); };
  return (
    <form onSubmit={submit} className="space-y-space-3 text-left">
      <p>
        {step.opened
          ? `The ${name} login page opened in a new tab. After you log in, copy the full address from the browser's address bar and paste it here.`
          : `Your browser blocked the new tab. Open the ${name} login page, log in, then paste the full address from the address bar here.`}
      </p>
      <a href={step.link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-space-1 text-brand hover:underline">
        Open the {name} login page <ExternalLink size={14} aria-hidden />
      </a>
      <div className="space-y-space-1">
        <label htmlFor={fieldId} className="block text-t12 font-medium text-fg-muted">Address or code from {name}</label>
        <input
          id={fieldId}
          type="text"
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className="h-11 w-full rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
        />
      </div>
      <Button type="submit" variant="primary" loading={busy} disabled={!code.trim()} className={primaryClass}>
        Finish logging in
      </Button>
    </form>
  );
}

/** The gate's look for each state - no data fetching, so stories and tests render it directly. */
export function BrokerGateView({ state, children, onConnect, onSignIn, onLogin, onRetry, busy = null, error = null, codeStep = null, onSubmitCode, positionsShown = false }: BrokerGateViewProps) {
  switch (state.kind) {
    case "connected":
      return <>{children}</>;

    case "loading":
      return (
        <div data-broker-gate="loading" aria-busy="true" aria-live="polite" className="mx-auto w-full max-w-[480px] space-y-space-3">
          <span className="sr-only">Checking your broker connection</span>
          <div className="h-6 w-48 animate-pulse rounded-control bg-surface-2" />
          <div className="h-32 animate-pulse rounded-panel bg-surface-2" />
        </div>
      );

    case "signed-out":
      return (
        <GateCard state={state.kind} icon={<UserCircle2 size={22} />} title="Sign in to see your data">
          <p>Your prices, positions and signals appear here once you sign in.</p>
          <a href={SIGN_IN_PATH} onClick={(e) => { if (onSignIn) { e.preventDefault(); onSignIn(); } }}
            className="inline-flex h-11 w-full items-center justify-center rounded-control bg-brand px-space-5 text-t14 font-semibold text-on-brand hover:bg-brand-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface sm:w-auto">
            Sign in
          </a>
        </GateCard>
      );

    case "unavailable":
      return (
        <GateCard state={state.kind} tone="warn" icon={<TriangleAlert size={22} />} title="We could not check your broker connection">
          <p>Check your internet connection, then try again.</p>
          <Button variant="primary" onClick={onRetry} className={primaryClass}>Try again</Button>
        </GateCard>
      );

    case "no-broker":
      return (
        <GateCard state={state.kind} icon={<Plug size={22} />} title="Connect your broker">
          <p>{positionsShown ? "Connect your broker to see live prices and charts." : "Connect your broker to see live prices, charts and your positions."}</p>
          <a href={CONNECT_BROKER_PATH} onClick={(e) => { if (onConnect) { e.preventDefault(); onConnect(); } }}
            className="inline-flex h-11 w-full items-center justify-center rounded-control bg-brand px-space-5 text-t14 font-semibold text-on-brand hover:bg-brand-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface sm:w-auto">
            Connect a broker
          </a>
          <p className="text-t12">It takes about 2 minutes.</p>
        </GateCard>
      );

    case "login-needed": {
      const t = state.broker;
      const name = brokerDisplayName(t.broker_name);
      const account = (t.account_label ?? "primary") !== "primary" ? ` (account "${t.account_label}")` : "";
      // The paste-the-code step belongs to whichever broker it was started for - the primary one or one listed below.
      const stepFor = (b: BrokerTokenInfo) => (codeStep && keyOf(codeStep.broker) === keyOf(b) ? codeStep : null);
      const step = stepFor(t);
      return (
        <GateCard state={state.kind} tone="warn" icon={<KeyRound size={22} />} title={`Log in to ${name}`}>
          <p>
            {state.expired
              ? `Your ${name} session${account} has ended. Log in to see live prices${positionsShown ? "" : " and your positions"}.`
              : `Log in to ${name}${account} to see live prices${positionsShown ? "" : " and your positions"}.`}
          </p>
          {step ? (
            <div data-code-step={keyOf(t)}><CodeForm step={step} onSubmit={onSubmitCode} busy={busy === keyOf(t)} /></div>
          ) : (
            // One primary action: while another broker's paste step is open, "Finish logging in" is the primary one.
            <Button variant={codeStep ? "secondary" : "primary"} icon={<LogIn size={16} aria-hidden />} loading={busy === keyOf(t)} disabled={busy !== null}
              onClick={() => onLogin?.(t)} className={primaryClass}>
              Log in to {name}
            </Button>
          )}
          {error && <ErrorNote text={error} />}
          <p className="text-t12">{loginNote(t)}</p>
          {state.others.length > 0 && (
            <div className="border-t border-subtle pt-space-3 text-t12">
              <p>Also waiting for a login:</p>
              <div className="mt-space-1 flex flex-wrap justify-center gap-space-2">
                {state.others.map((o) => {
                  const label = `${brokerDisplayName(o.broker_name)}${(o.account_label ?? "primary") !== "primary" ? ` (${o.account_label})` : ""}`;
                  const own = stepFor(o);
                  return own ? (
                    <div key={keyOf(o)} data-code-step={keyOf(o)} className="basis-full pt-space-2 text-t14">
                      <p className="mb-space-2 font-semibold text-fg">Log in to {label}</p>
                      <CodeForm step={own} onSubmit={onSubmitCode} busy={busy === keyOf(o)} />
                    </div>
                  ) : (
                    <Button key={keyOf(o)} variant="ghost" size="sm" disabled={busy !== null} onClick={() => onLogin?.(o)} className="h-11">
                      Log in to {label}
                    </Button>
                  );
                })}
              </div>
            </div>
          )}
        </GateCard>
      );
    }
  }
}

/** "Log in to <Broker>" started from a click: the login in progress, its error and the paste-the-code step. The broker
 * gate and the v2 dashboard's Needs attention row share it; `refresh` re-reads the broker status after a login. */
export interface BrokerLogin {
  busy: string | null;
  error: string | null;
  codeStep: CodeStep | null;
  login: (t: BrokerTokenInfo) => void;
  submitCode: (code: string) => void;
}

export function useBrokerLogin(refresh: () => void): BrokerLogin {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [codeStep, setCodeStep] = useState<CodeStep | null>(null);

  function login(t: BrokerTokenInfo) {
    setError(null);
    setCodeStep(null);
    setBusy(keyOf(t));
    // Called inside the click: the login-link tab opens before the first await (see startBrokerLogin).
    startBrokerLogin(t, BROWSER_LOGIN_DEPS)
      .then((out) => {
        if (out.kind === "redirected") return;                  // the browser is leaving for the broker
        if (out.kind === "code-needed") setCodeStep({ broker: t, link: out.link, opened: out.opened });
        else refresh();
        setBusy(null);
      })
      .catch((e: unknown) => { setError(loginErrorText(e, t.broker_name)); setBusy(null); });
  }

  function submitCode(code: string) {
    if (!codeStep) return;
    const t = codeStep.broker;
    setError(null);
    setBusy(keyOf(t));
    api.brokerLoginCode(t.broker_name, code, t.account_label ?? "primary")
      .then(() => { setCodeStep(null); refresh(); })
      .catch((e: unknown) => setError(loginErrorText(e, t.broker_name)))
      .finally(() => setBusy(null));
  }

  return { busy, error, codeStep, login, submitCode };
}

/**
 * Day-one fix 3: the one tap beside "Daily backtests use ATP's stored end-of-day history": "Log in to <Broker>" when a
 * broker is stored but not logged in today (its existing login path), otherwise the Broker page.
 */
export function StoredLoginAction() {
  const { state, refresh } = useBrokerSession();
  const { busy, error, codeStep, login, submitCode } = useBrokerLogin(refresh);
  if (state.kind !== "login-needed") {
    return (
      <Link to={CONNECT_BROKER_PATH} className="inline-flex min-h-11 items-center font-semibold text-brand hover:underline md:min-h-0" data-stored-login="broker-page">
        {state.kind === "no-broker" ? "Connect a broker" : "Open Broker"}
      </Link>
    );
  }
  const t = state.broker;
  const name = brokerDisplayName(t.broker_name);
  return (
    <div className="flex flex-col gap-space-2" data-stored-login="login">
      {codeStep && keyOf(codeStep.broker) === keyOf(t) ? <CodeForm step={codeStep} onSubmit={submitCode} busy={busy === keyOf(t)} /> : (
        <Button variant="secondary" icon={<LogIn size={16} aria-hidden />} loading={busy === keyOf(t)} disabled={busy !== null} onClick={() => login(t)}
          className="h-11 px-space-4 text-t14 md:h-9">
          Log in to {name}
        </Button>
      )}
      {error && <ErrorNote text={error} />}
    </div>
  );
}

/**
 * Day-one fix 2 (README row 19): the one line above a page that runs on ATP's stored Daily history before the day's
 * broker login - what the page shows now (`what`, one plain sentence) and the one tap that renews the login (or connects
 * a broker). Neutral styling: nothing is wrong, live prices are simply not there yet.
 */
export function StoredDailyNote({ state, what, onConnect, onLogin, busy = null, error = null, codeStep = null, onSubmitCode }: {
  state: Extract<BrokerGateState, { kind: "login-needed" | "no-broker" }>; what: string; onConnect?: () => void;
  onLogin?: (broker: BrokerTokenInfo) => void; busy?: string | null; error?: string | null; codeStep?: CodeStep | null;
  onSubmitCode?: (code: string) => void;
}) {
  const name = state.kind === "login-needed" ? brokerDisplayName(state.broker.broker_name) : null;
  const lead = state.kind === "login-needed"
    ? `${state.expired ? `Your ${name} session has ended for today.` : `You are not logged in to ${name} today.`} Log in for live prices.`
    : "Connect your broker for live prices.";
  return (
    <section data-stored-daily={state.kind} aria-label="Live prices"
      className="mb-space-4 flex flex-col gap-space-3 rounded-panel border border-subtle bg-surface-1 px-space-4 py-space-3 text-t14 text-fg sm:flex-row sm:items-center">
      <p className="min-w-0 flex-1"><span className="font-medium">{lead}</span> <span className="text-fg-muted">{what}</span></p>
      {state.kind === "login-needed" ? (
        codeStep && keyOf(codeStep.broker) === keyOf(state.broker) ? (
          <div className="w-full sm:max-w-sm" data-code-step={keyOf(state.broker)}><CodeForm step={codeStep} onSubmit={onSubmitCode} busy={busy === keyOf(state.broker)} /></div>
        ) : (
          <Button variant="primary" icon={<LogIn size={16} aria-hidden />} loading={busy === keyOf(state.broker)} disabled={busy !== null}
            onClick={() => onLogin?.(state.broker)} className="h-11 w-full shrink-0 px-space-5 text-t14 sm:w-auto">
            Log in to {name}
          </Button>
        )
      ) : (
        <a href={CONNECT_BROKER_PATH} onClick={(e) => { if (onConnect) { e.preventDefault(); onConnect(); } }}
          className="inline-flex h-11 w-full shrink-0 items-center justify-center rounded-control bg-brand px-space-5 text-t14 font-semibold text-on-brand hover:bg-brand-strong sm:w-auto">
          Connect a broker
        </a>
      )}
      {error && <div className="basis-full"><ErrorNote text={error} /></div>}
    </section>
  );
}

/**
 * Renders `children` only while the trader's broker session is usable; otherwise the gate (see the RULE above).
 * Needs the router (Connect goes to Settings) and AuthProvider, as every page inside the app shell has.
 */
export function BrokerGate({ children, storedDaily = null }: { children: ReactNode; storedDaily?: string | null }) {
  const { state, refresh } = useBrokerSession();
  const navigate = useNavigate();
  const { busy, error, codeStep, login, submitCode } = useBrokerLogin(refresh);
  // TM Part 1: while this card asks to log in, the app's live-prices notice does not ask a second time.
  useEffect(() => {
    gateShowing.set(state.kind === "login-needed");
    return () => gateShowing.set(false);
  }, [state.kind]);

  // Day-one fix 2: a page that works on stored Daily history shows its content under a one-tap renew line instead of
  // the full gate while only the broker login (or the broker) is missing.
  if (storedDaily && (state.kind === "login-needed" || state.kind === "no-broker")) {
    return (
      <>
        <StoredDailyNote state={state} what={storedDaily} onConnect={() => navigate(CONNECT_BROKER_PATH)} onLogin={login}
          busy={busy} error={error} codeStep={codeStep} onSubmitCode={submitCode} />
        {children}
      </>
    );
  }

  return (
    <BrokerGateView
      state={state}
      onConnect={() => navigate(CONNECT_BROKER_PATH)}
      onSignIn={() => navigate(SIGN_IN_PATH)}
      onLogin={login}
      onRetry={refresh}
      busy={busy}
      error={error}
      codeStep={codeStep}
      onSubmitCode={submitCode}
    >
      {children}
    </BrokerGateView>
  );
}
