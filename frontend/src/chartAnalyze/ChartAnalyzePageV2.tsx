import { ImagePlus, Link2, TriangleAlert, Upload } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState, type DragEvent } from "react";
import { friendlyError } from "../api/errors";
import { Button, Dialog } from "../components/primitives";
import { PageIntro } from "../ui/PageIntro";
import { useFeatures } from "../ui/useFeatures";
import AlertDialogV2, { type AlertTarget } from "./AlertDialogV2";
import { chartAnalyzeApi, errorCode, isFeatureOff } from "./api";
import { renderResultPng, shareOrDownload } from "./exportImage";
import { printResult, printTitle } from "./printResult";
import ResultViewV2 from "./ResultViewV2";
import { resultShapes } from "./shapes";
import { guardResult } from "./signalLanguage";
import type { StrikesState } from "./StrikesSectionV2";
import type { AnalysisItem, AnalyzeStatus } from "./types";

/**
 * Analyze a chart (docs/design/CHART_ANALYZE.md): upload a chart image (file, paste, drag and drop) or paste a chart
 * link, choose a mode, press Analyze, read "what the method sees and the conditions". Behind the `chart_analyze` flag
 * (hidden while off). PR-A: Image only; Hybrid is shown, disabled, "Coming soon". Educational: no buy / sell call and no
 * order button anywhere on this page; the disclaimer closes every result.
 */

export const FLAG = "chart_analyze";
/** The "Option strikes · from your settings" block has its own flag on top (owner's tenant first). */
export const STRIKES_FLAG = "chart_analyze_strikes";
const ACCEPT = "image/png,image/jpeg";
const card = "rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5";

export const IMAGE_GONE = "The chart image is no longer stored (it is kept for a limited time), so the result image cannot be made.";
const SETUP_SHORT = { formed: "Formed", forming: "Forming", none: "No setup" } as const;

type Phase = "idle" | "adding" | "analysing";
interface Problem { message: string; reason?: string; code: string }

function problemFrom(e: unknown): Problem {
  const code = errorCode(e);
  let reason: string | undefined;
  try {
    const body = JSON.parse((e as { body?: string }).body ?? "") as { detail?: { reason?: string } };
    reason = body.detail?.reason;
  } catch { /* not JSON */ }
  return { message: friendlyError(e), reason, code };
}

