import { Microscope } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../../api/client";
import { ApiError } from "../../../api/errors";
import { Badge, Button, Input, Select, type Tone } from "../../../components/primitives";
import type { ResearchStatus, ResearchStudy, ResearchStudyDetail } from "../../../types";
import { useCopilotT } from "../../i18n";
import { Panel } from "../shared";
import { POLL_MS, isActive, metricLine, prob, progressFraction, shouldPoll, workerLooksIdle } from "./research";

/**
 * H-C3c-2: research studies in the Strategy Lab. A study is queued here and run by the research worker in the
 * background; the list polls while any study is queued or running. A finished study shows how many drafts were tried
 * and judges the chosen one against all of them (deflated Sharpe, overfitting probability, the unseen-session check).
 * Nothing here saves a strategy or places an order.
 */
const TONE: Record<ResearchStatus, Tone> = { queued: "neutral", running: "info", done: "up", failed: "down", interrupted: "warn" };

export default function ResearchPanel({ symbol }: { symbol: string }) {
  const t = useCopilotT();
  const [studies, setStudies] = useState<ResearchStudy[]>([]);
  const [off, setOff] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [idea, setIdea] = useState("");
  const [timeframe, setTimeframe] = useState("15min");
  const [drafts, setDrafts] = useState("6");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<ResearchStudyDetail | null>(null);

  const load = useCallback(async () => {
    try {
      setStudies((await api.aiResearchList()).studies);
      setOff(false);
    } catch (error) {
      if (error instanceof ApiError && error.status === 503) setOff(true);
      else setMessage(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!shouldPoll(studies)) return undefined;
    const timer = window.setInterval(() => { void load(); }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [studies, load]);

  async function queue() {
    setBusy(true);
    setMessage(null);
    try {
      await api.aiResearchCreate({ idea: idea.trim(), symbol: symbol.trim().toUpperCase(), timeframe, max_drafts: Number(drafts) });
      setMessage(t("lab.research.queued"));
      setIdea("");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function show(id: string) {
    try {
      setOpen(await api.aiResearchGet(id));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  if (off) {
    return <Panel title={t("lab.research.title")} icon={<Microscope size={15} />} testId="lab-research"><p className="text-sm text-fg-muted">{t("lab.research.off")}</p></Panel>;
  }
  const now = Date.now();
  return (
    <Panel title={t("lab.research.title")} icon={<Microscope size={15} />} testId="lab-research">
      <p className="mb-3 text-xs text-fg-muted">{t("lab.research.intro")}</p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[16rem] flex-1">
          <Input label={t("lab.research.idea")} value={idea} placeholder={t("lab.research.ideaPlaceholder")} onChange={(e) => setIdea(e.target.value)} />
        </div>
        <div className="w-32"><Select label={t("lab.research.timeframe")} value={timeframe} onChange={setTimeframe}
          options={["5min", "15min", "30min", "60min"].map((v) => ({ value: v, label: v }))} /></div>
        <div className="w-28"><Select label={t("lab.research.drafts")} value={drafts} onChange={setDrafts}
          options={["2", "4", "6", "8"].map((v) => ({ value: v, label: v }))} /></div>
        <Button variant="primary" disabled={busy || idea.trim().length < 3 || !symbol.trim()} onClick={() => void queue()} data-testid="research-queue">
          {t("lab.research.queue")}
        </Button>
      </div>
      {message && <p className="mt-2 text-xs text-fg-muted" role="status">{message}</p>}

      {studies.length === 0 ? <p className="mt-3 text-sm text-fg-muted">{t("lab.research.empty")}</p> : (
        <ul className="mt-3 space-y-2" data-testid="research-list">
          {studies.map((s) => (
            <li key={s.study_id} className="rounded-lg border border-border p-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={TONE[s.status]}>{t(`lab.research.status.${s.status}`)}</Badge>
                <span className="font-semibold text-fg">{s.symbol} · {s.timeframe}</span>
                <span className="min-w-0 flex-1 truncate text-fg-muted" title={s.idea}>{s.idea}</span>
                {isActive(s) && <span className="text-fg-muted">{t("lab.research.progress", { tried: s.progress.drafts_tried, max: s.progress.max_drafts })}</span>}
                <Button size="sm" onClick={() => void show(s.study_id)}>{t("lab.research.open")}</Button>
              </div>
              {isActive(s) && (
                <div className="mt-1 h-1 rounded bg-surface-3"><div className="h-1 rounded bg-ai" style={{ width: `${Math.round(progressFraction(s) * 100)}%` }} /></div>
              )}
              {s.error && <p className="mt-1 text-down">{s.error}</p>}
              {workerLooksIdle(s, now) && <p className="mt-1 text-warn">{t("lab.research.waiting")}</p>}
            </li>
          ))}
        </ul>
      )}

      {open && <StudyReport detail={open} />}
    </Panel>
  );
}

function StudyReport({ detail }: { detail: ResearchStudyDetail }) {
  const t = useCopilotT();
  const r = detail.report;
  if (!r) return <p className="mt-3 text-sm text-fg-muted" data-testid="research-report">{t("lab.research.noReport")}</p>;
  return (
    <div className="mt-4 space-y-3 text-xs" data-testid="research-report">
      <div>
        <h4 className="font-semibold text-fg">{t("lab.research.summary")}</h4>
        <p className="text-fg">{r.summary}</p>
      </div>
      <dl className="grid grid-cols-1 gap-1 sm:grid-cols-3">
        <div><dt className="text-fg-muted">{t("lab.research.deflated")}</dt><dd className="font-tabular text-fg">{prob(r.deflated?.dsr)}</dd>{r.deflated?.note && <dd className="text-fg-muted">{r.deflated.note}</dd>}</div>
        <div><dt className="text-fg-muted">{t("lab.research.pbo")}</dt><dd className="font-tabular text-fg">{prob(r.pbo?.pbo)}</dd>{r.pbo?.note && <dd className="text-fg-muted">{r.pbo.note}</dd>}</div>
        <div><dt className="text-fg-muted">{t("lab.research.oos")}</dt>
          <dd className="text-fg">{r.out_of_sample.run ? metricLine(r.out_of_sample as Record<string, unknown>) || "—" : (r.out_of_sample.note ?? t("lab.research.oosNotRun"))}</dd></div>
      </dl>
      <div>
        <h4 className="mb-1 font-semibold text-fg">{t("lab.research.trialsTable")}</h4>
        <table className="w-full text-left">
          <thead className="text-fg-muted"><tr><th className="pr-2">{t("lab.research.seq")}</th><th className="pr-2">{t("lab.research.trialStatus")}</th><th className="pr-2">{t("lab.research.metrics")}</th><th>{t("lab.research.reason")}</th></tr></thead>
          <tbody>
            {detail.trials.map((tr) => (
              <tr key={tr.seq} className="border-t border-border">
                <td className="pr-2 font-tabular">{tr.seq}</td>
                <td className="pr-2">{tr.status}</td>
                <td className="pr-2 font-tabular">{metricLine(tr.metrics)}</td>
                <td className="text-fg-muted">{tr.reason ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-fg-muted">{t("lab.research.notSimulated")}: {r.not_simulated.join(", ")}</p>
      <p className="text-fg-muted">{r.disclaimer}</p>
    </div>
  );
}
