import { Sparkles } from "lucide-react";
import { useId } from "react";
import { Link } from "react-router";
import { useOptionalAuth } from "../auth/AuthContext";
import { Details } from "./Details";

/**
 * UX1 (handover 8b): what a trader sees at the address of a page, or in the place of a feature, that the operator has
 * not switched on yet - a friendly "Coming soon", never the flag name, an internal code or a reference id. A platform
 * administrator (SUPER_ADMIN) also sees a small "Admin: <flag>" note, so the switch is easy to find.
 */
export function isAdminViewer(role: string | null | undefined): boolean {
  return role === "SUPER_ADMIN";
}

function AdminFlagNote({ flag }: { flag?: string }) {
  const role = useOptionalAuth()?.user?.role;
  if (!flag || !isAdminViewer(role)) return null;
  return <p data-admin-note className="mt-space-3 text-t12 text-fg-muted">Admin: {flag}</p>;
}

/** The page-sized card (a whole page is closed). */
export function ComingSoon({ flag, home = true }: { flag?: string; home?: boolean }) {
  const titleId = useId();
  return (
    <section aria-labelledby={titleId} data-state="coming-soon"
      className="mx-auto w-full max-w-[480px] rounded-panel border border-subtle bg-surface-1 p-space-5 text-center shadow-card">
      <div aria-hidden className="mx-auto mb-space-3 flex h-11 w-11 items-center justify-center rounded-pill bg-surface-2 text-brand">
        <Sparkles size={20} />
      </div>
      <h2 id={titleId} className="text-t18 font-semibold text-fg">Coming soon</h2>
      <Details id="coming-soon.about" className="mt-space-2"><p>This part of AiAlgoDesk is not open for your account yet. Everything else works as usual.</p></Details>
      {home && (
        <Link to="/"
          className="mt-space-4 inline-flex min-h-11 items-center justify-center rounded-control bg-brand px-space-5 text-t14 font-semibold text-on-brand hover:bg-brand-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface">
          Go to the dashboard
        </Link>
      )}
      <AdminFlagNote flag={flag} />
    </section>
  );
}

/** One line in place of a feature inside a page (a card or an option that is not switched on yet). */
export function ComingSoonNote({ what, flag }: { what: string; flag?: string }) {
  return (
    <div data-state="coming-soon" className="text-t14 text-fg-muted">
      <p>{what} is coming soon.</p>
      <AdminFlagNote flag={flag} />
    </div>
  );
}
