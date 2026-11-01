import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { isNativeApp } from "../native/platform";
import { brokerInitials, isBrokerProvider, providersFrom, signInV2From, socialStartUrl, type SignInProvider, type SocialIntent } from "./social";

/**
 * M73 SL-1: the sign-in options at the top of Sign in, Create account and Join team - one brand-correct button per
 * provider the server lists, then "or use email". The area is a LIST of entries (`SignInOption`): a later entry such
 * as M74's "Continue with your broker" is one more item passed in `extra`, not a new layout.
 *
 * Brand rules followed: Google - the official four-colour "G" on a white (light) / #131314 (dark) button with the
 * #747775 / #8E918F outline and the label "Continue with Google"; Meta - the white "f" mark on Facebook blue #1877F2
 * with white text, label "Continue with Facebook". The colours are the --social-* tokens (src/styles/tokens.css).
 */
export interface SignInOption { key: string; node: ReactNode }

const BUTTON = "flex h-12 w-full items-center justify-center gap-space-3 rounded-control border px-space-4 text-t14 font-medium "
  + "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 "
  + "focus-visible:ring-offset-surface-1";

export function GoogleMark({ size = 20 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 48 48" focusable="false">
      <path fill="rgb(var(--social-google-red))" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="rgb(var(--social-google-blue))" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="rgb(var(--social-google-yellow))" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="rgb(var(--social-google-green))" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

export function FacebookMark({ size = 20 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" focusable="false">
      <path fill="rgb(var(--social-facebook-fg))" d="M24 12.073C24 5.405 18.627 0 12 0S0 5.405 0 12.073C0 18.1 4.388 23.094 10.125 24v-8.437H7.078v-3.49h3.047V9.413c0-3.026 1.792-4.697 4.533-4.697 1.312 0 2.686.236 2.686.236v2.971H15.83c-1.491 0-1.956.93-1.956 1.886v2.264h3.328l-.532 3.49h-2.796V24C19.612 23.094 24 18.1 24 12.073z" />
    </svg>
  );
}

const STYLES: Record<string, string> = {
  google: "border-social-google-border bg-social-google-bg text-social-google-fg hover:bg-social-google-hover",
  facebook: "border-social-facebook bg-social-facebook text-social-facebook-fg hover:bg-social-facebook-hover",
};

function mark(id: string): ReactNode {
  if (id === "google") return <GoogleMark />;
  if (id === "facebook") return <FacebookMark />;
  return null;
}

/** One provider's button (a link: the provider's page opens in this tab). */
export function ProviderButton({ provider, href }: { provider: SignInProvider; href: string }) {
  return (
    <a href={href} data-provider={provider.id}
      className={`${BUTTON} ${STYLES[provider.id] ?? "border-border bg-surface text-fg hover:bg-surface-2"}`}>
      {mark(provider.id)}
      <span>Continue with {provider.label}</span>
    </a>
  );
}

/** The list: every live provider, then any extra entry (M74). Renders nothing when the list is empty. */
export function SignInOptionsList({ providers, intent = "signin", invite, extra = [] }: {
  providers: SignInProvider[]; intent?: SocialIntent; invite?: string | null; extra?: SignInOption[];
}) {
  const options: SignInOption[] = [
    // M74: brokers are not buttons of their own - they sit in the one "Continue with your broker" entry (`extra`).
    ...providers.filter((p) => !isBrokerProvider(p)).map((p) => ({ key: p.id, node: <ProviderButton provider={p} href={socialStartUrl(p, intent, invite)} /> })),
    ...extra,
  ];
  if (options.length === 0) return null;
  return (
    <ul className="space-y-space-3" data-sign-in-options>
      {options.map((o) => <li key={o.key}>{o.node}</li>)}
    </ul>
  );
}

/**
 * M74 BL-1: "Continue with your broker" - one button that opens the list of brokers the server reports as set up
 * (initials badge + name); each opens that broker's own login page. Nothing when no broker is set up (no "coming
 * soon"): the other brokers stay connectable after sign-in with the user's own keys.
 */
export function BrokerSignIn({ brokers, intent = "signin", invite }: {
  brokers: SignInProvider[]; intent?: SocialIntent; invite?: string | null;
}) {
  const [open, setOpen] = useState(false);
  if (brokers.length === 0) return null;
  return (
    <div data-broker-sign-in>
      <button type="button" aria-expanded={open} aria-controls="broker-sign-in-list" onClick={() => setOpen((v) => !v)}
        className={`${BUTTON} border-border bg-surface text-fg hover:bg-surface-2`}>
        <span>Continue with your broker</span>
        <span aria-hidden="true" className={`transition-transform ${open ? "rotate-180" : ""}`}>▾</span>
      </button>
      {open && (
        <ul id="broker-sign-in-list" className="mt-space-2 overflow-hidden rounded-control border border-border" aria-label="Brokers">
          {brokers.map((b) => (
            <li key={b.id} className="border-b border-subtle last:border-b-0">
              <a href={socialStartUrl(b, intent, invite)} data-provider={b.id}
                className="flex min-h-12 items-center gap-space-3 px-space-4 text-t14 text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                <span aria-hidden="true"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-2 text-t12 font-semibold text-fg-muted">
                  {brokerInitials(b)}
                </span>
                <span>{b.label}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The `extra` entry for SignInOptionsList: the broker list when the server reports any broker, else none. */
export function brokerOptions(providers: SignInProvider[], intent: SocialIntent = "signin", invite?: string | null): SignInOption[] {
  const brokers = providers.filter(isBrokerProvider);
  return brokers.length ? [{ key: "brokers", node: <BrokerSignIn brokers={brokers} intent={intent} invite={invite} /> }] : [];
}

/** "or use email" between the provider buttons and the form. */
export function OrUseEmail() {
  return (
    <div className="my-space-5 flex items-center gap-space-3 text-t12 text-fg-muted" role="separator" aria-label="or use email">
      <span className="h-px flex-1 bg-border-subtle" />
      <span aria-hidden="true">or use email</span>
      <span className="h-px flex-1 bg-border-subtle" />
    </div>
  );
}

/** The live providers and the signed-out layout switch, asked once (none and "off" until the server answers). */
export function useSignInProviders(): { providers: SignInProvider[]; v2: boolean } {
  const [state, setState] = useState<{ providers: SignInProvider[]; v2: boolean }>({ providers: [], v2: false });
  useEffect(() => {
    let live = true;
    api.authProviders()
      .then((answer) => { if (live) setState({ providers: providersFrom(answer, isNativeApp()), v2: signInV2From(answer) }); })
      .catch(() => { if (live) setState({ providers: [], v2: false }); });
    return () => { live = false; };
  }, []);
  return state;
}
