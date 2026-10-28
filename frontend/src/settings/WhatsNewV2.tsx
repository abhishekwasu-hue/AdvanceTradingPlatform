import { CARD } from "../dashboard/CardV2";
import { Details } from "../ui/Details";
import { RELEASES, latestRelease } from "./whatsNew";

/** AUD-V7: the "What's new" sheet on Settings - the newest release's line, earlier ones under Details. */
export default function WhatsNewV2() {
  const latest = latestRelease();
  if (!latest) return null;
  const earlier = RELEASES.slice(1);
  return (
    <section data-card="What's new" aria-label="What's new" className={`${CARD} p-space-4`} data-whats-new>
      <h2 className="text-t14 font-semibold text-fg">What&apos;s new <span className="font-normal text-fg-muted">v{latest.version} · {latest.date}</span></h2>
      <p className="mt-space-1 text-t13 text-fg-muted">{latest.line}</p>
      {earlier.length > 0 && (
        <Details id="settings.whats-new" label="Earlier releases" className="mt-space-2 text-t13 text-fg-muted">
          <ul className="space-y-space-1">
            {earlier.map((r) => <li key={r.version}>v{r.version} · {r.date}: {r.line}</li>)}
          </ul>
        </Details>
      )}
    </section>
  );
}