function when(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function firstImage(files: FileList | File[] | null | undefined): File | null {
  for (const f of Array.from(files ?? [])) if (f.type === "image/png" || f.type === "image/jpeg") return f;
  return null;
}

export default function ChartAnalyzePageV2() {
  const features = useFeatures();
  const on = features.on(FLAG);
  const strikesOn = on && features.on(STRIKES_FLAG);
  const [status, setStatus] = useState<AnalyzeStatus | null>(null);
  const [off, setOff] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [link, setLink] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [problem, setProblem] = useState<Problem | null>(null);
  const [current, setCurrent] = useState<AnalysisItem | null>(null);
  const [imageSrc, setImageSrc] = useState<string | null>(null);
  const [imageNote, setImageNote] = useState<string | null>(null);
  const [history, setHistory] = useState<AnalysisItem[]>([]);
  const [alertTarget, setAlertTarget] = useState<AlertTarget | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState<"export" | "delete" | null>(null);
  const [dragging, setDragging] = useState(false);
  const [strikes, setStrikes] = useState<StrikesState | null>(null);
  const strikesFor = useRef<number | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const linkId = useId();
  const imageUrlRef = useRef<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [s, h] = await Promise.all([chartAnalyzeApi.status(), chartAnalyzeApi.list()]);
      setStatus(s);
      setHistory(h.items);
    } catch (e) {
      if (isFeatureOff(e)) setOff(true);
      else setProblem(problemFrom(e));
    }
  }, []);

  useEffect(() => { if (on) void reload(); }, [on, reload]);
  useEffect(() => () => { if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current); }, []);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  const pick = useCallback((f: File | null) => {
    if (!f) { setProblem({ message: "Choose a PNG or JPEG image.", code: "image_rejected" }); return; }
    setProblem(null);
    setLink("");
    setFile(f);
    setPreview(URL.createObjectURL(f));
  }, []);

  // Paste a screenshot anywhere on the page.
  useEffect(() => {
    if (!on) return;
    const onPaste = (e: ClipboardEvent) => {
      const f = firstImage(e.clipboardData?.files);
      if (f) { e.preventDefault(); pick(f); }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [on, pick]);

  const showItem = useCallback(async (item: AnalysisItem) => {
    setCurrent(item);
    setImageNote(null);
    try {
      const blob = await chartAnalyzeApi.image(item.id);
      if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
      imageUrlRef.current = URL.createObjectURL(blob);
      setImageSrc(imageUrlRef.current);
    } catch (e) {
      setImageSrc(null);
      setImageNote(errorCode(e) === "image_gone" ? IMAGE_GONE : "The chart image could not be loaded. Try again later.");
    }
  }, []);

  // "Option strikes · from your settings": loaded for the analysis on screen (the symbol read from the picture, or the
  // one the user types when it was not read). A late answer for another analysis is dropped.
  const loadStrikes = useCallback(async (id: number, symbol?: string) => {
    strikesFor.current = id;
    setStrikes({ status: "loading" });
    try {
      const data = await chartAnalyzeApi.strikes(id, symbol);
      if (strikesFor.current === id) setStrikes({ status: "ready", data });
    } catch (e) {
      if (strikesFor.current === id) setStrikes({ status: "error", message: friendlyError(e) });
    }
  }, []);

  const shownId = current?.result && strikesOn ? current.id : null;
  useEffect(() => {
    if (shownId == null) { strikesFor.current = null; setStrikes(null); return; }
    void loadStrikes(shownId);
  }, [shownId, loadStrikes]);

  const pdf = async () => {
    const node = document.querySelector<HTMLElement>('[data-testid="analysis-result"]');
    const rec = current?.result?.recognised;
    if (!node) return;
    try {
      await printResult(node, printTitle(rec?.symbol ?? null, rec?.timeframe ?? null));
    } catch (e) {
      setProblem(problemFrom(e));
    }
  };

  const analyze = async () => {
    setProblem(null);
    setNotice(null);
    if (!file && !link.trim()) { setProblem({ message: "Add a chart first: choose an image, paste one, or paste a link.", code: "one_source" }); return; }
    let item: AnalysisItem;
    setPhase("adding");
    try {
      item = await chartAnalyzeApi.add(file ? { file, fileName: file.name } : { link: link.trim() }, "image_only");
    } catch (e) {
      setPhase("idle");
      setProblem(problemFrom(e));
      return;
    }
    setFile(null);
    setPreview(null);
    setLink("");
    await showItem(item);
    setPhase("analysing");
    try {
      const done = await chartAnalyzeApi.analyze(item.id);
      setCurrent(done);
      if (done.status === "failed") setProblem({ message: done.error ?? "The analysis failed. Try again.", code: "failed" });
    } catch (e) {
      setProblem(problemFrom(e));
    } finally {
      setPhase("idle");
      void reload();
    }
  };

  const retry = async () => {
    if (!current) return;
    setProblem(null);
    setPhase("analysing");
    try {
      const done = await chartAnalyzeApi.analyze(current.id);
      setCurrent(done);
      if (done.status === "failed") setProblem({ message: done.error ?? "The analysis failed. Try again.", code: "failed" });
    } catch (e) {
      setProblem(problemFrom(e));
    } finally {
      setPhase("idle");
      void reload();
    }
  };

  const open = async (id: number) => {
    setProblem(null);
    try {
      await showItem(await chartAnalyzeApi.get(id));
      window.scrollTo?.(0, 0);
    } catch (e) {
      setProblem(problemFrom(e));
    }
  };

  const exportImage = async () => {
    const result = current?.result;
    if (!result) return;
    if (!imageSrc) {
      setProblem({ message: imageNote ?? IMAGE_GONE, code: "image_gone" });
      return;
    }
    setBusy("export");
    try {
      const img = new Image();
      img.src = imageSrc;
      await img.decode();
      const blob = await renderResultPng({ image: img, width: result.image.width, height: result.image.height, shapes: resultShapes(guardResult(result)),
        label: result.label, disclaimer: result.disclaimer });
      const how = await shareOrDownload(blob, `chart-analysis-${current.id}.png`);
      setNotice(how === "shared" ? "Result image shared." : "Result image downloaded.");
    } catch (e) {
      setProblem(problemFrom(e));
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!current) return;
    setBusy("delete");
    try {
      await chartAnalyzeApi.remove(current.id);
      setConfirmDelete(false);
      setCurrent(null);
      setImageSrc(null);
      setNotice("Deleted: the image and its analysis are gone.");
      void reload();
    } catch (e) {
      setProblem(problemFrom(e));
    } finally {
      setBusy(null);
    }
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    pick(firstImage(e.dataTransfer?.files));
  };

  if (!features.loaded) return <div className="h-40 animate-pulse rounded-panel bg-surface-2" aria-busy="true" />;
  if (!on || off) {
    return (
      <div className={card}>
        <h1 className="text-t20 font-semibold text-fg">Page not found</h1>
        <p className="mt-space-1 text-t14 text-fg-muted">This page is not available for your organisation.</p>
      </div>
    );
  }

  const engineMissing = status?.engine.installed === false;
  const limits = status?.limits;
  const working = phase !== "idle";
  const maxMb = status ? Math.round((status.upload.max_bytes / (1024 * 1024)) * 10) / 10 : null;

  return (
    <div className="mx-auto max-w-[1240px] space-y-space-4" data-testid="chart-analyze-page">
      <PageIntro id="chart-analyze" lines={[
        "Get an educational reading of a chart: the situation and the possibilities, as the method sees them.",
        "Upload or paste a screenshot, or paste a TradingView snapshot link, then press Analyze.",
      ]} />
      <h1 className="text-t20 font-semibold text-fg md:text-t24">Analyze a chart</h1>

      {engineMissing && (
        <div role="status" className="flex items-start gap-space-3 rounded-panel border border-subtle bg-warn-tint p-space-4 text-t14 text-fg">
          <TriangleAlert size={18} aria-hidden className="mt-0.5 shrink-0 text-warn" />
          <p><span className="font-semibold">Analysis engine not installed.</span> The administrator installs it on the server. Until then, charts cannot be analysed here.</p>
        </div>
      )}

      <section className={card} aria-labelledby="ca-add">
        <h2 id="ca-add" className="text-t15 font-semibold text-fg">Add a chart</h2>
        <div onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}
          data-testid="drop-zone"
          className={`mt-space-3 flex flex-col items-center gap-space-3 rounded-control border border-dashed p-space-4 text-center ${dragging ? "border-brand bg-surface-2" : "border-border"}`}>
          {preview ? (
            <img src={preview} alt="The chart you picked" className="max-h-56 w-auto max-w-full rounded-control border border-subtle" />
          ) : (
            <ImagePlus size={28} aria-hidden className="text-fg-muted" />
          )}
          <p className="text-t14 text-fg-muted">
            {file ? file.name : <>PNG or JPEG{maxMb ? `, up to ${maxMb} MB` : ""}. <span className="hidden md:inline">Drop it here, </span>paste a screenshot, or choose a file.</>}
          </p>
          <input ref={fileInput} type="file" accept={ACCEPT} className="sr-only" tabIndex={-1} aria-hidden
            onChange={(e) => { pick(firstImage(e.target.files)); e.target.value = ""; }} data-testid="file-input" />
          <Button className="h-11 md:h-9" icon={<Upload size={16} aria-hidden />} onClick={() => fileInput.current?.click()}>
            {file ? "Choose another image" : "Choose an image"}
          </Button>
        </div>
        <div className="mt-space-3">
          <label htmlFor={linkId} className="mb-space-1 block text-t12 font-medium text-fg-muted">Or paste a chart link</label>
          <div className="flex items-center gap-space-2">
            <Link2 size={16} aria-hidden className="shrink-0 text-fg-muted" />
            <input id={linkId} type="url" inputMode="url" value={link} placeholder="https://www.tradingview.com/x/..." autoComplete="off" spellCheck={false}
              onChange={(e) => { setLink(e.target.value); if (e.target.value) { setFile(null); setPreview(null); } }}
              className="h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9" />
          </div>
          <p className="mt-space-1 text-t12 text-fg-muted">TradingView snapshot links and direct image links. A page that needs a login or a live chart cannot be read.</p>
        </div>

        <fieldset className="mt-space-4">
          <legend className="mb-space-2 text-t12 font-medium text-fg-muted">Mode</legend>
          <div className="grid gap-space-2 sm:grid-cols-2">
            <label className="flex min-h-11 cursor-pointer items-start gap-space-3 rounded-control border border-brand bg-surface-2 p-space-3">
              <input type="radio" name="ca-mode" checked readOnly className="mt-1" />
              <span><span className="block text-t14 font-semibold text-fg">Image only</span>
                <span className="block text-t12 text-fg-muted">Read from the picture alone; prices may be approximate.</span></span>
            </label>
            <label className="flex min-h-11 items-start gap-space-3 rounded-control border border-subtle p-space-3 opacity-60" aria-disabled="true">
              <input type="radio" name="ca-mode" disabled className="mt-1" />
              <span><span className="block text-t14 font-semibold text-fg">Hybrid <span className="ml-space-1 rounded-pill bg-surface-2 px-space-2 py-0.5 text-t12 font-medium text-fg-muted">Coming soon</span></span>
                <span className="block text-t12 text-fg-muted">Rebuilds the chart from real bars before reading it.</span></span>
            </label>
          </div>
        </fieldset>

        <div className="mt-space-4 flex flex-col gap-space-2 sm:flex-row sm:items-center">
          <Button variant="primary" className="h-11 w-full sm:w-auto md:h-9" loading={working} disabled={engineMissing} onClick={analyze}>Analyze</Button>
          {limits && (
            <p className="text-t12 text-fg-muted" data-testid="limits">
              Used today {limits.daily_used} of {limits.daily_limit} · this month {limits.monthly_used} of {limits.monthly_limit}
            </p>
          )}
        </div>
        <p className="mt-space-2 text-t12 text-fg-muted">
          Your chart is sent to the AI provider that runs the analysis (your own key's provider, or your organisation's). It is stored privately
          {status ? ` for ${status.retention_days} days` : ""}, and you can delete it at any time.
        </p>
        <div aria-live="polite" className="mt-space-2 text-t14">
          {phase === "adding" && <p className="text-fg-muted">Checking the image…</p>}
          {phase === "analysing" && <p className="text-fg-muted" data-testid="progress">Reading the chart… this can take up to a minute.</p>}
          {notice && <p className="text-fg">{notice}</p>}
        </div>
        {problem && (
          <div role="alert" className="mt-space-2 flex items-start gap-space-3 rounded-control bg-warn-tint p-space-3 text-t14 text-fg" data-code={problem.code}>
            <TriangleAlert size={18} aria-hidden className="mt-0.5 shrink-0 text-warn" />
            <div><p className="font-semibold">{problem.message}</p>{problem.reason && <p className="text-t12 text-fg-muted">{problem.reason}</p>}</div>
          </div>
        )}
      </section>

      {current && current.result && (
        <ResultViewV2 result={current.result} itemLabel={current.label} itemDisclaimer={current.disclaimer} imageNote={imageNote}
          imageSrc={imageSrc} onAlert={setAlertTarget} onExport={exportImage} onDelete={() => setConfirmDelete(true)} onPdf={() => void pdf()}
          strikes={strikes} onStrikesSymbol={(symbol) => void loadStrikes(current.id, symbol)}
          exporting={busy === "export"} deleting={busy === "delete"} />
      )}
      {current && !current.result && (
        <section className={card} aria-label="This chart">
          <p className="text-t14 text-fg">{current.status === "failed" ? current.error ?? "The analysis failed." : "This chart is stored but not analysed yet."}</p>
          <div className="mt-space-3 flex flex-col gap-space-2 sm:flex-row">
            <Button variant="primary" className="h-11 md:h-9" loading={phase === "analysing"} disabled={engineMissing} onClick={retry}>Analyze</Button>
            <Button className="h-11 md:h-9" onClick={() => setConfirmDelete(true)}>Delete</Button>
          </div>
        </section>
      )}

      <section className={card} aria-labelledby="ca-history">
        <h2 id="ca-history" className="text-t15 font-semibold text-fg">Your analyses</h2>
        {history.length === 0 ? (
          <p className="mt-space-2 text-t14 text-fg-muted">Nothing yet. Your past analyses appear here, only for you.</p>
        ) : (
          <ul className="mt-space-2 divide-y divide-subtle">
            {history.map((h) => (
              <li key={h.id}>
                <button type="button" onClick={() => void open(h.id)}
                  className="flex min-h-12 w-full items-center justify-between gap-space-3 py-space-2 text-left hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                  <span className="min-w-0">
                    <span className="block truncate text-t14 font-medium text-fg">{h.summary?.symbol ?? (h.source === "link" ? h.source_host ?? "Linked chart" : "Uploaded chart")}</span>
                    <span className="block text-t12 text-fg-muted">{when(h.created_at)} · Image only</span>
                  </span>
                  <span className="shrink-0 rounded-pill bg-surface-2 px-space-2 py-0.5 text-t12 text-fg-muted">
                    {h.status === "done" && h.summary ? SETUP_SHORT[h.summary.setup_status]
                      : h.status === "failed" ? "Failed" : h.status === "running" ? "Running" : "Not analysed"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <AlertDialogV2 target={alertTarget} onClose={() => setAlertTarget(null)} onCreated={setNotice} />
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete} title="Delete this analysis?"
        description="The chart image and its analysis are deleted now and cannot be brought back."
        footer={<>
          <Button className="h-11 md:h-9" onClick={() => setConfirmDelete(false)}>Keep it</Button>
          <Button variant="danger" className="h-11 md:h-9" loading={busy === "delete"} onClick={remove}>Delete</Button>
        </>} />
    </div>
  );
}
