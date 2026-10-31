import { useEffect, useId, useState } from "react";
import { CARD } from "../dashboard/CardV2";
import StepUpDialog, { isStepUpError } from "../components/StepUpDialog";
import { friendlyError } from "../api/errors";
import { growthSettingsApi, learnBody, type LearnSettings, type ReferralSettings } from "./growthSettingsApi";
import { SkeletonRows } from "../components/primitives";
import { Details } from "../ui/Details";

const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const INPUT = `h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg placeholder:text-fg-muted md:h-9 ${FOCUS}`;
const SAVE = `inline-flex h-11 shrink-0 items-center justify-center rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand hover:bg-brand-strong disabled:cursor-not-allowed disabled:opacity-60 md:h-9 ${FOCUS}`;

/**
 * Readiness C13, Admin › Users & business: the two growth settings the operator owns - the Learn page's video links
 * (one per lesson, plus the full-course link; empty by default, https only) and the "Invite a friend" reward wording
 * with the referral counts. Saving needs a recent two-factor check (the dialog asks for the code, then saves again).
 * Each change is audited on the server without the addresses.
 */
export default function GrowthSettingsPanelV2() {
  return (
    <>
      <LearnVideosCard />
      <ReferralCard />
    </>
  );
}

function useSave<T>(save: () => Promise<T>, onDone: (value: T) => void) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [stepUp, setStepUp] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setMessage(null);
    try {
      onDone(await save());
      setMessage({ ok: true, text: "Saved." });
    } catch (err) {
      if (isStepUpError(err)) setStepUp(String(err));
      else setMessage({ ok: false, text: friendlyError(err) });
    } finally {
      setBusy(false);
    }
  };
  const dialog = stepUp ? <StepUpDialog reason={stepUp} onCancel={() => setStepUp(null)} onVerified={() => { setStepUp(null); void run(); }} /> : null;
  return { busy, message, run, dialog };
}

function Status({ message }: { message: { ok: boolean; text: string } | null }) {
  if (!message) return null;
  return <p role={message.ok ? "status" : "alert"} className={`text-t12 ${message.ok ? "text-up" : "text-down"}`}>{message.text}</p>;
}

function LearnVideosCard() {
  const [data, setData] = useState<LearnSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [videos, setVideos] = useState<Record<string, string>>({});
  const [course, setCourse] = useState("");
  const courseId = useId();
  useEffect(() => {
    let live = true;
    growthSettingsApi.learn().then((d) => { if (live) { setData(d); setVideos(d.videos); setCourse(d.course_url); } },
      (e) => { if (live) setError(friendlyError(e)); });
    return () => { live = false; };
  }, []);
  const save = useSave(() => growthSettingsApi.saveLearn(learnBody(videos, course)), (d) => { setVideos(d.videos); setCourse(d.course_url); });
  return (
    <section data-console-card="learn-videos" aria-label="Learn videos" className={`${CARD} p-space-4 md:p-space-5`}>
      <h2 className="text-t15 font-semibold text-fg">Learn videos</h2>
      <Details id="console.learn-videos" className="mt-space-1">A video link per lesson and one link to the full course. Empty fields show no video. Full https links only. They appear on the Learn page once it is turned on.</Details>
      {!data ? (error ? <p className="mt-space-3 text-t12 text-fg-muted">Could not load: {error}</p> : <SkeletonRows rows={2} label="Loading" />) : (
        <form className="mt-space-3 flex flex-col gap-space-3" onSubmit={(e) => { e.preventDefault(); void save.run(); }}>
          <label htmlFor={courseId} className="flex flex-col gap-space-1 text-t14 font-medium text-fg">Full course
            <input id={courseId} type="url" inputMode="url" placeholder="https://" value={course} onChange={(e) => setCourse(e.target.value)} className={INPUT} />
          </label>
          <ul className="grid grid-cols-1 gap-space-3 md:grid-cols-2">
            {(data.lessons ?? []).map((l) => (
              <li key={l.id}>
                <label className="flex flex-col gap-space-1 text-t14 text-fg">{l.title}
                  <input type="url" inputMode="url" placeholder="https://" value={videos[l.id] ?? ""} data-video-input={l.id}
                    onChange={(e) => setVideos((v) => ({ ...v, [l.id]: e.target.value }))} className={INPUT} />
                </label>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center gap-space-3">
            <button type="submit" disabled={save.busy} className={SAVE}>{save.busy ? "Saving…" : "Save video links"}</button>
            <Status message={save.message} />
          </div>
        </form>
      )}
      {save.dialog}
    </section>
  );
}

function ReferralCard() {
  const [data, setData] = useState<ReferralSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const textId = useId();
  useEffect(() => {
    let live = true;
    growthSettingsApi.referral().then((d) => { if (live) { setData(d); setText(d.reward_text); } }, (e) => { if (live) setError(friendlyError(e)); });
    return () => { live = false; };
  }, []);
  const save = useSave(() => growthSettingsApi.saveReferral(text), (d) => { setData(d); setText(d.reward_text); });
  return (
    <section data-console-card="referral-settings" aria-label="Invite a friend" className={`${CARD} p-space-4 md:p-space-5`}>
      <h2 className="text-t15 font-semibold text-fg">Invite a friend</h2>
      <Details id="console.referral-settings" className="mt-space-1">What the invite card says about the reward, in your own words. Leave it empty to name no reward. No reward is given automatically yet: payments will handle that.</Details>
      {!data ? (error ? <p className="mt-space-3 text-t12 text-fg-muted">Could not load: {error}</p> : <SkeletonRows rows={2} label="Loading" />) : (
        <form className="mt-space-3 flex flex-col gap-space-3" onSubmit={(e) => { e.preventDefault(); void save.run(); }}>
          <dl className="grid grid-cols-3 gap-space-3" data-referral-totals>
            {([["Invite links made", data.totals.links_created], ["People who invited", data.totals.referrers], ["Joined through a link", data.totals.joined]] as const).map(([label, n]) => (
              <div key={label} className="rounded-control border border-subtle p-space-3">
                <dt className="text-t12 text-fg-muted">{label}</dt><dd className="mt-space-1 font-tabular text-t14 font-semibold text-fg">{n}</dd>
              </div>
            ))}
          </dl>
          <label htmlFor={textId} className="flex flex-col gap-space-1 text-t14 font-medium text-fg">Reward wording
            <textarea id={textId} rows={2} maxLength={200} value={text} onChange={(e) => setText(e.target.value)}
              placeholder="For example: you both get one month of the Pro plan."
              className={`min-h-20 w-full rounded-control border border-border bg-surface-2 px-space-3 py-space-2 text-t14 text-fg placeholder:text-fg-muted ${FOCUS}`} />
          </label>
          <div className="flex flex-wrap items-center gap-space-3">
            <button type="submit" disabled={save.busy} className={SAVE}>{save.busy ? "Saving…" : "Save wording"}</button>
            <Status message={save.message} />
          </div>
        </form>
      )}
      {save.dialog}
    </section>
  );
}
