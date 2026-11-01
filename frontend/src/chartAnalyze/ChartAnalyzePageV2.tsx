import { ImagePlus, Link2, TriangleAlert, Upload } from "lucide-react";
import { ComingSoon } from "../ui/ComingSoon";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type DragEvent } from "react";
import { useSearchParams } from "react-router";
import { friendlyError } from "../api/errors";
import { Button, Dialog, Skeleton } from "../components/primitives";
import { Details } from "../ui/Details";
import { HelpPopover } from "../ui/HelpPopover";
import { PageIntro } from "../ui/PageIntro";
import { useFeatures } from "../ui/useFeatures";
import AlertDialogV2, { type AlertTarget } from "./AlertDialogV2";
import { CHANGE_LABEL, ChartReadLine, NeedsCard, OPTIONAL_NOTE, PickFields, PRICES_NOTE } from "./ChartReadV2";
import { chartAnalyzeApi, errorCode, isFeatureOff } from "./api";
import { ENGINE_ADMIN_TITLE, ENGINE_USER_LINE, ENGINE_USER_TITLE, engineAudience } from "./engineWords";
import { renderResultPng, shareOrDownload } from "./exportImage";
import { printResult, printTitle } from "./printResult";
import HybridResultV2 from "./HybridResultV2";
import ResultViewV2 from "./ResultViewV2";
import ShareDialogV2 from "./ShareDialogV2";
import SharedAnalysisV2 from "./SharedAnalysisV2";
import { resultShapes } from "./shapes";
import { guardResult } from "./signalLanguage";
import type { StrikesState } from "./StrikesSectionV2";
import type { AnalysisItem, AnalyzeStatus, HybridOverride } from "./types";
import { EMPTY, formatDateTimeIST } from "../ui/format";

type Mode = "image_only" | "hybrid";
/** M59 CR-4: the two modes' names (the server's `mode_labels` wins when it sends them). */
export const MODE_LABELS: Record<Mode, string> = { image_only: "AI only", hybrid: "Advanced analysis (code + AI)" };

/**
 * Analyze a chart (docs/design/CHART_ANALYZE.md): upload a chart image (file, paste, drag and drop) or paste a chart
 * link, choose a mode, press Analyze, read "what the method sees and the conditions". Behind the `chart_analyze` flag
 * (hidden while off). Modes: "AI only" (the picture alone) and, behind `chart_analyze_hybrid` (M59 CR-4), "Advanced
 * analysis (code + AI)": M84 - the symbol and timeframe are read from what is printed on the chart (the user may change
 * them, optional), ATP reads its own stored candles and draws its reading beside his picture (HybridResultV2). While that flag is off the choice is shown disabled with one plain line
 * (the admin also reads the flag and where to switch it). Educational: no buy / sell call and no order button anywhere
 * on this page; the disclaimer closes every result.
 */

export const FLAG = "chart_analyze";
/** Item 8: the drop zone's words, each a sentence of its own (capitalised). */
export const DROP_TEXT_DESKTOP = "Drop it here, paste a screenshot, or choose a file.";
export const DROP_TEXT_PHONE = "Paste a screenshot, or choose a file.";
/** M59 CR-5: the one (?) line beside the link input - how to take a TradingView snapshot; a pasted screenshot works too. */
export const SNAPSHOT_HELP = "On TradingView press Alt+S (Option+S on a Mac): it copies the chart's snapshot link - paste it here. A screenshot image pasted on this page works too.";
/** M59 CR-3: the engine-missing words live in ./engineWords (shared with the Copilot's chart attach). */
export { ENGINE_ADMIN_TITLE, ENGINE_USER_LINE, ENGINE_USER_TITLE } from "./engineWords";
/** The "Option strikes · from your settings" block has its own flag on top (owner's tenant first). */
export const STRIKES_FLAG = "chart_analyze_strikes";
/** "Share with my organisation" (PR-A4): its own flag on top; a link opens `?shared=<link>` on this page. */
export const SHARE_FLAG = "chart_analyze_share";
const ACCEPT = "image/png,image/jpeg";
const card = "rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5";

export const IMAGE_GONE = "The chart image is no longer stored (it is kept for a limited time), so the result image cannot be made.";
const SETUP_SHORT = { formed: "Formed", forming: "Forming", none: "No setup" } as const;

