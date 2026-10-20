import { Download, Info, RefreshCw, Share, X } from "lucide-react";
import { Button } from "../components/primitives";
import { INSTALL_NEEDS_DOMAIN, applyUpdate, dismissInstall, promptInstall, usePwa } from "./pwa";

/** PW1: the two app-level notices - a waiting new version ("Reload") and the install offer. Both sit above the page
 * content, never cover it, and are announced politely to screen readers. Touch targets are 44 px on phones.
 * Owner mobile brief (Phase A): on an address that can never install the app (no trusted HTTPS - a bare IP with
 * Caddy's internal certificate), the install offer says why once, instead of never appearing. */
export default function PwaBanners() {
  const { updateReady, install } = usePwa();
  if (!updateReady && !install) return null;
  return (
    <div className="space-y-2 px-3 pt-3 md:px-6" role="status" aria-live="polite">
      {updateReady && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border border-l-4 border-l-brand bg-surface-1 px-3 py-2 text-sm text-fg">
          <RefreshCw size={16} className="shrink-0 text-brand" aria-hidden />
          <span className="min-w-0 flex-1">A new version is ready.</span>
          <Button variant="primary" className="h-11 md:h-9" onClick={applyUpdate}>Reload</Button>
        </div>
      )}
      {install === "prompt" && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm text-fg">
          <Download size={16} className="shrink-0 text-brand" aria-hidden />
          <span className="min-w-0 flex-1">Install Advance Trading Platform on this device for one-tap access and alerts.</span>
          <Button variant="primary" className="h-11 md:h-9" onClick={() => { void promptInstall(); }}>Install app</Button>
          <Button variant="ghost" className="h-11 w-11 px-0 md:h-9 md:w-9" aria-label="Not now" onClick={dismissInstall} icon={<X size={16} />} />
        </div>
      )}
      {install === "unavailable" && (
        <div className="flex items-start gap-3 rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm text-fg" data-install="unavailable">
          <Info size={16} className="mt-0.5 shrink-0 text-fg-muted" aria-hidden />
          <span className="min-w-0 flex-1">{INSTALL_NEEDS_DOMAIN}</span>
          <Button variant="ghost" className="h-11 w-11 px-0 md:h-9 md:w-9" aria-label="Dismiss" onClick={dismissInstall} icon={<X size={16} />} />
        </div>
      )}
      {install === "ios" && (
        <div className="flex items-start gap-3 rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm text-fg">
          <Share size={16} className="mt-0.5 shrink-0 text-brand" aria-hidden />
          <span className="min-w-0 flex-1">
            Install on iPhone or iPad: tap <strong>Share</strong> in Safari, then <strong>Add to Home Screen</strong>.
            Alerts on iOS arrive only in the installed app.
          </span>
          <Button variant="ghost" className="h-11 w-11 px-0 md:h-9 md:w-9" aria-label="Dismiss" onClick={dismissInstall} icon={<X size={16} />} />
        </div>
      )}
    </div>
  );
}
