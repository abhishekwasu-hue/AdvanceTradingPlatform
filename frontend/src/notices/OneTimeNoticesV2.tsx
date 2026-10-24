import { Info, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { NOTICES_CHANGED } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { dismissNotice, readNotices, visibleNotices, type OneTimeNotice } from "./oneTimeNotices";

/** One notice above the page (never a modal): the message, a link to where to act, and a close button. */
export function NoticeCard({ notice, onDismiss }: { notice: OneTimeNotice; onDismiss: () => void }) {
  return (
    <section aria-label="Notice" data-notice={notice.id}
      className="flex items-start gap-space-3 rounded-panel border border-subtle border-l-4 border-l-brand bg-surface-1 px-space-4 py-space-3">
      <Info size={18} aria-hidden className="mt-0.5 shrink-0 text-brand" />
      <p className="min-w-0 flex-1 text-t14 text-fg">
        {notice.text}
        {" "}
        <Link to={notice.link} className="whitespace-nowrap font-semibold text-brand underline-offset-2 hover:underline">
          Open Risk limits
        </Link>
      </p>
      <button type="button" onClick={onDismiss} aria-label="Dismiss"
        className="-m-space-2 inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg md:h-9 md:w-9">
        <X size={18} aria-hidden />
      </button>
    </section>
  );
}

/** TT12-1: the signed-in user's one-time notices in the v2 shell. Read once after sign-in, and again on a page change
 * only while one is showing, and when a risk limit is saved (setting any limit marks it seen). */
export default function OneTimeNoticesV2() {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const [notices, setNotices] = useState<OneTimeNotice[]>([]);
  const showing = notices.length > 0;

  useEffect(() => {
    setNotices([]);
    if (!user) return;
    let live = true;
    void readNotices().then((n) => { if (live) setNotices(n); });
    return () => { live = false; };
  }, [user?.id]);

  useEffect(() => {
    if (!user || !showing) return;
    let live = true;
    void readNotices().then((n) => { if (live) setNotices(n); });
    return () => { live = false; };
  }, [pathname]);                                          // a page change re-reads only while one is showing

  useEffect(() => {
    if (!user || !showing) return;
    const reread = () => { void readNotices().then(setNotices); };
    window.addEventListener(NOTICES_CHANGED, reread);       // a risk limit was saved: the server marked it seen
    return () => window.removeEventListener(NOTICES_CHANGED, reread);
  }, [user?.id, showing]);

  if (!user || !showing) return null;
  return (
    <div className="px-space-4 pt-space-3 md:px-space-8">
      {notices.map((n) => (
        <NoticeCard key={n.id} notice={n} onDismiss={() => {
          void dismissNotice(n.id);
          setNotices((list) => visibleNotices(list));
        }} />
      ))}
    </div>
  );
}
