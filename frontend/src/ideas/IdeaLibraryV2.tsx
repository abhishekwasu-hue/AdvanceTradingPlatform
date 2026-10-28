import { ChevronDown, History, Play, Search } from "lucide-react";
import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/errors";
import { CARD, ChipV2, LINK_PRIMARY, LINK_SECONDARY, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import type { PageProps } from "../routes";
import { PageTitleV2 } from "../trading/PartsV2";
import { ComingSoon } from "../ui/ComingSoon";
import { Details } from "../ui/numbersFirst";
import { EducationFrame } from "../ui/EducationFrame";
import { useFeatures } from "../ui/useFeatures";
import { paperBotPathFor, testPathFor } from "../strategyTemplates/link";
import { ideasApi, type Idea, type IdeaLibrary } from "./api";
import { ALL_GROUPS, IDEA_FLAG, defaultText, ideasIn, rangeText, screenerPathFor, suggestIdeas } from "./model";

/**
 * Idea library slice 1 (owner order ATP_SESSION_IDEA_LIBRARY.md, T1-T8) at `/ideas`, v2 shell only, behind the
 * `idea_library` flag (the shell shows "Coming soon" while it is off; the server answers 404). Well-known methods as
 * white-box cards: the idea in plain words, how it reads the chart, how a signal reads (structure words), the settings
 * the trader chooses, the decisions that stay his, and where the idea comes from. Education, not advice: no security
 * names, no prices, no performance claims (the content guard in the backend tests).
 *
 * "Try as a scan" opens the idea's screen in Find setups (`/screener?idea=..&scan=..`), built from the existing registry
 * only. Slice 2 (IDEA-1): "Adopt as a paper strategy" appears only on an idea the server maps HONESTLY onto a strategy
 * template (`adopt.available`, with `strategy_templates` on too); it opens the Paper bot form with the template picked
 * and the idea's settings as the pre-fill, which the user reviews and saves himself (PAPER only). Every other idea says
 * it comes later. Slice 3: T2 (A2) and T5 (A5) are mapped; the card shows the mapping's note (where the paper strategy
 * still reads less than the card). Nothing here places an order.
 */
export default function IdeaLibraryV2(_props: PageProps = {}) {
  const features = useFeatures();
  const [library, setLibrary] = useState<IdeaLibrary | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "failed" | "off">("loading");

  useEffect(() => {
    let alive = true;
    ideasApi.library()
      .then((l) => { if (alive) { setLibrary(l); setState("ready"); } })
      .catch((e: unknown) => { if (alive) setState(e instanceof ApiError && e.status === 404 ? "off" : "failed"); });
    return () => { alive = false; };
  }, []);

  if (state === "off") return <ComingSoon flag={IDEA_FLAG} />;

  return (
    <div className="flex flex-col gap-space-5" data-page="ideas">
      <PageTitleV2 title={library?.header.title ?? "Idea library"}
        subtitle={library?.header.subtitle ?? "Well-known chart-reading methods, explained in plain words."} />
      {state === "loading" && <section className={`${CARD} p-space-4 md:p-space-5`} aria-busy="true"><LoadingRowsV2 rows={4} /></section>}
      {state === "failed" && <section className={`${CARD} p-space-4 md:p-space-5`}><LoadFailedV2 what="the idea library" /></section>}
      {library && <IdeaLibraryView library={library} adoptOn={features.on("strategy_templates")} />}
    </div>
  );
}

/** The library once loaded: the frame, the beginner questions, the group filter and the cards. The starting group and
 * answers are props so the static render tests can show any state. */
export function IdeaLibraryView({ library, initialGroup = ALL_GROUPS, initialAnswers = {}, adoptOn = false }: {
  library: IdeaLibrary; initialGroup?: string; initialAnswers?: Record<string, string>;
  /** `strategy_templates` is on (the server checks it too): only then can a mapped idea be adopted. */
  adoptOn?: boolean;
}) {
  const [group, setGroup] = useState(initialGroup);
  return (
    <>
      <Frame lines={library.header.frame} />
      <Beginner library={library} initialAnswers={initialAnswers} />
      <GroupFilter library={library} group={group} onChange={setGroup} />
      <div className="grid grid-cols-1 gap-space-4 lg:grid-cols-2" data-block="idea-cards">
        {ideasIn(library.ideas, group).map((idea) => (
          <IdeaCard key={idea.id} idea={idea} groupName={library.groups.find((g) => g.id === idea.group)?.name ?? ""}
            adoptLater={library.header.adopt_later} adoptOn={adoptOn} />
        ))}
      </div>
    </>
  );
}

/** The compliance frame, always above the cards: education, no claims, the trader's own settings. */
function Frame({ lines }: { lines: string[] }) {
  return <EducationFrame block="idea-frame" label="About these ideas" lines={lines} />;
}

/** "Not sure where to start?": three questions, then two or three ideas to read first (a reading list, not advice). */
function Beginner({ library, initialAnswers }: { library: IdeaLibrary; initialAnswers: Record<string, string> }) {
  const { beginner } = library;
  const [answers, setAnswers] = useState<Record<string, string>>(initialAnswers);
  const titleId = useId();
  const order = useMemo(() => library.ideas.map((i) => i.id), [library.ideas]);
  const picks = suggestIdeas(beginner.questions, answers, order);
  const byId = new Map(library.ideas.map((i) => [i.id, i]));
  return (
    <section aria-labelledby={titleId} data-block="idea-beginner" className={`${CARD} p-space-4 md:p-space-5`}>
      <h2 id={titleId} className="flex items-center gap-space-2 text-t15 font-semibold text-fg">
        <Search size={16} aria-hidden className="text-brand" />{beginner.title}
      </h2>
      <Details id="ideas.beginner" className="mt-space-1"><p className="text-t14 text-fg-muted">{beginner.intro}</p></Details>
      <div className="mt-space-3 grid grid-cols-1 gap-space-3 md:grid-cols-3">
        {beginner.questions.map((q) => (
          <fieldset key={q.id} className="min-w-0">
            <legend className="mb-space-2 text-t14 font-medium text-fg">{q.text}</legend>
            <div className="flex flex-wrap gap-space-2">
              {q.options.map((o) => {
                const on = answers[q.id] === o.id;
                return (
                  <button key={o.id} type="button" aria-pressed={on} onClick={() => setAnswers((a) => ({ ...a, [q.id]: o.id }))}
                    className={`inline-flex min-h-11 items-center rounded-control border px-space-3 text-t14 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9 ${on ? "border-brand bg-brand/10 font-semibold text-brand" : "border-border bg-surface-1 text-fg hover:bg-surface-2"}`}>
                    {o.label}
                  </button>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>
      {picks.length > 0 && (
        <div className="mt-space-4 flex flex-col gap-space-2 sm:flex-row sm:items-center" data-block="idea-suggestions">
          <span className="text-t14 font-medium text-fg">Start by reading:</span>
          <ul className="flex flex-wrap gap-space-2">
            {picks.map((id) => (
              <li key={id}>
                <a href={`#idea-${id}`} className="inline-flex min-h-11 items-center rounded-control px-space-2 text-t14 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-0">
                  {byId.get(id)?.name ?? id}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function GroupFilter({ library, group, onChange }: { library: IdeaLibrary; group: string; onChange: (g: string) => void }) {
  const options = [{ id: ALL_GROUPS, name: "All ideas" }, ...library.groups];
  return (
    <div role="group" aria-label="Show ideas" className="-mx-space-1 flex gap-space-2 overflow-x-auto px-space-1 pb-space-1" data-block="idea-groups">
      {options.map((g) => {
        const on = g.id === group;
        const count = ideasIn(library.ideas, g.id).length;
        return (
          <button key={g.id} type="button" aria-pressed={on} onClick={() => onChange(g.id)}
            className={`inline-flex min-h-11 shrink-0 items-center gap-space-1 rounded-pill border px-space-3 text-t14 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9 ${on ? "border-brand bg-brand/10 font-semibold text-brand" : "border-border bg-surface-1 text-fg hover:bg-surface-2"}`}>
            {g.name}<span className="text-t12 text-fg-muted">{count}</span>
          </button>
        );
      })}
    </div>
  );
}

// A scan's label can be long ("Inverse head and shoulders: neckline retest"): on a phone it wraps inside the card
// instead of pushing the card past the screen edge.
const SCAN_LINK = `${LINK_SECONDARY.replace("h-11", "min-h-11").replace("md:h-9", "md:min-h-9")} max-w-full py-space-2 text-left md:py-0`;

function IdeaCard({ idea, groupName, adoptLater, adoptOn }: { idea: Idea; groupName: string; adoptLater: string; adoptOn: boolean }) {
  const titleId = useId();
  const open = idea.scans.filter((s) => s.available);
  const adoptPath = adoptOn ? adoptPathFor(idea) : null;
  const adoptNote = adoptPath && idea.adopt.available ? idea.adopt.note ?? null : null;
  const testPath = adoptPath ? testPathForIdea(idea) : null;
  return (
    <article id={`idea-${idea.id}`} aria-labelledby={titleId} data-idea={idea.id} className={`${CARD} flex min-w-0 scroll-mt-space-6 flex-col p-space-4 md:p-space-5`}>
      <div className="flex flex-wrap items-center gap-space-2">
        <ChipV2>{groupName}</ChipV2>
        <ChipV2 kind={idea.difficulty === "Beginner" ? "brand" : "outline"}>{idea.difficulty}</ChipV2>
      </div>
      <h3 id={titleId} className="mt-space-2 text-t15 font-semibold text-fg">{idea.name}</h3>
      <p className="mt-space-1 text-t14 text-fg">{idea.one_line}</p>
      <p className="mt-space-2 text-t14 text-fg-muted"><span className="font-medium text-fg">Works best when: </span>{idea.works_best_when}</p>

      <div className="mt-space-3" data-block="idea-scans">
        {idea.scans.length > 0 && open.length > 0 && (
          <div>
            <p className="mb-space-2 text-t14 font-medium text-fg">Try as a scan</p>
            <div className="flex flex-wrap gap-space-2">
              {open.map((s) => (
                <Link key={s.id} to={screenerPathFor(idea.id, s.id)} className={SCAN_LINK} data-scan={s.id}
                  aria-label={`Try as a scan: ${s.label}`}>
                  <Search size={14} aria-hidden className="shrink-0" />{s.label}
                </Link>
              ))}
            </div>
          </div>
        )}
        {open.length > 0 && open.length < idea.scans.length && (
          <p className="mt-space-2 text-t14 text-fg-muted" data-state="scan-some-off">The other scans for this idea are not switched on for your account yet.</p>
        )}
        {idea.scans.length > 0 && open.length === 0 && (
          <p className="text-t14 text-fg-muted" data-state="scan-off">The scans for this idea are not switched on for your account yet.</p>
        )}
        {idea.scan_gaps && <p className="mt-space-2 text-t12 text-fg-muted" data-state="scan-gaps">{idea.scan_gaps}</p>}
      </div>

      {adoptPath && (
        <div className="mt-space-3" data-block="idea-adopt">
          <div className="flex flex-wrap items-center gap-space-2">
            <Link to={adoptPath} className={LINK_PRIMARY} data-adopt={idea.adopt.available ? idea.adopt.template : undefined} aria-label={`Adopt as a paper strategy: ${idea.name}`}>
              <Play size={14} aria-hidden />Adopt as a paper strategy
            </Link>
            {/* Day-one fix 1: the idea's own strategy on past prices first (Strategies › Test; nothing starts). */}
            {testPath && (
              <Link to={testPath} className={LINK_SECONDARY} data-test-idea={idea.adopt.available ? idea.adopt.template : undefined}
                aria-label={`Test it on past prices: ${idea.name}`}>
                <History size={14} aria-hidden />Test it on past prices
              </Link>
            )}
          </div>
          <p className="mt-space-2 text-t12 text-fg-muted">{ADOPT_NOTE}</p>
          {adoptNote && <p className="mt-space-1 text-t12 text-fg-muted" data-state="adopt-note">{adoptNote}</p>}
        </div>
      )}

      <details className="group mt-space-3 border-t border-subtle pt-space-2">
        <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-space-2 rounded-control text-t14 font-semibold text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9 [&::-webkit-details-marker]:hidden">
          Read the full idea
          <ChevronDown size={16} aria-hidden className="shrink-0 transition-transform duration-fast group-open:rotate-180" />
        </summary>
        <div className="mt-space-2 flex flex-col gap-space-4 text-t14 text-fg">
          <Part title="In plain words">
            {idea.plain.map((p) => <p key={p} className="mt-space-1 first:mt-0">{p}</p>)}
          </Part>
          <Part title="How it reads the chart">
            <dl className="grid grid-cols-1 gap-space-2 sm:grid-cols-[8rem_1fr]">
              {([["Context", idea.logic.context], ["Area", idea.logic.area], ["Trigger", idea.logic.trigger], ["Wrong if", idea.logic.invalidation]] as const).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="font-medium text-fg-muted">{k}</dt>
                  <dd className="min-w-0">{v}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-space-3 font-medium text-fg-muted">Evidence it lists</p>
            <ul className="mt-space-1 list-disc pl-space-5">{idea.logic.evidence.map((e) => <li key={e}>{e}</li>)}</ul>
            <p className="mt-space-3 text-fg-muted"><span className="font-medium text-fg">Tends to fail when: </span>{idea.tends_to_fail_when}</p>
          </Part>
          <Part title="How a signal reads">
            <ul className="flex flex-col gap-space-2">
              {idea.structure_words.map((w) => <li key={w} className="rounded-control bg-surface-2 px-space-3 py-space-2">{w}</li>)}
            </ul>
            <p className="mt-space-2 text-t12 text-fg-muted">Structure words only. On your own signals the area is also shown as a range.</p>
          </Part>
          {idea.settings.length > 0 && (
            <Part title="Settings you choose">
              <ul className="flex flex-col gap-space-2">
                {idea.settings.map((s) => (
                  <li key={s.key}>
                    <span className="font-medium">{s.label}</span>
                    <span className="text-fg-muted">{` - starts at ${defaultText(s)}${rangeText(s) ? ` (${rangeText(s)})` : ""}`}</span>
                    <p className="text-t12 text-fg-muted">{s.help}</p>
                  </li>
                ))}
              </ul>
            </Part>
          )}
          <Part title="Always your decision">
            <ul className="list-disc pl-space-5">{idea.your_decisions.map((d) => <li key={d}>{d}</li>)}</ul>
          </Part>
          <Part title="Where it comes from"><p>{idea.provenance}</p></Part>
          {idea.scans.length > 0 && (
            <Part title="The scan in full">
              <p className="text-t12 text-fg-muted">You can see and change every part of it in Find setups.</p>
              <ul className="mt-space-2 flex flex-col gap-space-2">
                {idea.scans.map((s) => (
                  <li key={s.id}>
                    <p className="text-t12 font-medium text-fg-muted">{s.label}</p>
                    <code className="mt-space-1 block overflow-x-auto whitespace-pre-wrap break-words rounded-control bg-surface-2 px-space-3 py-space-2 font-mono text-t12 text-fg">{s.text}</code>
                  </li>
                ))}
              </ul>
            </Part>
          )}
          <Part title="Use as a paper strategy">
            {adoptPath ? <p className="text-fg-muted" data-state="adopt-ready">{ADOPT_NOTE}{adoptNote ? ` ${adoptNote}` : ""}</p>
              : <p className="text-fg-muted" data-state="adopt-later">{adoptLater}</p>}
          </Part>
        </div>
      </details>
    </article>
  );
}

/** What "Adopt as a paper strategy" does, in plain words (PAPER only; the user saves it himself). */
export const ADOPT_NOTE = "Opens the paper bot form with this idea's settings filled in. You review it and start it yourself; it runs in PAPER.";

/** Day-one fix 1: the Strategies › Test link of an idea the server lets the user adopt now, or null. */
export function testPathForIdea(idea: Pick<Idea, "adopt">): string | null {
  const a = idea.adopt;
  return a.available ? testPathFor({ template: a.template, timeframe: a.timeframe, params: a.params }) : null;
}

/** The Paper bot link of an idea the server lets the user adopt now, or null ("comes later"). */
export function adoptPathFor(idea: Pick<Idea, "adopt">): string | null {
  const a = idea.adopt;
  return a.available ? paperBotPathFor({ template: a.template, timeframe: a.timeframe, params: a.params }) : null;
}

function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h4 className="mb-space-1 text-t12 font-semibold uppercase tracking-wide text-fg-muted">{title}</h4>
      {children}
    </section>
  );
}