/** History filters (PR-A4): the user's own analyses by what the method saw. */
export type HistoryFilter = "all" | "formed" | "forming" | "none" | "open";
export const HISTORY_FILTERS: { id: HistoryFilter; label: string }[] = [
  { id: "all", label: "All" }, { id: "formed", label: "Formed" }, { id: "forming", label: "Forming" }, { id: "none", label: "No setup" },
  { id: "open", label: "Not analysed" },
];

export function filterHistory(items: AnalysisItem[], filter: HistoryFilter): AnalysisItem[] {
  if (filter === "all") return items;
  if (filter === "open") return items.filter((h) => h.status !== "done" || !h.summary);
  return items.filter((h) => h.status === "done" && h.summary?.setup_status === filter);
}

type Phase = "idle" | "adding" | "analysing";
interface Problem { message: string; reason?: string; code: string }

function problemFrom(e: unknown): Problem {
  const code = errorCode(e);
  let reason: string | undefined;
  try {
    const body = JSON.parse((e as { body?: string }).body ?? "") as { detail?: { reason?: string } };
    reason = body.detail?.reason;
  } catch { /* intentional: not JSON */ }
  return { message: friendlyError(e), reason, code };
}

function when(iso: string | null): string {
  if (!iso) return "";
  const text = formatDateTimeIST(iso);                      // REV-1 M3: IST, never the device's zone
  return text === EMPTY ? "" : text;
}

export function firstImage(files: FileList | File[] | null | undefined): File | null {
  for (const f of Array.from(files ?? [])) if (f.type === "image/png" || f.type === "image/jpeg") return f;
  return null;
}

/** A1: the analysis engine is not installed on this server. Users see one plain line and no upload or link inputs; the
 * admin (the server sends `reason` / `owner_steps` to SUPER_ADMIN only) also sees why and what the owner does. */
export function EngineUnavailable({ engine }: { engine: AnalyzeStatus["engine"] }) {
  const admin = engineAudience(engine) === "admin";
  return (
    <div className="mx-auto max-w-[1240px] space-y-space-4" data-testid="chart-analyze-unavailable" data-audience={admin ? "admin" : "user"}>
      <h1 className="text-t20 font-semibold text-fg md:text-t24">Analyze a chart</h1>
      <section className={card} role="status">
        <div className="flex items-start gap-space-3">
          <TriangleAlert size={20} aria-hidden className="mt-0.5 shrink-0 text-warn" />
          <div>
            <p className="text-t15 font-semibold text-fg">{admin ? ENGINE_ADMIN_TITLE : ENGINE_USER_TITLE}</p>
            <p className="mt-space-1 text-t14 text-fg-muted">{ENGINE_USER_LINE}</p>
          </div>
        </div>
        {admin ? (
          <div className="mt-space-4 rounded-control border border-subtle bg-surface-2 p-space-3" data-testid="engine-admin-detail">
            <p className="text-t14 font-semibold text-fg">Owner steps</p>
            {engine.reason ? <p className="mt-space-1 text-t14 text-fg-muted">Why: {engine.reason}.</p> : null}
            {engine.owner_steps ? <p className="mt-space-1 break-words text-t14 text-fg-muted">{engine.owner_steps}</p> : null}
            <p className="mt-space-1 text-t13 text-fg-muted">Users see only: “{ENGINE_USER_TITLE}”.</p>
          </div>
        ) : null}
      </section>
    </div>
  );
}

