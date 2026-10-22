import { useEffect, useState } from "react";
import { api } from "../api/client";
import { isNativeApp } from "../native/platform";
import { googleEnabled, googleStartUrl } from "./google";

/** GS-1: the "Continue with Google" link, or nothing at all when `show` is false. */
export function GoogleButton({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <div className="mt-3 space-y-3">
      <div className="flex items-center gap-2 text-xs text-fg-muted" aria-hidden="true">
        <span className="h-px flex-1 bg-border" />
        or
        <span className="h-px flex-1 bg-border" />
      </div>
      <a
        href={googleStartUrl()}
        className="flex w-full items-center justify-center gap-2 rounded border border-border bg-surface hover:bg-surface-2 text-fg font-medium px-4 py-1.5 text-sm transition-colors"
      >
        <GoogleMark />
        Continue with Google
      </a>
    </div>
  );
}

function GoogleMark() {
  // A neutral "G" in the text colour (no brand artwork, no hard-coded colours).
  return (
    <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center rounded-full border border-current text-xs font-semibold leading-none">
      G
    </span>
  );
}

/** Asks the server once whether Google sign-in is set up; renders nothing until it says yes. */
export default function GoogleSignInButton() {
  const [show, setShow] = useState(false);
  useEffect(() => {
    let live = true;
    api.authProviders()
      .then((p) => { if (live) setShow(googleEnabled(p, isNativeApp())); })
      .catch(() => { if (live) setShow(false); });
    return () => { live = false; };
  }, []);
  return <GoogleButton show={show} />;
}
