import { useNavigate } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { LINK_SECONDARY } from "../dashboard/CardV2";
import { LOCAL_INTRO_STORE, clearIntroDismissals } from "../ui/PageIntro";
import { showAllTipsAgain, skipAllTips, useTours } from "./state";

/**
 * MO2 part C: Settings → Help - "Show all tips again" (every mark cleared on the server and every "What is this page?"
 * card back; the welcome then opens on the Dashboard) and "Skip all tips" (no tip opens by itself; the "?" next to a page
 * title still shows that page's). Enabled once the server answered, so a change never rests on the cached copy alone.
 */
export default function TipsButtonsV2({ onDone, className = "" }: { onDone?: () => void; className?: string }) {
  const { user } = useAuth();
  const tips = useTours(user?.id ?? null);
  const navigate = useNavigate();
  if (!user || !tips.store) return null;
  const store = tips.store;
  const off = !!tips.marks.skip_all_at;
  const again = () => {
    void store.update(showAllTipsAgain);
    clearIntroDismissals(LOCAL_INTRO_STORE, user.id);
    onDone?.();
    navigate("/");
  };
  const skip = () => { void store.update(skipAllTips(new Date().toISOString())); onDone?.(); };
  return (
    <div className={`flex flex-col gap-space-2 ${className}`} data-tips-settings={off ? "off" : "on"}>
      <div className="flex flex-wrap gap-space-2">
        <button type="button" onClick={again} disabled={!tips.fresh} data-action="show-all-tips" className={`${LINK_SECONDARY} disabled:opacity-50`}>
          Show all tips again
        </button>
        {!off && (
          <button type="button" onClick={skip} disabled={!tips.fresh} data-action="skip-all-tips" className={`${LINK_SECONDARY} disabled:opacity-50`}>
            Skip all tips
          </button>
        )}
      </div>
      {off && <p className="text-t13 text-fg-muted" role="status">Tips are off. Press ? next to a page title to see that page&apos;s tips.</p>}
      {tips.error && <p className="text-t13 text-warn" role="alert">{tips.error}</p>}
    </div>
  );
}