export default function ChartAnalyzePageV2() {
  const features = useFeatures();
  const on = features.on(FLAG);
  const strikesOn = on && features.on(STRIKES_FLAG);
  const shareOn = on && features.on(SHARE_FLAG);
  const [params, setParams] = useSearchParams();
  const sharedToken = params.get("shared");
  const [sharing, setSharing] = useState(false);
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [status, setStatus] = useState<AnalyzeStatus | null>(null);
  const [off, setOff] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [link, setLink] = useState("");
  const [mode, setMode] = useState<Mode>("image_only");
  const [symbol, setSymbol] = useState("");
  const [timeframe, setTimeframe] = useState("");
  const [readingSrc, setReadingSrc] = useState<string | null>(null);
  const readingUrlRef = useRef<string | null>(null);
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
  // A1: with the engine missing the server answers the flag as off for users (the nav hides the page). A direct visit
  // asks the status once, so the page can say the feature is not available instead of "Page not found".
  const [probed, setProbed] = useState(false);
  useEffect(() => {
    if (!features.loaded || on) return;
    let live = true;
    chartAnalyzeApi.status().then((s) => { if (live) setStatus(s); }, () => undefined).finally(() => { if (live) setProbed(true); });
    return () => { live = false; };
  }, [features.loaded, on]);
  useEffect(() => () => { if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current); }, []);
  useEffect(() => () => { if (readingUrlRef.current) URL.revokeObjectURL(readingUrlRef.current); }, []);
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

  // M59 CR-4: a done hybrid item's annotated chart of ATP's reading (a Blob: an <img> cannot send the token).
  const loadReading = useCallback(async (item: AnalysisItem) => {
    if (readingUrlRef.current) URL.revokeObjectURL(readingUrlRef.current);
    readingUrlRef.current = null;
    setReadingSrc(null);
    if (item.mode !== "hybrid" || !item.hybrid) return;
    try {
      readingUrlRef.current = URL.createObjectURL(await chartAnalyzeApi.readingPng(item.id));
      setReadingSrc(readingUrlRef.current);
    } catch {
      setReadingSrc(null);
    }
  }, []);

  const showItem = useCallback(async (item: AnalysisItem) => {
    setCurrent(item);
    setImageNote(null);
    void loadReading(item);
    try {
      const blob = await chartAnalyzeApi.image(item.id);
      if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
      imageUrlRef.current = URL.createObjectURL(blob);
      setImageSrc(imageUrlRef.current);
    } catch (e) {
      setImageSrc(null);
      setImageNote(errorCode(e) === "image_gone" ? IMAGE_GONE : "The chart image could not be loaded. Try again later.");
    }
  }, [loadReading]);

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
    const hybridMode = mode === "hybrid" && hybridOn;
    let item: AnalysisItem;
    setPhase("adding");
    try {
      item = await chartAnalyzeApi.add(file ? { file, fileName: file.name } : { link: link.trim() }, hybridMode ? "hybrid" : "image_only",
        hybridMode ? { symbol: symbol.trim().toUpperCase(), timeframe } : undefined);      // M84: both optional
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
      void loadReading(done);
      if (done.status === "failed") setProblem({ message: done.error ?? "The analysis failed. Try again.", code: "failed" });
    } catch (e) {
      setProblem(problemFrom(e));
    } finally {
      setPhase("idle");
      void reload();
    }
  };

  const retry = async (change?: Partial<HybridOverride>) => {
    if (!current) return;
    setProblem(null);
    setPhase("analysing");
    try {
      const done = await chartAnalyzeApi.analyze(current.id, change);
      // M84: a changed symbol / timeframe on a finished analysis is a new analysis of the same picture, shown in place
      if (done.id !== current.id) await showItem(done);
      else setCurrent(done);
      void loadReading(done);
      if (done.status === "failed") setProblem({ message: done.error ?? "The analysis failed. Try again.", code: "failed" });
    } catch (e) {
      setProblem(problemFrom(e));
    } finally {
      setPhase("idle");
      void reload();
    }
  };

  const downloadReading = async () => {
    if (!current || current.mode !== "hybrid" || !current.hybrid) return;
    setBusy("export");
    try {
      const blob = await chartAnalyzeApi.readingPng(current.id);
      const r = current.hybrid.reading;
      const how = await shareOrDownload(blob, `atp-reading-${r.symbol.replace(/[^A-Za-z0-9]/g, "")}-${r.timeframe}-${current.id}.png`);
      setNotice(how === "shared" ? "Image shared." : "Image downloaded.");
    } catch (e) {
      setProblem(problemFrom(e));
    } finally {
      setBusy(null);
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

  const shareChanged = (item: AnalysisItem) => {
    setCurrent((c) => (c && c.id === item.id ? { ...c, share: item.share ?? null } : c));
    setHistory((h) => h.map((x) => (x.id === item.id ? { ...x, share: item.share ?? null } : x)));
  };

  const shown = useMemo(() => filterHistory(history, filter), [history, filter]);

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    pick(firstImage(e.dataTransfer?.files));
  };

  const engineMissing = status?.engine.installed === false;
  const hybridOn = Boolean(status?.modes.hybrid);
  const labels = status?.mode_labels ?? MODE_LABELS;
  const timeframes = status?.hybrid?.timeframes ?? [];
  if (!features.loaded || (!on && !probed)) return <Skeleton className="h-40" panel label="Loading" />;
  // A1: the engine (private package) is not installed - a real "not available" state, never "Coming soon".
  if (engineMissing && !off) return <EngineUnavailable engine={status.engine} />;
  if (!on) return <ComingSoon flag={FLAG} />;                 // the flag is off: the shell's standard page (SELF_GATED)
  if (off) {
    return (
      <div className={card}>
        <h1 className="text-t20 font-semibold text-fg">Page not found</h1>
        <p className="mt-space-1 text-t14 text-fg-muted">This page is not available for your organisation.</p>
      </div>
    );
  }

  if (sharedToken) {
    return <SharedAnalysisV2 token={sharedToken} onBack={() => { params.delete("shared"); setParams(params, { replace: true }); }} />;
  }

  const limits = status?.limits;
  const working = phase !== "idle";
  const maxMb = status ? Math.round((status.upload.max_bytes / (1024 * 1024)) * 10) / 10 : null;

  return (
    <div className="mx-auto max-w-[1240px] space-y-space-4" data-testid="chart-analyze-page">
      <PageIntro id="chart-analyze" lines={["Upload or paste a chart screenshot, or a TradingView snapshot link, then press Analyze for an educational reading."]} />
      <h1 className="text-t20 font-semibold text-fg md:text-t24">Analyze a chart</h1>

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
            {file ? file.name : <>PNG or JPEG{maxMb ? `, up to ${maxMb} MB` : ""}. <span className="hidden md:inline">{DROP_TEXT_DESKTOP}</span><span className="md:hidden">{DROP_TEXT_PHONE}</span></>}
          </p>
          <input ref={fileInput} type="file" accept={ACCEPT} className="sr-only" tabIndex={-1} aria-hidden
            onChange={(e) => { pick(firstImage(e.target.files)); e.target.value = ""; }} data-testid="file-input" />
          <Button className="h-11 md:h-9" icon={<Upload size={16} aria-hidden />} onClick={() => fileInput.current?.click()}>
            {file ? "Choose another image" : "Choose an image"}
          </Button>
        </div>
        <div className="mt-space-3">
          <div className="mb-space-1 flex items-center gap-space-1">
            <label htmlFor={linkId} className="block text-t12 font-medium text-fg-muted">Or paste a chart link</label>
            <HelpPopover title="How to take a TradingView snapshot"><p data-testid="snapshot-help">{SNAPSHOT_HELP}</p></HelpPopover>
          </div>
          <div className="flex items-center gap-space-2">
            <Link2 size={16} aria-hidden className="shrink-0 text-fg-muted" />
            <input id={linkId} type="url" inputMode="url" value={link} placeholder="https://www.tradingview.com/x/..." autoComplete="off" spellCheck={false}
              onChange={(e) => { setLink(e.target.value); if (e.target.value) { setFile(null); setPreview(null); } }}
              className="h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9" />
          </div>
        </div>

        <fieldset className="mt-space-4" data-testid="mode-picker">
          <legend className="mb-space-2 text-t12 font-medium text-fg-muted">Mode</legend>
          <div className="grid gap-space-2 sm:grid-cols-2">
            <label className={`flex min-h-11 cursor-pointer items-start gap-space-3 rounded-control border p-space-3 ${mode === "image_only" || !hybridOn ? "border-brand bg-surface-2" : "border-subtle"}`}>
              <input type="radio" name="ca-mode" value="image_only" checked={mode === "image_only" || !hybridOn} onChange={() => setMode("image_only")} className="mt-1" />
              <span><span className="block text-t14 font-semibold text-fg">{labels.image_only}</span>
                <span className="block text-t12 text-fg-muted">Read from the picture alone; prices may be approximate.</span></span>
            </label>
            <label className={`flex min-h-11 items-start gap-space-3 rounded-control border p-space-3 ${hybridOn ? `cursor-pointer ${mode === "hybrid" ? "border-brand bg-surface-2" : "border-subtle"}` : "border-subtle opacity-70"}`}
              aria-disabled={!hybridOn || undefined} data-testid="mode-hybrid">
              <input type="radio" name="ca-mode" value="hybrid" disabled={!hybridOn} checked={hybridOn && mode === "hybrid"} onChange={() => setMode("hybrid")} className="mt-1" />
              <span className="min-w-0"><span className="block text-t14 font-semibold text-fg">{labels.hybrid}</span>
                <span className="block text-t12 text-fg-muted">Reads ATP&apos;s stored candles for this chart.</span>
                {!hybridOn && status && (
                  <span className="mt-space-1 block text-t12 text-fg" data-testid="hybrid-off">{status.hybrid?.message ?? status.hybrid_note}</span>
                )}
                {!hybridOn && status?.hybrid?.admin && (
                  <span className="mt-space-1 block break-words text-t12 text-fg-muted" data-testid="hybrid-admin">
                    For admins: turn on &quot;{status.hybrid.admin.flag}&quot; in {status.hybrid.admin.where} (needs {status.hybrid.admin.needs.join(", ")}).
                  </span>
                )}
              </span>
            </label>
          </div>
        </fieldset>
        {hybridOn && mode === "hybrid" && (
          // M84: the symbol and timeframe are read from the chart; these are OPTIONAL overrides, collapsed.
          <details className="group mt-space-3" data-testid="hybrid-override" open={Boolean(symbol || timeframe) || undefined}>
            <summary className="inline-flex min-h-11 cursor-pointer list-none items-center gap-space-1 rounded-control text-t13 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-0 [&::-webkit-details-marker]:hidden">
              {CHANGE_LABEL}<span className="text-fg-muted"> · optional</span>
            </summary>
            <p className="mt-space-1 text-t12 text-fg-muted">{OPTIONAL_NOTE}</p>
            <div className="mt-space-2"><PickFields symbol={symbol} timeframe={timeframe} onSymbol={setSymbol} onTimeframe={setTimeframe} timeframes={timeframes} /></div>
          </details>
        )}

        <div className="mt-space-4 flex flex-col gap-space-2 sm:flex-row sm:items-center">
          <Button variant="primary" className="h-11 w-full sm:w-auto md:h-9" loading={working} disabled={engineMissing} onClick={analyze}>Analyze</Button>
          {limits && (
            <p className="text-t12 text-fg-muted" data-testid="limits">
              Used today {limits.daily_used} of {limits.daily_limit} · this month {limits.monthly_used} of {limits.monthly_limit}
            </p>
          )}
        </div>
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
        <Details id="chart-analyze.add">
          <p>TradingView snapshot links and direct image links. A page that needs a login or a live chart cannot be read.</p>
          <p>{labels.hybrid}: ATP reads the symbol and timeframe printed on your chart (you can change them); it reads its own stored candles and draws its reading beside your chart. {PRICES_NOTE}</p>
          <p>
            Your chart is sent to the AI provider that runs the analysis (your own key's provider, or your organisation's). It is stored privately
            {status ? ` for ${status.retention_days} days` : ""}, and you can delete it at any time.
          </p>
        </Details>
      </section>

      {current && current.mode === "hybrid" && current.hybrid && (
        <HybridResultV2 result={current.hybrid} userImageSrc={imageSrc} readingSrc={readingSrc} imageNote={imageNote}
          readLine={<ChartReadLine key={current.id} result={current.hybrid} timeframes={timeframes} running={phase === "analysing"} onRun={(c) => void retry(c)} />}
          onDownload={() => void downloadReading()} onDelete={() => setConfirmDelete(true)} downloading={busy === "export"} deleting={busy === "delete"} />
      )}
      {current && current.result && (
        <ResultViewV2 result={current.result} itemLabel={current.label} itemDisclaimer={current.disclaimer} imageNote={imageNote}
          imageSrc={imageSrc} onAlert={setAlertTarget} onExport={exportImage} onDelete={() => setConfirmDelete(true)} onPdf={() => void pdf()}
          strikes={strikes} onStrikesSymbol={(symbol) => void loadStrikes(current.id, symbol)}
          onShare={shareOn ? () => setSharing(true) : undefined} shareLive={Boolean(current.share)}
          exporting={busy === "export"} deleting={busy === "delete"} />
      )}
      {current && current.mode === "hybrid" && !current.hybrid && current.status === "ready" && (current.needs?.length ?? 0) > 0 && (
        <NeedsCard key={`${current.id}-${current.needs!.map((n) => n.field).join()}`} needs={current.needs!} read={current.read_from_chart}
          timeframes={timeframes} running={phase === "analysing"} onRun={(c) => void retry(c)} />
      )}
      {current && !current.result && !current.hybrid && !(current.needs?.length) && (
        <section className={card} aria-label="This chart">
          <p className="text-t14 text-fg">{current.status === "failed" ? current.error ?? "The analysis failed." : "This chart is stored but not analysed yet."}</p>
          <div className="mt-space-3 flex flex-col gap-space-2 sm:flex-row">
            <Button variant="primary" className="h-11 md:h-9" loading={phase === "analysing"} disabled={engineMissing} onClick={() => void retry()}>Analyze</Button>
            <Button className="h-11 md:h-9" onClick={() => setConfirmDelete(true)}>Delete</Button>
          </div>
        </section>
      )}

      <section className={card} aria-labelledby="ca-history">
        <h2 id="ca-history" className="text-t15 font-semibold text-fg">Your analyses</h2>
        {history.length === 0 ? (
          <p className="mt-space-2 text-t14 text-fg-muted">Your past analyses appear here, only for you.</p>
        ) : (
          <>
            <p className="mt-space-1 text-t12 text-fg-muted">Only you see these{status ? `. Each is kept for ${status.retention_days} days` : ""}.</p>
            <div role="group" aria-label="Show" className="-mx-space-1 mt-space-3 flex gap-space-2 overflow-x-auto px-space-1 pb-space-1" data-testid="history-filters">
              {HISTORY_FILTERS.map((f) => {
                const count = filterHistory(history, f.id).length;
                return (
                  <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}
                    className={`inline-flex h-11 shrink-0 items-center gap-space-1 rounded-pill border px-space-3 text-t12 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-8 ${
                      filter === f.id ? "border-brand bg-surface-2 text-fg" : "border-subtle text-fg-muted hover:text-fg"}`}>
                    {f.label}<span className="font-tabular text-fg-muted">{count}</span>
                  </button>
                );
              })}
            </div>
            {shown.length === 0 ? (
              <p className="mt-space-2 text-t14 text-fg-muted">None of your analyses match this filter.</p>
            ) : (
              <ul className="mt-space-2 divide-y divide-subtle">
                {shown.map((h) => (
                  <li key={h.id}>
                    <button type="button" onClick={() => void open(h.id)} aria-current={current?.id === h.id || undefined}
                      className="flex min-h-12 w-full items-center justify-between gap-space-3 py-space-2 text-left hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                      <span className="min-w-0">
                        <span className="block truncate text-t14 font-medium text-fg">{h.summary?.symbol ?? h.pick?.symbol ?? (h.source === "link" ? h.source_host ?? "Linked chart" : "Uploaded chart")}</span>
                        <span className="block text-t12 text-fg-muted">{when(h.created_at)} · {h.mode === "hybrid" ? labels.hybrid.replace(/ \(.*\)$/, "") : labels.image_only}{h.share ? " · Shared" : ""}</span>
                      </span>
                      <span className="shrink-0 rounded-pill bg-surface-2 px-space-2 py-0.5 text-t12 text-fg-muted">
                        {h.status === "done" && h.summary ? (h.summary.setup_status ? SETUP_SHORT[h.summary.setup_status] : "Read")
                          : h.status === "failed" ? "Failed" : h.status === "running" ? "Running" : "Not analysed"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      <AlertDialogV2 target={alertTarget} onClose={() => setAlertTarget(null)} onCreated={setNotice} />
      {sharing && shareOn && current && <ShareDialogV2 item={current} onClose={() => setSharing(false)} onChanged={shareChanged} />}
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete} title="Delete this analysis?"
        description={`The chart image and its analysis are deleted now and cannot be brought back.${current?.share ? " Its share link stops working." : ""}`}
        footer={<>
          <Button className="h-11 md:h-9" onClick={() => setConfirmDelete(false)}>Keep it</Button>
          <Button variant="danger" className="h-11 md:h-9" loading={busy === "delete"} onClick={remove}>Delete</Button>
        </>} />
    </div>
  );
}
