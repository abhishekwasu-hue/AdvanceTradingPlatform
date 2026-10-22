import { ChevronRight } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { CardV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { TAB_LABELS, KIND_ORDER, RHYTHM_REFRESH_MS, cleanBrief } from "./model";
import type { RhythmBrief, RhythmKind, RhythmSection, RhythmToday } from "./types";

/**
 * MO4 (Master Order 4 part 1, the daily rhythm; behind `ui_v2` + `daily_rhythm`): the Dashboard's brief card - the
 * "ready-made dish". It opens on the digest of the moment (before the open, the pulse during the session, the closing
 * wrap after it, the week at the weekend) and the four small tabs show the others. Each section is a few plain lines
 * from the user's own data and the market's, with the page it opens; a section with nothing says so in plain words (no
 * sample figure). Structure words only: every line passes the signal-language check again here.
 */

type State = { status: "loading" } | { status: "error" } | { status: "ok"; data: RhythmToday };

export function RhythmSectionV2({ section }: { section: RhythmSection }) {
  return (
    <div data-rhythm-section={section.id} className="py-space-3 first:pt-0 last:pb-0">
      <div className="flex min-h-11 items-center justify-between gap-space-3 md:min-h-0">
        <h3 className="text-t14 font-semibold text-fg">{section.title}</h3>
        {section.link && (
          <Link to={section.link.to}
            className="inline-flex h-11 shrink-0 items-center gap-space-1 rounded-control text-t13 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-auto">
            {section.link.label} <ChevronRight size={14} aria-hidden />
          </Link>
        )}
      </div>
      {section.lines.length === 0 ? (
        <p className="mt-px text-t14 text-fg-muted">{section.empty}</p>
      ) : (
        <ul className="mt-space-1 space-y-space-1">
          {section.lines.map((line) => (
            <li key={line} className="flex gap-space-2 text-t14 text-fg">
              <span aria-hidden className="mt-[0.55em] h-1.5 w-1.5 shrink-0 rounded-pill bg-fg-muted" />
              <span className="min-w-0 [overflow-wrap:anywhere]">{line}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function RhythmBriefV2({ brief, phase, kind, onKind }: {
  brief: RhythmBrief; phase: RhythmKind; kind: RhythmKind; onKind: (k: RhythmKind) => void;
}) {
  const shown = cleanBrief(brief);
  return (
    <div>
      <p className="text-t13 text-fg-muted">{shown.subtitle}</p>
      <div role="tablist" aria-label="Daily briefs" className="mt-space-3 grid grid-cols-4 gap-px rounded-control border border-subtle bg-surface-2 p-px">
        {KIND_ORDER.map((k) => (
          <button key={k} type="button" role="tab" aria-selected={k === kind} data-rhythm-tab={k} onClick={() => onKind(k)}
            className={`relative inline-flex h-11 min-w-0 items-center justify-center rounded-control px-space-1 text-t13 font-medium transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9 ${
              k === kind ? "bg-surface-1 text-fg shadow-card" : "text-fg-muted hover:text-fg"}`}>
            <span className="truncate">{TAB_LABELS[k]}</span>
            {k === phase && <span aria-label="now" className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-pill bg-brand" />}
          </button>
        ))}
      </div>
      <div className="mt-space-4 divide-y divide-subtle">
        {shown.sections.map((s) => <RhythmSectionV2 key={s.id} section={s} />)}
      </div>
      <p className="mt-space-4 text-t12 text-fg-muted">{shown.note}</p>
    </div>
  );
}

export default function DailyRhythmCardV2() {
  const [state, setState] = useState<State>({ status: "loading" });
  const [kind, setKind] = useState<RhythmKind | null>(null);

  const load = useCallback(async (k: RhythmKind | null) => {
    try {
      const data = await api.rhythmToday(k ?? undefined);
      setState({ status: "ok", data });
    } catch {
      setState((s) => (s.status === "ok" ? s : { status: "error" }));
    }
  }, []);

  useEffect(() => {
    void load(kind);
    const id = window.setInterval(() => { if (document.visibilityState !== "hidden") void load(kind); }, RHYTHM_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [kind, load]);

  const choose = (k: RhythmKind) => {
    if (state.status === "ok" && k === state.data.brief.kind) return;
    setKind(k);
  };

  const title = state.status === "ok" ? state.data.brief.title : "Your daily brief";
  return (
    <CardV2 title={title} testId="rhythm">
      {state.status === "loading" ? <LoadingRowsV2 rows={3} /> : state.status === "error" ? <LoadFailedV2 what="your daily brief" /> : (
        <RhythmBriefV2 brief={state.data.brief} phase={state.data.phase} kind={state.data.brief.kind} onKind={choose} />
      )}
    </CardV2>
  );
}
