import { ChevronRight } from "lucide-react";
import { Suspense, lazy, useEffect, useRef, useState, type ComponentType, type ReactNode } from "react";
import { api } from "../api/client";
import BrokerAccountsCard from "../components/BrokerAccountsCard";
import StaticIpCard from "../components/StaticIpCard";
import { BTN_SECONDARY } from "./parts";

/**
 * P2b: "Advanced", collapsed at the bottom of the Broker page - the technical settings moved off the main screen:
 * - the exact Upstox redirect URL with a copy button (the OAuth app setting on Upstox's developer site);
 * - the static IP registered with each broker (SEBI);
 * - the broker accounts (default account, enable / disable, balances, the read-only check).
 * Nothing here loads until "Advanced" is opened.
 */

type RedirectProps = { selected: boolean; refreshKey?: number };

/**
 * PR #319 adds src/components/UpstoxRedirectUrl.tsx (GET /api/broker/upstox/redirect-url, the deployment's public
 * address). The import is optional on purpose: `import.meta.glob` finds the file once #319 is merged and finds nothing
 * before - then RedirectUrlFallback shows the address the server reports in /broker/token-status. No code change is
 * needed when #319 lands.
 */
const OPTIONAL = import.meta.glob<{ default: ComponentType<RedirectProps> }>("../components/UpstoxRedirectUrl.tsx");
const loadRedirect = Object.values(OPTIONAL)[0];
export const HAS_UPSTOX_REDIRECT_COMPONENT = loadRedirect !== undefined;
const UpstoxRedirectUrl = loadRedirect ? lazy(loadRedirect) : null;

/** Copies `text`; when the clipboard is refused, selects the field so it can be copied by hand. Never throws. */
export async function copyText(text: string, clipboard: Pick<Clipboard, "writeText"> | undefined, select: () => void): Promise<boolean> {
  try {
    if (!clipboard) throw new Error("no clipboard");
    await clipboard.writeText(text);
    return true;
  } catch {
    try { select(); } catch { /* the address is still on screen */ }
    return false;
  }
}

/** Until #319: the Upstox redirect URL the server reports with the stored Upstox credential (token-status). */
export function RedirectUrlFallback() {
  const [url, setUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let live = true;
    api.brokerTokenStatus()
      .then((rows) => { if (live) setUrl(rows.find((r) => r.broker_name === "upstox" && r.oauth_callback_url)?.oauth_callback_url ?? null); })
      .catch(() => undefined);
    return () => { live = false; };
  }, []);
  if (!url) return <p className="text-t13 text-fg-muted">The Upstox redirect URL shows here once Upstox is connected.</p>;
  const copy = async () => setCopied(await copyText(url, navigator.clipboard, () => { field.current?.focus(); field.current?.select(); }));
  return (
    <div className="space-y-space-2">
      <label htmlFor="upstox-redirect-url" className="block text-t13 font-medium text-fg">Redirect URL</label>
      <div className="flex gap-space-2">
        <input id="upstox-redirect-url" ref={field} readOnly value={url} onFocus={(e) => e.target.select()}
          className="h-11 min-w-0 flex-1 rounded-control border border-border bg-surface-2 px-space-3 font-mono text-t13 text-fg" />
        <button type="button" onClick={() => void copy()} className={BTN_SECONDARY}>{copied ? "Copied" : "Copy"}</button>
      </div>
      <p className="text-t13 text-fg-muted">Paste this address into the Redirect URL field of your app on the Upstox developer site.</p>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-space-2">
      <h3 className="text-t13 font-semibold text-fg">{title}</h3>
      {children}
    </section>
  );
}

export default function BrokerAdvanced() {
  const [open, setOpen] = useState(false);
  return (
    <details data-broker-advanced className="rounded-panel border border-subtle bg-surface-1"
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-space-2 px-space-4 text-t14 font-semibold text-fg-muted hover:text-fg [&::-webkit-details-marker]:hidden">
        <ChevronRight size={16} aria-hidden className={`transition-transform ${open ? "rotate-90" : ""}`} />
        Advanced
      </summary>
      {open && (
        <div className="space-y-space-5 border-t border-subtle p-space-4">
          <Section title="Upstox OAuth app">
            {UpstoxRedirectUrl ? (
              <Suspense fallback={null}><UpstoxRedirectUrl selected /></Suspense>
            ) : <RedirectUrlFallback />}
          </Section>
          <StaticIpCard />
          <BrokerAccountsCard />
        </div>
      )}
    </details>
  );
}
