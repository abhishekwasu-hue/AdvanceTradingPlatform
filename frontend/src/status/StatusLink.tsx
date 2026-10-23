import { useEffect, useState } from "react";
import { loadPublicStatus, STATE_LABEL, type ComponentState, type StatusLoad } from "./publicStatus";

/**
 * Production readiness A2 (OPEN_QUESTIONS RDY-11): the "Status" link in the app's footers - the desktop sidebar, the
 * phone's More sheet and the sign-in page.
 *
 * Where it goes: `VITE_STATUS_PAGE_URL` when the build names one (an uptime service's hosted status page, off the
 * server, so it still answers when the server does not); otherwise the in-app `/status` page, shown only while the
 * operator has turned it on (flag `public_status_page`: the page's own endpoint answers 404 while it is off, and then
 * no link is shown at all). The in-app link carries the current overall state as a dot and in words for screen readers.
 */

export function externalStatusUrl(raw: string | undefined = import.meta.env.VITE_STATUS_PAGE_URL): string | null {
  const value = (raw ?? "").trim();
  return /^https:\/\/[^\s/]+\.[^\s]+$/.test(value) ? value : null;
}

let probe: Promise<StatusLoad> | null = null;

/** One read of `/api/v1/status` per page load, shared by every footer on the screen. */
export function statusProbe(fetcher?: typeof fetch): Promise<StatusLoad> {
  probe ??= loadPublicStatus(fetcher);
  return probe;
}

/** Tests: forget the shared read. */
export function resetStatusProbe(): void {
  probe = null;
}

export interface StatusTarget { href: string; external: boolean; state: ComponentState | null }

/** Pure: where the link goes for this read (null = no link). An unreachable server still gets the link - that is when
 * people look for it. */
export function statusTarget(load: StatusLoad | null, external: string | null = externalStatusUrl()): StatusTarget | null {
  if (external) return { href: external, external: true, state: null };
  if (!load || load.kind === "loading" || load.kind === "disabled") return null;
  return { href: "/status", external: false, state: load.kind === "ok" ? load.status.status : "outage" };
}

export function useStatusTarget(): StatusTarget | null {
  const external = externalStatusUrl();
  const [load, setLoad] = useState<StatusLoad | null>(null);
  useEffect(() => {
    if (external) return;
    let live = true;
    void statusProbe().then((next) => { if (live) setLoad(next); });
    return () => { live = false; };
  }, [external]);
  return statusTarget(load, external);
}

const DOT: Record<ComponentState, string> = { operational: "bg-up", degraded: "bg-warn", outage: "bg-down", unknown: "bg-fg-muted" };

export function StatusLinkView({ target, className = "" }: { target: StatusTarget | null; className?: string }) {
  if (!target) return null;
  return (
    <a href={target.href} data-status-link={target.external ? "external" : "in-app"}
      {...(target.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      className={`inline-flex min-h-11 items-center gap-space-2 rounded-control text-t12 font-medium text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8 ${className}`}>
      {target.state && <span aria-hidden className={`h-2 w-2 shrink-0 rounded-pill ${DOT[target.state]}`} />}
      <span>Status</span>
      {target.state && <span className="sr-only">: {STATE_LABEL[target.state]}</span>}
      {target.external && <span className="sr-only"> (opens in a new tab)</span>}
    </a>
  );
}

export default function StatusLink({ className }: { className?: string }) {
  return <StatusLinkView target={useStatusTarget()} className={className} />;
}
