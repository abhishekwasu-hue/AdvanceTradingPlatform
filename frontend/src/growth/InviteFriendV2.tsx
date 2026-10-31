import { Check, Copy, Gift, Share2 } from "lucide-react";
import { useEffect, useState } from "react";
import { CardV2, LINK_PRIMARY, LINK_SECONDARY, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { joinedLine, referralApi, type ReferralCard } from "./referral";
import { Details } from "../ui/Details";

/**
 * Readiness C13 (`ui_v2` + `referrals`): "Invite a friend" on Settings → Account & plan - his own invite link (made
 * when he asks for it), how many people joined through it (a count, never who) and the reward in the operator's words
 * when one is set. Nothing is promised that the operator did not write; the parent mounts this only while both flags are on.
 */
type State = { status: "loading" } | { status: "error" } | { status: "ok"; data: ReferralCard };

export function InviteFriendBodyV2({ data, onCreate, busy = false }: { data: ReferralCard; onCreate: () => void; busy?: boolean }) {
  const [copied, setCopied] = useState(false);
  const canShare = typeof navigator !== "undefined" && typeof navigator.share === "function";
  const copy = async () => {
    if (!data.link) return;
    try { await navigator.clipboard.writeText(data.link); setCopied(true); window.setTimeout(() => setCopied(false), 2000); } catch { /* intentional: the link stays visible to copy by hand */ }
  };
  return (
    <div className="flex flex-col gap-space-3">
      <Details id="growth.invite" className="mt-0"><p className="text-t14 text-fg">Know someone who wants to learn trading the careful way? Send them your link.</p></Details>
      {data.reward_text && (
        <p className="flex items-start gap-space-2 rounded-control bg-surface-2 p-space-3 text-t14 text-fg" data-reward>
          <Gift size={16} aria-hidden className="mt-0.5 shrink-0 text-brand" /><span className="min-w-0">{data.reward_text}</span>
        </p>
      )}
      {data.link ? (
        <>
          <div className="flex flex-col gap-space-2 sm:flex-row">
            <input readOnly value={data.link} aria-label="Your invite link" onFocus={(e) => e.currentTarget.select()}
              className="h-11 min-w-0 flex-1 rounded-control border border-border bg-surface-2 px-space-3 font-mono text-t14 text-fg md:h-9" />
            <button type="button" onClick={copy} className={LINK_SECONDARY}>
              {copied ? <Check size={16} aria-hidden /> : <Copy size={16} aria-hidden />}{copied ? "Copied" : "Copy link"}
            </button>
            {canShare && (
              <button type="button" className={LINK_SECONDARY}
                onClick={() => { navigator.share({ title: "AiAlgoDesk", text: "Learn to read the market and practise on paper.", url: data.link! }).catch(() => { /* intentional: he closed the share sheet */ }); }}>
                <Share2 size={16} aria-hidden />Share
              </button>
            )}
          </div>
          <p className="text-t12 text-fg-muted" data-joined>{joinedLine(data.joined)}</p>
        </>
      ) : data.code ? (
        <p className="text-t14 text-fg">Your invite code: <strong className="font-mono">{data.code}</strong></p>
      ) : (
        <div><button type="button" onClick={onCreate} disabled={busy} className={LINK_PRIMARY}>{busy ? "Creating…" : "Create my invite link"}</button></div>
      )}
    </div>
  );
}

export default function InviteFriendV2() {
  const [state, setState] = useState<State>({ status: "loading" });
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    referralApi.card().then((data) => { if (live) setState({ status: "ok", data }); }, () => { if (live) setState({ status: "error" }); });
    return () => { live = false; };
  }, []);
  const create = () => {
    setBusy(true);
    referralApi.createCode().then((data) => setState({ status: "ok", data }), () => setState({ status: "error" })).finally(() => setBusy(false));
  };
  return (
    <CardV2 testId="invite-friend" title="Invite a friend">
      {state.status === "loading" ? <LoadingRowsV2 rows={2} /> : state.status === "error" ? <LoadFailedV2 what="your invite link" />
        : <InviteFriendBodyV2 data={state.data} onCreate={create} busy={busy} />}
    </CardV2>
  );
}

