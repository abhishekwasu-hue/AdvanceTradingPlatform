import { CircleSlash, RotateCw } from "lucide-react";
import type { ReactNode } from "react";
import { useOptionalAuth } from "../auth/AuthContext";

/**
 * UI-H Wave 0 (H8): what a page shows when a part of it cannot work right now. Two audiences, two texts:
 * - every user sees one plain sentence with the REAL reason in his words ("Chart reading is being set up on our
 *   server"), never "Coming soon" for a failure and never a code, a flag or an endpoint;
 * - an administrator (SUPER_ADMIN, the role from the server) also sees `adminDetail`: the technical cause and the fix.
 * "Try again" shows when the page passes `onRetry`.
 */
export interface UnavailableStateProps {
  /** The heading: what is not available ("Chart reading"). */
  title: string;
  /** The user's sentence: why, and what he can do. */
  message: ReactNode;
  /** Admins only: the technical cause and the fix. */
  adminDetail?: ReactNode;
  onRetry?: () => void;
  /** Overrides the role read from the signed-in user (stories and tests). */
  isAdmin?: boolean;
  className?: string;
}

export function UnavailableState({ title, message, adminDetail, onRetry, isAdmin, className = "" }: UnavailableStateProps) {
  const auth = useOptionalAuth();
  const admin = isAdmin ?? auth?.user?.role === "SUPER_ADMIN";
  return (
    <section role="status" aria-label={`${title}: not available`} data-state="unavailable"
      className={`rounded-panel border border-subtle bg-surface-1 p-space-4 ${className}`}>
      <div className="flex items-start gap-space-3">
        <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded-pill bg-surface-2 text-fg-muted">
          <CircleSlash size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-t15 font-semibold text-fg">{title}</h2>
          <p className="mt-space-1 text-t14 text-fg-muted">{message}</p>
          {admin && adminDetail && (
            <p className="mt-space-2 rounded-control bg-surface-2 px-space-3 py-space-2 text-t14 text-fg" data-admin-detail>
              <span className="font-semibold">For admins: </span>{adminDetail}
            </p>
          )}
          {onRetry && (
            <button type="button" onClick={onRetry}
              className="mt-space-3 inline-flex min-h-11 items-center gap-space-2 rounded-control border border-border bg-surface-2 px-space-4 text-t14 font-semibold text-fg hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
              <RotateCw size={16} aria-hidden /> Try again
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
