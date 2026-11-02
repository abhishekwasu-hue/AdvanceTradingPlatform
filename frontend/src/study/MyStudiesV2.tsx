import { useCallback, useEffect, useState } from "react";
import { Button, Dialog, Input } from "../components/primitives";
import { saveBlob } from "../journal/journalModel";
import { formatDateTimeIST } from "../ui/format";
import { studyApi, type StudyQuery } from "./api";
import { studyFormats, timeframeWords, type DownloadFormat, type StudySummary } from "./model";
import { cvdOn } from "./SaveStudySheetV2";

/**
 * M77 SX-7 (owner order M77 PD-1 / PD-4): My studies - his own saved studies, newest first, searched by symbol and
 * IST date, each downloadable again (PDF, image, CSV, JSON - rebuilt by the server from the stored study, the same
 * file every time) or deleted (after a confirm). Shown in place: inside the workspace's sheet and on the Journal page.
 */
type Load = { status: "loading" } | { status: "ready"; items: StudySummary[]; total: number } | { status: "failed"; text: string };

export function MyStudiesV2({ refresh = 0 }: { refresh?: number }) {
  const [q, setQ] = useState<StudyQuery>({});
  const [applied, setApplied] = useState<StudyQuery>({});
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<StudySummary | null>(null);
  const [n, setN] = useState(0);
  const reload = useCallback(() => setN((x) => x + 1), []);
  useEffect(() => {
    let live = true;
    setLoad({ status: "loading" });
    studyApi.list(applied).then(
      (r) => live && setLoad({ status: "ready", items: r.items, total: r.total }),
      (e) => live && setLoad({ status: "failed", text: e instanceof Error && e.message ? e.message : "Your studies could not be loaded." }),
    );
    return () => { live = false; };
  }, [applied, refresh, n]);
  const download = async (s: StudySummary, format: DownloadFormat, tf?: string) => {
    const key = `${s.id}-${format}-${tf ?? ""}`;
    setBusy(key);
    setNote(null);
    try {
      const file = await studyApi.download(s.id, format, { tf, cvd: cvdOn() });
      saveBlob(file.blob, file.filename);
      setNote(`${file.filename} is downloading.`);
    } catch (e) {
      setNote(e instanceof Error && e.message ? e.message : "The file could not be made. Try again.");
    } finally {
      setBusy(null);
    }
  };
  const remove = async (s: StudySummary) => {
    setConfirm(null);
    try {
      await studyApi.remove(s.id);
      setNote("The study is deleted.");
      reload();
    } catch (e) {
      setNote(e instanceof Error && e.message ? e.message : "The study could not be deleted. Try again.");
    }
  };
  return (
    <div className="space-y-space-3" data-my-studies={load.status}>
      <form className="grid gap-space-2 sm:grid-cols-[minmax(0,1fr)_auto_auto_auto] sm:items-end"
        onSubmit={(e) => { e.preventDefault(); setApplied({ ...q }); }}>
        <Input label="Symbol" value={q.symbol ?? ""} onChange={(e) => setQ((v) => ({ ...v, symbol: e.target.value }))} placeholder="Any symbol" data-studies-symbol />
        <Input label="From" type="date" value={q.dateFrom ?? ""} onChange={(e) => setQ((v) => ({ ...v, dateFrom: e.target.value }))} data-studies-from />
        <Input label="To" type="date" value={q.dateTo ?? ""} onChange={(e) => setQ((v) => ({ ...v, dateTo: e.target.value }))} data-studies-to />
        <Button type="submit" variant="secondary" data-studies-search>Search</Button>
      </form>
      {note && <p role="status" className="text-t13 text-fg">{note}</p>}
      {load.status === "loading" && <p role="status" className="text-t13 text-fg-muted">Loading your studies…</p>}
      {load.status === "failed" && <p role="alert" className="text-t13 text-down">{load.text}</p>}
      {load.status === "ready" && !load.items.length && (
        <p className="text-t13 text-fg-muted">No saved studies{applied.symbol || applied.dateFrom || applied.dateTo ? " match this search" : " yet. Save one from the chart in the Copilot"}.</p>
      )}
      {load.status === "ready" && load.items.length > 0 && (
        <ul className="divide-y divide-border rounded-panel border border-subtle" data-studies-list>
          {load.items.map((s) => (
            <li key={s.id} className="space-y-space-2 p-space-3" data-study-row={s.id}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-space-3">
                <p className="min-w-0 break-words text-t14 font-semibold text-fg">{s.title || s.symbol}</p>
                <p className="text-t13 tabular-nums text-fg-muted">{formatDateTimeIST(s.created_at)}</p>
              </div>
              <p className="text-t13 text-fg-muted">{s.title ? `${s.symbol} · ` : ""}{timeframeWords(s.timeframes)}</p>
              <div className="flex flex-wrap gap-space-2">
                {studyFormats(s).map((f) => (
                  <Button key={`${f.format}-${f.tf ?? ""}`} size="sm" variant="outline" loading={busy === `${s.id}-${f.format}-${f.tf ?? ""}`}
                    onClick={() => download(s, f.format, f.tf)} data-study-download={`${f.format}${f.tf ? `-${f.tf}` : ""}`}>{f.label}</Button>
                ))}
                <Button size="sm" variant="ghost" onClick={() => setConfirm(s)} data-study-delete>Delete</Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {load.status === "ready" && load.total > load.items.length && (
        <p className="text-t13 text-fg-muted">Showing the newest {load.items.length} of {load.total}. Search by symbol or date to find older ones.</p>
      )}
      <Dialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)} title="Delete this study?"
        footer={<><Button variant="ghost" onClick={() => setConfirm(null)}>Keep it</Button>
          <Button variant="danger" onClick={() => confirm && remove(confirm)} data-study-delete-confirm>Delete</Button></>} />
    </div>
  );
}
