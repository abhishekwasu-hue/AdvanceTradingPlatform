import { ChevronDown, ExternalLink, GraduationCap, PlayCircle, Search } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/errors";
import { CARD, LINK_SECONDARY, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import type { PageProps } from "../routes";
import { PageTitleV2 } from "../trading/PartsV2";
import { ComingSoon } from "../ui/ComingSoon";
import { learnApi, type LearnLibrary, type Lesson } from "./api";
import { ALL, LEARN_FLAG, isAppPath, learnGlossary, lessonsIn, safeVideoUrl, searchLessons } from "./model";
import { EducationFrame } from "../ui/EducationFrame";
import { PageTabs } from "../ui/PageTabs";

/** The lessons a newcomer reads first, in this order (each only when the library has it). */
const START_HERE = ["atp-how", "paper-why", "risk-before"] as const;
const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

/**
 * Readiness C13 at `/learn`, v2 shell only, behind `learn` (the shell shows "Coming soon" while it is off; the server
 * answers 404). Short lessons in plain English - reading price structure, risk and the trader's own settings, practising
 * on paper, how ATP works - and the glossary. Video links appear only once the operator adds them (Admin › Users &
 * business › Learn videos). Education only: no security names, no prices, no trade calls, no performance claims (the
 * backend content guard runs over every lesson line). Nothing here places an order.
 */
export default function LearnPageV2(_props: PageProps = {}) {
  const [library, setLibrary] = useState<LearnLibrary | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "failed" | "off">("loading");

  useEffect(() => {
    let alive = true;
    learnApi.library()
      .then((l) => { if (alive) { setLibrary(l); setState("ready"); } })
      .catch((e: unknown) => { if (alive) setState(e instanceof ApiError && e.status === 404 ? "off" : "failed"); });
    return () => { alive = false; };
  }, []);

  if (state === "off") return <ComingSoon flag={LEARN_FLAG} />;
  return (
    <div className="flex flex-col gap-space-5" data-page="learn">
      <PageTitleV2 title={library?.header.title ?? "Learn"}
        subtitle={library?.header.subtitle ?? "Short lessons on reading the chart, managing risk and practising on paper."} />
      {state === "loading" && <section className={`${CARD} p-space-4 md:p-space-5`} aria-busy="true"><LoadingRowsV2 rows={4} /></section>}
      {state === "failed" && <section className={`${CARD} p-space-4 md:p-space-5`}><LoadFailedV2 what="the lessons" /></section>}
      {library && <LearnView library={library} />}
    </div>
  );
}

/** The page once loaded. `initialTab` / `initialOpen` let the static render tests show any state. */
export function LearnView({ library, initialTab = "lessons", initialOpen = [] }: {
  library: LearnLibrary; initialTab?: "lessons" | "glossary"; initialOpen?: string[];
}) {
  const [tab, setTab] = useState(initialTab);
  const [group, setGroup] = useState(ALL);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Set<string>>(() => new Set(initialOpen));
  const searchId = useId();
  const shown = useMemo(() => searchLessons(lessonsIn(library.lessons, group), query), [library.lessons, group, query]);
  const glossary = useMemo(() => learnGlossary(tab === "glossary" ? query : ""), [tab, query]);
  const byKey = useMemo(() => new Map(learnGlossary().map((e) => [e.key, e])), []);
  const toggle = (id: string) => setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const startHere = START_HERE.map((id) => library.lessons.find((l) => l.id === id)).filter((l): l is Lesson => !!l);
  const course = safeVideoUrl(library.course_url);

  return (
    <>
      <EducationFrame block="learn-frame" label="About these lessons" lines={library.header.frame} />

      {startHere.length > 0 && (
        <section data-block="learn-start" aria-label="Start here" className={`${CARD} p-space-4 md:p-space-5`}>
          <div className="flex flex-col gap-space-3 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0">
              <h2 className="flex items-center gap-space-2 text-t15 font-semibold text-fg">
                <GraduationCap size={16} aria-hidden className="text-brand" />New here? Start with these
              </h2>
              <ol className="mt-space-2 flex flex-col gap-space-1 text-t14 md:flex-row md:flex-wrap md:gap-space-4">
                {startHere.map((l, i) => (
                  <li key={l.id}>
                    <a href={`#lesson-${l.id}`} onClick={() => { setTab("lessons"); setGroup(ALL); setQuery(""); setOpen((s) => new Set(s).add(l.id)); }}
                      className={`inline-flex min-h-11 items-center gap-space-2 rounded-control font-medium text-brand hover:underline md:min-h-0 ${FOCUS}`}>
                      <span className="inline-flex h-6 w-6 items-center justify-center rounded-pill bg-brand/10 text-t12 font-semibold">{i + 1}</span>{l.title}
                    </a>
                  </li>
                ))}
              </ol>
            </div>
            {course && (
              <a href={course} target="_blank" rel="noopener noreferrer" className={LINK_SECONDARY} data-course-link>
                <PlayCircle size={16} aria-hidden />Open the full course<ExternalLink size={14} aria-hidden />
              </a>
            )}
          </div>
        </section>
      )}

      <div className="flex flex-col gap-space-3 md:flex-row md:items-center md:justify-between">
        <PageTabs label="Learn" value={tab} onChange={setTab}
          items={[{ key: "lessons", label: "Lessons" }, { key: "glossary", label: "Words explained" }] as const} />
        <label htmlFor={searchId} className="relative block w-full md:w-72">
          <span className="sr-only">{tab === "lessons" ? "Search the lessons" : "Search the words"}</span>
          <Search size={16} aria-hidden className="pointer-events-none absolute left-space-3 top-1/2 -translate-y-1/2 text-fg-muted" />
          <input id={searchId} type="search" value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder={tab === "lessons" ? "Search the lessons" : "Search the words"}
            className={`h-11 w-full rounded-control border border-border bg-surface-1 pl-9 pr-space-3 text-t14 text-fg placeholder:text-fg-muted md:h-9 ${FOCUS}`} />
        </label>
      </div>

      {tab === "lessons" ? (
        <>
          <div role="group" aria-label="Topics" className="-mx-space-1 flex gap-space-2 overflow-x-auto px-space-1 pb-space-1" data-block="learn-groups">
            {[{ id: ALL, name: "All topics" }, ...library.groups].map((g) => (
              <button key={g.id} type="button" aria-pressed={group === g.id} onClick={() => setGroup(g.id)}
                className={`inline-flex min-h-11 shrink-0 items-center rounded-pill border px-space-3 text-t14 md:min-h-9 ${group === g.id ? "border-brand bg-brand/10 font-semibold text-brand" : "border-border bg-surface-1 text-fg hover:bg-surface-2"} ${FOCUS}`}>
                {g.name}
              </button>
            ))}
          </div>
          {shown.length === 0 ? (
            <p className={`${CARD} p-space-4 text-t14 text-fg-muted`}>Try the glossary, or clear the search.</p>
          ) : (
            <ul className="grid grid-cols-1 items-start gap-space-3 lg:grid-cols-2" data-block="lessons">
              {shown.map((lesson) => (
                <LessonCard key={lesson.id} lesson={lesson} open={open.has(lesson.id)} onToggle={() => toggle(lesson.id)}
                  groupName={library.groups.find((g) => g.id === lesson.group)?.name ?? ""}
                  terms={lesson.glossary.map((k) => byKey.get(k)).filter((e): e is NonNullable<typeof e> => !!e)} />
              ))}
            </ul>
          )}
        </>
      ) : (
        <section className={`${CARD} p-space-4 md:p-space-5`} aria-label="Words explained" data-block="learn-glossary">
          {glossary.length === 0 ? <p className="text-t14 text-fg-muted">Clear the search to see every word.</p> : (
            <dl className="grid grid-cols-1 gap-x-space-6 gap-y-space-3 md:grid-cols-2">
              {glossary.map((e) => (
                <div key={e.key} className="min-w-0 border-b border-subtle pb-space-3" data-term={e.key}>
                  <dt className="text-t14 font-semibold text-fg">{e.term}</dt>
                  <dd className="mt-space-1 text-t14 text-fg-muted">{e.definition}</dd>
                </div>
              ))}
            </dl>
          )}
        </section>
      )}
    </>
  );
}

function LessonCard({ lesson, open, onToggle, groupName, terms }: {
  lesson: Lesson; open: boolean; onToggle: () => void; groupName: string;
  terms: { key: string; term: string; definition: string }[];
}) {
  const bodyId = useId();
  const video = safeVideoUrl(lesson.video_url);
  return (
    <li id={`lesson-${lesson.id}`} data-lesson={lesson.id} className={`${CARD} scroll-mt-space-10 p-space-4 md:p-space-5`}>
      <button type="button" aria-expanded={open} aria-controls={bodyId} onClick={onToggle}
        className={`flex w-full items-start justify-between gap-space-3 rounded-control text-left ${FOCUS}`}>
        <span className="min-w-0">
          <span className="block text-t12 font-medium uppercase tracking-wide text-fg-muted">{groupName}</span>
          <span className="mt-space-1 block text-t15 font-semibold text-fg">{lesson.title}</span>
          <span className="mt-space-1 block text-t14 text-fg-muted">{lesson.summary}</span>
        </span>
        <ChevronDown size={18} aria-hidden className={`mt-space-1 shrink-0 text-fg-muted transition-transform duration-fast ${open ? "rotate-180" : ""}`} />
      </button>
      <div id={bodyId} hidden={!open} className="mt-space-3">
        <ul className="flex list-disc flex-col gap-space-2 pl-space-5 text-t14 text-fg">{lesson.points.map((p) => <li key={p}>{p}</li>)}</ul>
        {terms.length > 0 && (
          <dl className="mt-space-3 flex flex-col gap-space-2 rounded-control bg-surface-2 p-space-3 text-t14">
            {terms.map((t) => (
              <div key={t.key}><dt className="inline font-semibold text-fg">{t.term}: </dt><dd className="inline text-fg-muted">{t.definition}</dd></div>
            ))}
          </dl>
        )}
        <div className="mt-space-3 flex flex-wrap gap-space-2">
          {lesson.next && isAppPath(lesson.next.path) && <Link to={lesson.next.path} className={LINK_SECONDARY}>{lesson.next.label}</Link>}
          {video && (
            <a href={video} target="_blank" rel="noopener noreferrer" className={LINK_SECONDARY} data-video-link>
              <PlayCircle size={16} aria-hidden />Watch the video<ExternalLink size={14} aria-hidden />
            </a>
          )}
        </div>
      </div>
    </li>
  );
}
