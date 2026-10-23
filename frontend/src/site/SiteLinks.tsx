import { useEffect, useState } from "react";
import { Link } from "react-router";
import { SITE_LABELS, SITE_SLUGS, sitePages, sitePath } from "./site";

/** Readiness C13: the sign-in screen's links to the public pages - nothing while the operator has them off. */
export default function SiteLinks() {
  const [on, setOn] = useState(false);
  useEffect(() => {
    let live = true;
    sitePages().then((p) => { if (live) setOn(!!p && p.length > 0); });
    return () => { live = false; };
  }, []);
  if (!on) return null;
  return (
    <nav aria-label="About AiAlgoDesk" className="flex flex-wrap justify-center gap-x-space-4 gap-y-space-1 text-t12" data-site-links>
      {SITE_SLUGS.map((s) => (
        <Link key={s} to={sitePath(s)}
          className="inline-flex min-h-11 items-center rounded-control text-fg-muted hover:text-fg md:min-h-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          {SITE_LABELS[s]}
        </Link>
      ))}
    </nav>
  );
}
