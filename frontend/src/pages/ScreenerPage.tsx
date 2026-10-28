/**
 * U5 D1: the Screener - a precision instrument, not a form. Three columns on a desktop (Universe 280 / the funnel /
 * Results 380); on a tablet the canvas leads and the universe folds above it; on a phone everything stacks and a sticky
 * bar keeps the live count and Run in reach. The funnel carries the page's character; everything else stays quiet.
 *
 * Research only: a scan lists symbols that meet the trader's own conditions - never a recommendation, never an order.
 */
import { BellRing, ChevronDown, Code2, History, Layers, LayoutGrid, Lightbulb, MoreHorizontal, Play, Save } from "lucide-react";
import { DataTierBadge } from "../components/DataTierBadge";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router";
import { ApiError } from "../api/errors";
import { Button, cx, Dialog, Sheet } from "../components/primitives";
import { Combobox } from "../ui/Combobox";
import { useMediaQuery } from "../utils/useMediaQuery";
import { useToast } from "../components/Toast";
import type { PageProps } from "../routes";
import { screenerApi, type IdeaResponse, type PreviewResponse, type RunResponse, type ServerVersion } from "../screener/api";
import { FreshnessPill } from "../screener/FreshnessPill";
import { PlainTimeframes } from "../screener/TimeframeChip";
import { FunnelCanvas, STARTERS } from "../screener/FunnelCanvas";
import { blockProblems, parseSymbols, runBlocker, runStamp, sameRun, screenText, stageAt, TIMEFRAMES, timeframeWords, trail, whyChips, type Registry, type RunStamp, type Stage } from "../screener/model";
import { EvidencePanel } from "../screener/EvidencePanel";
import { ResultViews } from "../screener/ResultViews";
import { EtfPanel } from "../screener/EtfPanel";
import { ScreenGallery } from "../screener/ScreenGallery";
import { isLegends, type LegendsLibrary } from "../screener/legends";
import { stagesFromServer, type ServerStage } from "../screener/importStages";
import { isMirrors, isWatchLists, mirrorsByScreen, type MirrorsResponse, type WatchListsResponse } from "../screener/library";
import { LibraryTools } from "../screener/LibraryTools";
import { WatchlistPicker } from "../screener/WatchlistPicker";
import { isTaxonomy, type Taxonomy, type TaxScreen } from "../screener/taxonomy";
import "../screener/screener.css";
import { useFeatures } from "../ui/useFeatures";
import PageDishV2 from "../growth/PageDishV2";
import { ComingSoon } from "../ui/ComingSoon";
import { ErrorState } from "../ui/ErrorState";
import { Details } from "../ui/Details";
import { PageFootnote } from "../ui/PageFootnote";
import { COMING_SOON_MESSAGE } from "../api/errors";
import { ideasApi } from "../ideas/api";
import { ideaScanFromSearch, scanName } from "../ideas/model";
import { builderOf, clearStore, readStore, readsOf, snapshot, syncDeviceHistory, writeStore } from "../screener/versions";
import { UniversePicker } from "../screener/UniversePicker";
import { CUSTOM, DEFAULT_CHOICE, DEFAULT_KEY, FALLBACK_PLAN, NIFTY50_FALLBACK, NIFTY50_FALLBACK_NOTE, RUN_MAX, effectiveUniverse, heldBack, isUniverses, listLabel,
  runInBatches, serverHasDefault, universesApi, type UniverseSymbols, type UniversesResponse } from "../screener/universe";
import { isWatchlists, watchlistsApi, type Watchlist } from "../screener/watchlists";

// Handover 11c-1 (flag `strategy_lab_pine`): the Pine Script panel loads only when it is opened.
const PinePanel = lazy(() => import("../lab/pine/PinePanel"));
// Handover 11j (flag `screener_copilot`, behind `ui_v2`): a scan from a plain idea, part by part, as editable blocks.
const IdeaPanel = lazy(() => import("../screener/IdeaPanel"));
// Handover 11j steps 6-7 (same flags): the saved scan's versions (LAB-18: kept on the server) and "Tell me when it matches".
const ScanVersionsPanel = lazy(() => import("../screener/ScanVersionsPanel"));
const ScanAlertPanel = lazy(() => import("../screener/ScanAlertPanel"));

const VALIDATE_DELAY_MS = 350;
/** D2 live counts: a preview run (not stored) once the trader pauses editing, on bars the server caches per symbol. */
const PREVIEW_DELAY_MS = 900;

/** SCR-FIX D9: below Tailwind's `sm` (where the sticky Run bar shows) the header's tools fold into one "More" sheet. */
const NARROW_QUERY = "(max-width: 639px)";
/** One of the header's tools (Browse screens, From an idea, Advanced, ...): a button inline, a row in the "More" sheet. */
interface HeaderTool { id: string; onClick: () => void; content: ReactNode; disabled?: boolean; ghost?: boolean; keepOpen?: boolean; expanded?: boolean }

/** UX1: the screener's routes answer 404 while `screener_v2` is off - the page then shows "Coming soon". */
const SCREENER_OFF = "screener-off";

function errorText(e: unknown): string {
  if (e instanceof ApiError && e.status === 404) return COMING_SOON_MESSAGE;
  if (e instanceof ApiError && e.status === 409) return "Connect a broker on the Broker page - the scan reads its bars from there.";
  return e instanceof Error ? e.message : String(e);
}

export default function ScreenerPage(_props: PageProps) {
  const toast = useToast();
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState("Untitled scan");
  const [scanTf, setScanTf] = useState("1d");
  const [symbolsText, setSymbolsText] = useState("");
  const [stages, setStages] = useState<Stage[]>([]);
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [otherProblem, setOtherProblem] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<RunResponse | null>(null);
  const [evidence, setEvidence] = useState<{ id: number; symbol: string } | null>(null);   // U8 EV1: "Show on chart"
  const [ran, setRan] = useState<RunStamp | null>(null);         // what the shown run was made of
  const [validating, setValidating] = useState(false);
  const [live, setLive] = useState(true);
  const [preview, setPreview] = useState<{ res: PreviewResponse; stamp: RunStamp } | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);          // one preview at a time; the latest scan goes next
  const [ranAt, setRanAt] = useState<Date | null>(null);
  const [runKey, setRunKey] = useState(0);
  const [savedId, setSavedId] = useState<number | undefined>(undefined);
  const validateSeq = useRef(0);
  // U9 TX1 (flag `screener_gallery`): the taxonomy loads once; while the flag is off the call fails and the page shows
  // no gallery - it is then exactly as before.
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null);
  const [galleryOpen, setGalleryOpen] = useState(false);
  // U7 LG1 (flag `screener_legends`): the Legends cards load with the taxonomy; while the flag is off the call fails,
  // the taxonomy hides the Legends screens and nothing changes.
  const [legends, setLegends] = useState<LegendsLibrary | null>(null);
  // U2 (flag `screener_builder_u2`): formula blocks, and Legends / breakout screens open in the builder.
  // UI2-0: this page's flags come from the app's single /system/features read.
  const features = useFeatures();
  const builderU2 = features.on("screener_builder_u2");
  // U9 TX2 (flag `screener_tx2`): mirrors and watch lists; while the flag is off the calls fail and nothing shows.
  const [mirrors, setMirrors] = useState<MirrorsResponse | null>(null);
  const [watchLists, setWatchLists] = useState<WatchListsResponse | null>(null);
  const [toolsOpen, setToolsOpen] = useState(false);
  const mirrorMap = useMemo(() => mirrorsByScreen(mirrors), [mirrors]);
  // S10 J (flag `screener_etf`): the ETF screen in a dialog.
  const etfOn = features.on("screener_etf");
  const [etfOpen, setEtfOpen] = useState(false);
  // AMW-2 (flag `watchlists`): "My watchlists" in the universe column.
  const watchlistsOn = features.on("watchlists");
  // Handover 11c-1 (flag `strategy_lab_pine`): paste a Pine script, see what translates, run its condition as a screen.
  const pineOn = features.on("strategy_lab_pine");
  const [pineOpen, setPineOpen] = useState(false);
  // Handover 11j (flag `screener_copilot`, behind `ui_v2`): "From an idea".
  const ideaOn = features.on("screener_copilot") && features.on("ui_v2");
  // MO4 slice 4: "Today's setups from your scans", the page's ready-made dish (ui_v2 + growth_v2).
  const dishOn = features.on("growth_v2") && features.on("ui_v2");
  const [ideaOpen, setIdeaOpen] = useState(false);
  const [alertOpen, setAlertOpen] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [ideaReads, setIdeaReads] = useState<Record<string, string>>({});     // a scan from an idea: block -> its plain words
  // LAB-18 (AMW-7): the saved scan's versions on the server; the device history is uploaded once first (`synced`).
  const [scanVersions, setScanVersions] = useState<ServerVersion[]>([]);
  const [versionsMax, setVersionsMax] = useState(50);
  const [synced, setSynced] = useState(false);
  const [openingVersion, setOpeningVersion] = useState<number | null>(null);
  // Owner order 23 Oct (ui_v2): the universe is a list picked from the system lists (NIFTY 50 on open), a watchlist or
  // the trader's own symbols - never empty; a list longer than one run goes in batches paced by the broker's budget.
  const universeV2 = features.on("ui_v2");
  // Day-one fix 4 (ui_v2): Pine Script, Library tools, the formula block and "Live counts while editing" sit behind
  // "Advanced"; the trader's page shows his list, the timeframe in words and the ready scans.
  const [advancedOpen, setAdvanced] = useState(false);
  const advanced = !universeV2 || advancedOpen;
  const [universes, setUniverses] = useState<UniversesResponse | null>(null);
  const [universesDone, setUniversesDone] = useState(false);
  const [choice, setChoice] = useState(DEFAULT_CHOICE);
  const [lists, setLists] = useState<Record<string, UniverseSymbols | "failed">>({});
  const [myLists, setMyLists] = useState<Watchlist[] | null>(null);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const stopRef = useRef(false);
  const [pendingRun, setPendingRun] = useState(false);         // one tap: run as soon as the opened scan is checked
  const [checkedText, setCheckedText] = useState("");          // the text the last finished check was for

  useEffect(() => {
    screenerApi.registry().then((r) => setRegistry(r.entries))
      .catch((e: unknown) => setLoadError(e instanceof ApiError && e.status === 404 ? SCREENER_OFF : errorText(e)));
    screenerApi.taxonomy().then((t) => setTaxonomy(isTaxonomy(t) ? t : null)).catch(() => setTaxonomy(null));
    screenerApi.legends().then((l) => setLegends(isLegends(l) ? l : null)).catch(() => setLegends(null));
    screenerApi.libraryMirrors().then((m) => setMirrors(isMirrors(m) ? m : null)).catch(() => setMirrors(null));
    screenerApi.watchLists().then((w) => setWatchLists(isWatchLists(w) ? w : null)).catch(() => setWatchLists(null));
  }, []);

  useEffect(() => {
    if (!universeV2) return undefined;
    let alive = true;
    universesApi.list().then((u) => { if (alive) setUniverses(isUniverses(u) ? u : null); })
      .catch(() => undefined).finally(() => { if (alive) setUniversesDone(true); });
    return () => { alive = false; };
  }, [universeV2]);
  useEffect(() => {
    if (!universeV2 || !watchlistsOn) return undefined;
    let alive = true;
    watchlistsApi.list().then((l) => { if (alive) setMyLists(isWatchlists(l) ? l : null); }).catch(() => undefined);
    return () => { alive = false; };
  }, [universeV2, watchlistsOn]);
  const builtinDefault = universesDone && !serverHasDefault(universes);
  const chosenKey = choice.startsWith("sys:") ? choice.slice(4) : null;
  const needsList = !!chosenKey && !(chosenKey === DEFAULT_KEY && builtinDefault);
  useEffect(() => {
    if (!universeV2 || !universesDone || !needsList || !chosenKey || lists[chosenKey]) return undefined;
    let alive = true;
    universesApi.symbols(chosenKey).then((r) => { if (alive) setLists((all) => ({ ...all, [chosenKey]: r })); })
      .catch(() => { if (alive) setLists((all) => ({ ...all, [chosenKey]: "failed" })); });
    return () => { alive = false; };
  }, [universeV2, universesDone, needsList, chosenKey, lists]);

  // LAB-18 first sync: what this device kept before the server kept versions goes up once per scan, then the device
  // copy is cleared (what the server did not take yet - it was off or unreachable - stays for the next load).
  useEffect(() => {
    if (!ideaOn) return undefined;
    let alive = true;
    const store = readStore();
    if (!Object.keys(store).length) { setSynced(true); return undefined; }
    syncDeviceHistory(store, screenerApi).then(({ left }) => {
      if (Object.keys(left).length) writeStore(left);
      else clearStore();
    }).catch(() => undefined).finally(() => { if (alive) setSynced(true); });
    return () => { alive = false; };
  }, [ideaOn]);

  const loadVersions = useCallback((id: number | undefined) => {
    if (!ideaOn || id == null) { setScanVersions([]); return; }
    screenerApi.versions(id).then((r) => { setScanVersions(r.versions); setVersionsMax(r.max); })
      .catch(() => setScanVersions([]));          // no history shown is better than an error on the page
  }, [ideaOn]);
  useEffect(() => { if (synced) loadVersions(savedId); }, [synced, savedId, loadVersions]);

  // A gallery card opens in the builder when this page knows how to build its source (the U5 starters today; the
  // breakout family joins with U6 BK1).
  const canOpen = useCallback((s: TaxScreen) => !!registry && ((s.source === "starter" && STARTERS.some((t) => t.id === s.id))
    || (builderU2 && (s.source === "legends" || s.source === "breakouts"))), [registry, builderU2]);
  // Day-one fix 4: the page opens on something to do - three ready scans from the library, one tap each.
  const readyScans = useMemo(() => (taxonomy?.screens ?? []).filter((s) => s.source === "starter" && canOpen(s)).slice(0, 3), [taxonomy, canOpen]);
  const openScreen = (s: TaxScreen, runNow = false) => {
    const starter = STARTERS.find((t) => t.id === s.id);
    if (!registry || !canOpen(s)) return;
    if (stages.length > 0 && !window.confirm(`Replace the current conditions with "${s.name}"?`)) return;
    if (starter) {
      setStages(starter.build(registry));
      setName(s.name);
      setGalleryOpen(false);
      if (runNow) setPendingRun(true);
      return;
    }
    // U2: a Legends or breakout screen - its conjuncts as blocks (a formula block where no other form writes the same text)
    screenerApi.stages({ library_id: s.id }).then((res) => {
      setStages(stagesFromServer(registry, res.stages));
      setScanTf(res.base_tf);
      setName(s.name);
      setGalleryOpen(false);
      if (runNow) setPendingRun(true);
    }).catch((e: unknown) => setOtherProblem(errorText(e)));
  };

  const openIdea = (server: ServerStage[], baseTf: string, parts: IdeaResponse["parts"] = []) => {
    if (!registry) return;
    if (stages.length > 0 && !window.confirm("Replace the current conditions with the scan from your idea?")) return;
    setIdeaReads(readsOf(parts));
    setStages(stagesFromServer(registry, server));
    setScanTf(baseTf);
    setName("Scan from my idea");
    setSavedId(undefined);                             // a scan from an idea is a new scan (its own versions and alerts)
    setIdeaOpen(false);
  };

  // Idea library slice 1 (flag idea_library): "Try as a scan" opens `/screener?idea=T1&scan=up` - the idea's screen, its
  // settings at their starting values, as builder blocks the trader sees and changes like any other. The server
  // validates and gates it as usual (`/stages`); the address loses the query once it is handled.
  const location = useLocation();
  const navigate = useNavigate();
  const ideaAsk = useMemo(() => ideaScanFromSearch(location.search), [location.search]);
  useEffect(() => {
    if (!registry || !ideaAsk) return undefined;
    let alive = true;
    (async () => {
      try {
        const idea = await ideasApi.idea(ideaAsk.idea);
        const scan = idea.scans.find((s) => s.id === ideaAsk.scan);
        if (!scan || !scan.available) throw new Error("This idea's scan is not switched on for your account yet.");
        const res = await screenerApi.stages({ source: scan.text, base_tf: scan.base_tf, params: scan.params });
        if (!alive) return;
        setStages(stagesFromServer(registry, res.stages));
        setScanTf(res.base_tf);
        setName(scanName(idea, scan));
        setSavedId(undefined);
        if (universeV2) setPendingRun(true);           // one tap: "Try as a scan" runs it on the chosen universe
      } catch (e) {
        if (alive) toast.error(errorText(e));
      } finally {
        if (alive) navigate({ search: "" }, { replace: true });
      }
    })();
    return () => { alive = false; };
  }, [registry, ideaAsk, navigate, toast, universeV2]);

  const plan = universes?.batch ?? FALLBACK_PLAN;
  const universe = useMemo(() => {
    const max = plan.max_symbols;
    const nifty = lists[DEFAULT_KEY];
    const serverNifty = !!nifty && nifty !== "failed" && nifty.symbols.length > 0;
    const nifty50 = serverNifty ? nifty.symbols : NIFTY50_FALLBACK;
    let chosen: readonly string[] = [];
    let label = "your own symbols";
    let note: string | null = null;
    let loading = false;
    let builtin = false;                                // SCR-FIX D10: the built-in NIFTY 50 names stand in (dated, said)
    if (chosenKey) {
      const got = lists[chosenKey];
      if (chosenKey === DEFAULT_KEY && builtinDefault) {
        chosen = NIFTY50_FALLBACK;
        label = "NIFTY 50";
        builtin = true;
      } else if (!universesDone || !got) {
        loading = true;
      } else if (got === "failed") {
        note = "This list could not be loaded, so the scan runs on NIFTY 50.";
      } else {
        chosen = got.symbols.slice(0, max);
        const group = universes?.groups.find((g) => g.lists.some((l) => l.key === chosenKey));
        const entry = group?.lists.find((l) => l.key === chosenKey);
        label = entry && group ? listLabel(entry, group.group) : got.name;
        if (got.truncated) note = `This list has ${got.source_count} stocks; the first ${max} are scanned.`;
      }
    } else if (choice.startsWith("wl:")) {
      const list = myLists?.find((w) => `wl:${w.id}` === choice);
      chosen = (list?.symbols ?? []).slice(0, max);
      label = list?.name ?? "My watchlist";
      if (list && list.symbols.length > max) note = `"${list.name}" has ${list.symbols.length} symbols; the first ${max} are scanned.`;
    } else {
      chosen = parseSymbols(symbolsText, max);
    }
    if (loading) return { symbols: [] as string[], name: label, note: null, loading, builtin: false };
    const eff = effectiveUniverse(chosen, nifty50);
    if (eff.fellBack && !note) note = choice === CUSTOM ? "Type at least one symbol. Until then the scan runs on NIFTY 50." : "This list has no stocks yet, so the scan runs on NIFTY 50.";
    if (eff.fellBack && !serverNifty) builtin = true;
    return { symbols: eff.symbols, name: eff.fellBack ? "NIFTY 50" : label, note, loading, builtin };
  }, [plan.max_symbols, lists, chosenKey, builtinDefault, universesDone, universes, choice, myLists, symbolsText]);
  const legacySymbols = useMemo(() => parseSymbols(symbolsText), [symbolsText]);
  const symbols = universeV2 ? universe.symbols : legacySymbols;
  const text = useMemo(() => (registry ? screenText(registry, stages) : ""), [registry, stages]);
  const stagesRef = useRef(stages);
  stagesRef.current = stages;
  // what must be filled in before a block can run (a category without a value, a blank name): no Run, no preview
  const local = useMemo(() => (registry ? blockProblems(registry, stages) : {}), [registry, stages]);
  const allProblems = useMemo(() => ({ ...problems, ...local }), [problems, local]);

  // Every edit is checked on the server (debounced); a problem lands on the stage it is about. Run waits for the check
  // of the current text (`validating`), so a scan is never run on the previous text's verdict.
  useEffect(() => {
    const seq = ++validateSeq.current;                // also invalidates a check in flight for an older text
    if (!registry || !text) { setProblems({}); setOtherProblem(null); setValidating(false); return undefined; }
    setValidating(true);
    const timer = window.setTimeout(() => {
      screenerApi.validate(text, scanTf).then((v) => {
        if (seq !== validateSeq.current) return;
        const byStage: Record<string, string> = {};
        let other: string | null = null;
        for (const p of v.problems) {
          const id = stageAt(registry, stagesRef.current, p.pos);
          if (id && !byStage[id]) byStage[id] = p.message;
          else if (!id && !other) other = p.message;
        }
        setProblems(byStage);
        setOtherProblem(other);
      }).catch(() => undefined)                        // a failed check is not a problem in the scan; Run reports errors
        .finally(() => { if (seq === validateSeq.current) { setValidating(false); setCheckedText(text); } });
    }, VALIDATE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [registry, text, scanTf]);                        // an edit that leaves the text alone (a disabled block) is not re-checked

  const clean = Object.keys(allProblems).length === 0 && !otherProblem;
  const canRun = !!registry && !!text && symbols.length > 0 && !validating && clean && !running;
  const now = useMemo(() => (registry ? runStamp(registry, stages, scanTf, symbols) : null), [registry, stages, scanTf, symbols]);
  const fresh = !!now && sameRun(ran, now);
  const liveFresh = !fresh && !!now && !!preview && sameRun(preview.stamp, now);

  // Live counts while editing: after the check passes and the trader pauses, a preview run fills the trails.
  // One at a time: while a preview is out, edits wait; when it lands it is kept with the scan it counted (`stamp`)
  // and shown only while that is still the scan (liveFresh), and the next preview goes for the scan as it is then.
  // Never during a Run.
  const canPreview = live && !!registry && !!text && symbols.length > 0 && symbols.length <= RUN_MAX && !validating && clean && !running && !fresh;
  useEffect(() => {
    if (!canPreview || previewBusy || !now || (preview && sameRun(preview.stamp, now))) return undefined;
    const stamp = now;
    const timer = window.setTimeout(() => {
      setPreviewBusy(true);
      screenerApi.preview(text, scanTf, symbols).then((res) => setPreview({ res, stamp }))
        .catch(() => undefined)                        // a live count is a convenience; Run reports errors
        .finally(() => setPreviewBusy(false));
    }, PREVIEW_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [canPreview, previewBusy, now, preview, text, scanTf, symbols]);

  const doRun = useCallback(async () => {
    if (!registry || !canRun || !now) return;
    setRunning(true);
    const stamp = now;                                 // the scan as sent, not as it may be by the time the reply lands
    try {
      if (universeV2 && symbols.length > RUN_MAX) {
        // a long list: batch after batch, the results growing as each lands; paced by the broker calls each made
        stopRef.current = false;
        setProgress({ done: 0, total: symbols.length });
        let first = true;
        const out = await runInBatches(symbols, plan, {
          run: (batch) => screenerApi.runBatch(text, scanTf, batch),
          onProgress: (done, total, merged) => {
            setRun(merged);
            if (first) { setRan(stamp); setRanAt(new Date()); setRunKey((k) => k + 1); first = false; }
            setProgress({ done, total });
          },
          stopped: () => stopRef.current,
        });
        if (out) { setRun(out); setRanAt(new Date()); }
      } else {
        const out = await screenerApi.run(text, scanTf, symbols);
        setRun(out);
        setRan(stamp);
        setRanAt(new Date());
        setRunKey((k) => k + 1);
      }
    } catch (e) {
      toast.error(errorText(e));                       // a batch that failed: the batches before it stay shown
    } finally {
      setRunning(false);
      setProgress(null);
    }
  }, [registry, canRun, now, text, scanTf, symbols, toast, universeV2, plan]);

  // One tap: a screen opened with "Try as a scan" runs as soon as its text has been checked and the universe is ready;
  // a problem in it stops the run (the trader sees it on its block), and the blocks stay editable either way.
  useEffect(() => {
    if (!pendingRun) return;
    if (canRun && checkedText === text) { setPendingRun(false); void doRun(); return; }
    if (registry && text && checkedText === text && !validating && !clean) setPendingRun(false);
  }, [pendingRun, canRun, checkedText, text, validating, clean, registry, doRun]);
  const held = useMemo(() => (universeV2 ? heldBack(fresh ? run : null) : []), [universeV2, fresh, run]);
  // A long list's answer leads with what matters: the matches (and any stock the broker's limit held back); the rest
  // are counted, and listed on request.
  const [showAll, setShowAll] = useState(false);
  // Day-one fix 4: on v2 every answer leads with its matches; the ones that did not match are counted, listed on request.
  const longAnswer = universeV2 && (run?.results.length ?? 0) > 0;
  const shownResults = useMemo(() => {
    if (!run) return null;
    if (!longAnswer || showAll) return run.results;
    return run.results.filter((r) => r.matched || held.includes(r.symbol));
  }, [run, longAnswer, showAll, held]);
  const hiddenCount = run && shownResults ? run.results.length - shownResults.length : 0;

  const showStage = (id: string) => {
    const handle = document.querySelector<HTMLElement>(`[data-stage-id="${id}"] button`);
    handle?.scrollIntoView({ block: "center", behavior: "smooth" });
    handle?.focus({ preventScroll: true });
  };

  /** Saves the scan as it is now; its id, or null (the toast says why). With 11j on, the save sends its builder blocks
   * and the server writes a new version when the scan changed (LAB-18). */
  const saveNow = async (): Promise<number | null> => {
    if (!text) return null;
    try {
      const blocks = ideaOn && registry ? builderOf(snapshot(registry, name.trim() || "Untitled scan", scanTf, stages, new Date(), ideaReads)) : undefined;
      const saved = await screenerApi.save(name.trim() || "Untitled scan", text, scanTf, savedId, blocks);
      setSavedId(saved.id);
      if (ideaOn && saved.id === savedId) loadVersions(saved.id);      // a new id loads through the effect above
      toast.success(`Saved "${saved.name}".`);
      return saved.id;
    } catch (e) {
      toast.error(errorText(e));
      return null;
    }
  };
  const doSave = async () => { await saveNow(); };

  /** "Open this version": its blocks go back into the builder (the blocks it was saved with, else its text as blocks).
   * Nothing is saved; the next save is a new version. */
  const openVersion = async (v: ServerVersion) => {
    if (!registry || savedId == null) return;
    setOpeningVersion(v.version);
    try {
      const full = await screenerApi.version(savedId, v.version);
      const blocks = full.builder?.stages
        ?? stagesFromServer(registry, (await screenerApi.stages({ source: full.text, base_tf: full.base_tf, params: full.params })).stages);
      setStages(blocks);
      setScanTf(full.base_tf);
      setName(full.name);
      setVersionsOpen(false);
      toast.success(`Version ${v.number} is in the builder. Save to keep it as the newest version.`);
    } catch {
      toast.error("This version could not be opened. Try again in a moment.");
    } finally {
      setOpeningVersion(null);
    }
  };

  // the counts shown: the run's while the scan is the one that ran, else a live preview's for the current scan
  const shown = fresh && run ? { funnel: run.funnel, matched: run.matched.length }
    : liveFresh && preview ? { funnel: preview.res.funnel, matched: preview.res.matched_count } : null;
  const rows = trail(stages, shown?.funnel ?? null, !!shown);
  const killer = rows.find((r) => r.kills);
  const matchedNow = shown ? shown.matched : null;
  // SCR-FIX D1: a disabled Run says why ("Add symbols", "Fix stage 2"); a check or a run in flight shows on the button
  const runWhy = registry && !running && !validating ? runBlocker({ stages, symbols: symbols.length, text, problems: allProblems, otherProblem }) : null;
  const narrow = useMediaQuery(NARROW_QUERY);
  const [moreOpen, setMoreOpen] = useState(false);
  const headerTools = ([
    taxonomy && { id: "browse-screens", onClick: () => setGalleryOpen(true), content: <><LayoutGrid size={14} aria-hidden /> Browse screens</> },
    advanced && taxonomy && mirrors && { id: "library-tools-open", onClick: () => setToolsOpen(true), content: <><Layers size={14} aria-hidden /> Library tools</> },
    etfOn && { id: "etf-open", onClick: () => setEtfOpen(true), content: <><LayoutGrid size={14} aria-hidden /> ETFs</> },
    ideaOn && { id: "idea-open-dialog", onClick: () => setIdeaOpen(true), content: <><Lightbulb size={14} aria-hidden /> From an idea</> },
    advanced && pineOn && { id: "pine-open", onClick: () => setPineOpen(true), content: <><Code2 size={14} aria-hidden /> Pine Script</> },
    ideaOn && { id: "scan-alert-open", onClick: () => setAlertOpen(true), disabled: !text, content: <><BellRing size={14} aria-hidden /> Tell me when it matches</> },
    universeV2 && ((taxonomy && mirrors) || pineOn || builderU2) && {
      id: "screener-advanced", onClick: () => setAdvanced((v) => !v), ghost: true, keepOpen: true, expanded: advancedOpen,
      content: <>Advanced <ChevronDown size={14} aria-hidden className={cx("transition-transform", advancedOpen && "rotate-180")} /></>,
    },
    ideaOn && scanVersions.length > 0 && { id: "scan-versions-open", onClick: () => setVersionsOpen(true), content: <><History size={14} aria-hidden /> Versions ({scanVersions.length})</> },
  ] as (HeaderTool | false | null | undefined)[]).filter((t): t is HeaderTool => !!t);

  if (loadError === SCREENER_OFF) return <ComingSoon flag="screener_v2" />;
  if (loadError) return <ErrorState message={loadError} />;

  return (
    <PlainTimeframes.Provider value={universeV2}>
    <div className="screener-page pb-20 sm:pb-0">
      {dishOn && <PageDishV2 page="screener" className="mb-4" />}
      {/* Header strip: name (edit in place), freshness, Run (primary) / Save */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className={cx("min-w-0 flex-1", universeV2 ? "basis-full" : ideaOn && "basis-full sm:basis-0")}>
          <span className="sr-only">Scan name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder={universeV2 ? "Untitled scan" : undefined}
                 className="w-full max-w-md rounded-control border border-transparent bg-transparent px-1 text-t24 font-semibold text-fg hover:border-border focus:border-border focus:outline-none focus:ring-2 focus:ring-brand" />
        </label>
        <FreshnessPill ranAt={ranAt} source={run?.data_source ?? null} scanTf={scanTf} />
        {/* BT1b: the tier the last run actually read (only sent with byob_data_tier on). */}
        <DataTierBadge badge={run?.data_tier} />
        {/* SCR-FIX D9: up to eight tools - inline from the small-tablet width up, one "More" sheet on a phone */}
        {narrow ? (
          headerTools.length > 0 && (
            <Button size="v2" onClick={() => setMoreOpen(true)} aria-haspopup="dialog" data-testid="screener-more">
              <MoreHorizontal size={16} aria-hidden /> More
            </Button>
          )
        ) : headerTools.map((t) => (
          <Button key={t.id} size="sm" variant={t.ghost ? "ghost" : "secondary"} onClick={t.onClick} disabled={t.disabled}
                  aria-expanded={t.expanded} data-testid={t.id}>{t.content}</Button>
        ))}
        <div className="hidden items-center gap-2 sm:flex">
          {runWhy && <span id="run-why" className="text-t12 text-fg-muted" data-testid="run-why">{runWhy}</span>}
          <Button size="v2" onClick={() => void doSave()} disabled={!text}><Save size={14} aria-hidden /> Save</Button>
          <Button size="v2" variant="primary" edge onClick={() => void doRun()} disabled={!canRun} loading={running} data-testid="run-scan"
                  aria-describedby={runWhy ? "run-why" : undefined}>
            <Play size={14} aria-hidden /> Run
          </Button>
        </div>
      </div>
      {narrow && (
        <Sheet side="bottom" open={moreOpen} onOpenChange={setMoreOpen} title="More">
          <ul className="space-y-space-2" data-testid="screener-more-list">
            {headerTools.map((t) => (
              <li key={t.id}>
                <Button size="v2" variant={t.ghost ? "ghost" : "secondary"} className="w-full justify-start" disabled={t.disabled}
                        aria-expanded={t.expanded} data-testid={t.id}
                        onClick={() => { if (!t.keepOpen) setMoreOpen(false); t.onClick(); }}>{t.content}</Button>
              </li>
            ))}
          </ul>
        </Sheet>
      )}

      <div className="screener-grid">
        {/* Universe */}
        <aside aria-label="Universe" className="screener-universe rounded-panel border border-border bg-surface-1 p-4">
          <h2 className="text-t15 font-semibold text-fg">Universe</h2>
          <div className="screener-universe-body">
          {universeV2 ? (
          <div>
          <UniversePicker groups={universes?.groups ?? []} builtinDefault={builtinDefault} watchlists={myLists} choice={choice}
                          onChoice={(c) => { if (c === CUSTOM && !symbolsText.trim()) setSymbolsText(symbols.join(", ")); setChoice(c); }}
                          symbolsText={symbolsText} onSymbolsText={setSymbolsText}
                          count={symbols.length} name={universe.name} loading={universe.loading} note={universe.note} maxSymbols={plan.max_symbols} />
          {universe.builtin && <p className="mt-1 text-t12 text-warn" data-testid="universe-builtin">{NIFTY50_FALLBACK_NOTE}</p>}
          </div>
          ) : (
          <div>
          <label className="mt-3 block text-t12 text-fg-muted" htmlFor="screener-symbols">Symbols (up to 50)</label>
          <textarea id="screener-symbols" value={symbolsText} onChange={(e) => setSymbolsText(e.target.value)} rows={4}
                    placeholder="RELIANCE, TCS, INFY"
                    className="mt-1 w-full resize-y rounded-control border border-border bg-surface-inset px-2 py-1.5 font-mono text-t13 text-fg placeholder:text-fg-muted/60 focus:outline-none focus:ring-2 focus:ring-brand" />
          <p className="mt-1 text-t12 text-fg-muted"><span className="font-mono font-tabular text-fg">{symbols.length}</span> symbols · separate with commas or new lines</p>
          {watchlistsOn && <WatchlistPicker symbols={symbols} max={50} onLoad={(list) => setSymbolsText(list.join(", "))} />}
          </div>
          )}
          <div>
          <label className="mt-3 block text-t12 text-fg-muted" htmlFor="screener-tf">Scan timeframe</label>
          {/* SCR-FIX D8: the shared picker (a bottom sheet with 44 px rows on a phone); all nine timeframes */}
          <Combobox id="screener-tf" label="Scan timeframe" value={scanTf} onChange={setScanTf} className="mt-1 w-full"
                    options={TIMEFRAMES.map((tf) => ({ value: tf, label: timeframeWords(tf, universeV2) }))} />
          </div>
          {universeV2 && symbols.length > RUN_MAX && (
            <p className="mt-3 text-t12 text-fg-muted" data-testid="universe-batches">
              Live counts show for lists of up to {RUN_MAX} stocks. Run scans all {symbols.length}, {plan.size} at a time, within your broker&apos;s limits.
            </p>
          )}
          {advanced && (
          <label className="mt-3 flex items-center gap-2 text-t12 text-fg-muted">
            <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} className="h-3.5 w-3.5 accent-[rgb(var(--signal))]" data-testid="live-counts" />
            Live counts while editing
          </label>
          )}
          {/* SCR-FIX E1: on the v2 page the "not advice" line is the page's one footnote; the rest of the note is in Details */}
          {universeV2 ? (
            <Details id="screener.universe.about" className="mt-4">
              <p>Closed bars only, read from your broker. A scan lists symbols that meet your own conditions.</p>
            </Details>
          ) : (
            <p className="mt-4 text-t12 text-fg-muted">Closed bars only, read from your broker. A scan lists symbols that meet your own conditions; it is not advice.</p>
          )}
          </div>
        </aside>

        {/* Conditions: the funnel */}
        <div className="screener-canvas">
          {registry ? (
            <FunnelCanvas stages={stages} registry={registry} scanTf={scanTf} universe={symbols.length} funnel={shown?.funnel ?? null}
                          fresh={!!shown} live={liveFresh} matched={matchedNow} problems={allProblems} runKey={runKey} onChange={setStages} formula={builderU2 && advanced} />
          ) : (
            <div className="h-40 animate-pulse rounded-panel border border-border bg-surface-1" aria-label="Loading the conditions" />
          )}
          {otherProblem && <p role="alert" className="mt-2 text-t12 text-warn">{otherProblem}</p>}
        </div>

        {/* Results */}
        <div className="screener-results space-y-3">
          {killer && registry && (
            <div role="status" className="rounded-panel border border-warn/50 bg-surface-1 p-3 text-t13 text-fg">
              <p>This condition removes every symbol. Loosen it or disable it to see candidates.</p>
              <div className="mt-2 flex gap-2">
                <Button size="sm" onClick={() => showStage(killer.id)}>Show the stage</Button>
                <Button size="sm" onClick={() => setStages((all) => all.map((s) => (s.id === killer.id ? { ...s, enabled: false } : s)))}>Disable it</Button>
              </div>
            </div>
          )}
          {progress && (
            <div role="status" className="rounded-panel border border-border bg-surface-1 p-3 text-t13 text-fg" data-testid="scan-progress">
              <div className="flex items-center justify-between gap-3">
                <p>Scanning <span className="font-mono font-tabular">{progress.done}</span> of <span className="font-mono font-tabular">{progress.total}</span> stocks…</p>
                <Button size="sm" onClick={() => { stopRef.current = true; }} data-testid="scan-stop">Stop</Button>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
                <div className="h-full bg-signal transition-[width]" style={{ width: `${Math.round((progress.done / Math.max(1, progress.total)) * 100)}%` }} />
              </div>
            </div>
          )}
          {held.length > 0 && !running && (
            <p role="status" className="rounded-panel border border-warn/50 bg-surface-1 p-3 text-t13 text-fg" data-testid="scan-held">
              {held.length} {held.length === 1 ? "stock waited" : "stocks waited"} for your broker&apos;s limit. Run again in a minute to include {held.length === 1 ? "it" : "them"}.
            </p>
          )}
          {longAnswer && (hiddenCount > 0 || showAll) && (
            <p className="flex flex-wrap items-center justify-between gap-2 text-t13 text-fg-muted" data-testid="results-hidden">
              <span>{showAll ? `Showing all ${run?.results.length ?? 0} stocks.` : `${hiddenCount} ${hiddenCount === 1 ? "stock did" : "stocks did"} not match; they are not listed.`}</span>
              <Button size="sm" variant="ghost" onClick={() => setShowAll((v) => !v)}>{showAll ? "Matches only" : `Show the ${hiddenCount} that did not match`}</Button>
            </p>
          )}
          {universeV2 && !run && !running && stages.length === 0 && readyScans.length > 0 && (
            <section className="rounded-panel border border-border bg-surface-1 p-4" data-testid="ready-scans" aria-labelledby="ready-scans-title">
              <h2 id="ready-scans-title" className="text-t15 font-semibold text-fg">Start with a ready scan</h2>
              <p className="mt-1 text-t13 text-fg-muted">One tap runs it on {universe.name || "your list"}. You can change every condition afterwards.</p>
              <ul className="mt-3 space-y-2">
                {readyScans.map((s) => (
                  <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 rounded-control border border-subtle px-3 py-2">
                    <span className="min-w-0 flex-1 text-t14 font-medium text-fg">{s.name}</span>
                    <Button size="sm" variant="primary" edge onClick={() => openScreen(s, true)} aria-label={`Run ${s.name}`} data-testid="ready-scan-run">
                      <Play size={14} aria-hidden /> Run
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <ResultViews results={shownResults} running={running} runKey={runKey} stale={!!run && !fresh}
                       why={fresh && registry ? (symbol) => whyChips(registry, stages, run?.funnel ?? null, symbol) : undefined}
                       onShowEvidence={(r) => { if (r.evidence_id != null) setEvidence({ id: r.evidence_id, symbol: r.symbol }); }} />
          <EvidencePanel evidenceId={evidence?.id ?? null} symbol={evidence?.symbol} onClose={() => setEvidence(null)} />
          {run && (universeV2
            ? <Details id="screener.results.about" label="About these results"><p>{run.disclaimer}</p></Details>
            : <p className="text-t12 text-fg-muted">{run.disclaimer}</p>)}
        </div>
      </div>
      {/* SCR-FIX E1: the page's one "not advice" line, at its foot */}
      {universeV2 && <div className="mt-space-6"><PageFootnote /></div>}

      {taxonomy && (
        <Dialog wide open={galleryOpen} onOpenChange={setGalleryOpen} title="Screen library"
                description="Pre-built screens filed by method (how a screen reasons) and setup type (what it looks for).">
          <ScreenGallery taxonomy={taxonomy} canOpen={canOpen} onOpen={(s) => openScreen(s)} legends={legends} mirrors={mirrorMap}
                         onTry={universeV2 ? (s) => openScreen(s, true) : undefined} />
        </Dialog>
      )}
      {taxonomy && mirrors && (
        <Dialog wide open={toolsOpen} onOpenChange={setToolsOpen} title="Library tools"
                description="Run several screens together to see where they agree, or watch a list for a ready-then-triggered move.">
          <LibraryTools taxonomy={taxonomy} mirrors={mirrors} watchLists={watchLists} symbolsText={universeV2 ? symbols.slice(0, RUN_MAX).join(", ") : symbolsText} />
        </Dialog>
      )}

      {etfOn && (
        <Dialog wide open={etfOpen} onOpenChange={setEtfOpen} title="ETFs"
                description="NSE ETFs from the shared end-of-day history: traded value and how each moved against the index it tracks.">
          <EtfPanel />
        </Dialog>
      )}

      {pineOn && (
        <Dialog wide open={pineOpen} onOpenChange={setPineOpen} title="Pine Script"
                description="Paste a TradingView Pine script: see what translates, read each condition in plain words and run it as a screen. The script is never run.">
          {pineOpen && (
            <Suspense fallback={<p className="text-t13 text-fg-muted">Loading…</p>}>
              <PinePanel symbols={symbols.slice(0, RUN_MAX)} baseTf={scanTf} />
            </Suspense>
          )}
        </Dialog>
      )}

      {ideaOn && (
        <Dialog wide open={ideaOpen} onOpenChange={setIdeaOpen} title="A scan from your idea"
                description="Write the idea in plain words. Each part becomes a block you can check and edit; anything ATP cannot read yet is listed, never guessed.">
          {ideaOpen && (
            <Suspense fallback={<p className="text-t13 text-fg-muted">Loading…</p>}>
              <IdeaPanel baseTf={scanTf} onOpen={openIdea} />
            </Suspense>
          )}
        </Dialog>
      )}

      {ideaOn && (
        <Dialog open={alertOpen} onOpenChange={setAlertOpen} title="Tell me when it matches"
                description="Get a message when a symbol from your list passes this scan.">
          {alertOpen && (
            <Suspense fallback={<p className="text-t13 text-fg-muted">Loading…</p>}>
              <ScanAlertPanel name={name} symbols={symbols.slice(0, RUN_MAX)} scanTf={scanTf} screenId={savedId} save={saveNow}
                              intrabar={features.on("screener_intrabar")}
                              universeName={universeV2 ? universe.name : undefined} universeTotal={symbols.length} />
            </Suspense>
          )}
        </Dialog>
      )}

      {ideaOn && (
        <Dialog open={versionsOpen} onOpenChange={setVersionsOpen} title="Versions of this scan"
                description="Every save that changes the scan is a version. Open an older one to go back to it.">
          {versionsOpen && (
            <Suspense fallback={<p className="text-t13 text-fg-muted">Loading…</p>}>
              <ScanVersionsPanel versions={scanVersions} onOpen={(v) => void openVersion(v)} max={versionsMax} opening={openingVersion} />
            </Suspense>
          )}
        </Dialog>
      )}

      {/* Phone: the live count and Run stay in reach - PW2: just above the app's bottom navigation */}
      <div className={cx("fixed inset-x-0 bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-20 flex items-center justify-between gap-3 border-t border-border bg-surface-1 px-4 py-2 sm:hidden")}
           data-testid="screener-bottom-bar">
        <div className="min-w-0">
          <p className="text-t12 text-fg-muted">
            Matched <span className="font-mono font-tabular text-t15 font-semibold text-signal">{matchedNow ?? "–"}</span>
            <span> of <span className="font-mono font-tabular">{symbols.length}</span></span>
          </p>
          {runWhy && <p id="run-why-mobile" className="text-t12 text-fg-muted" data-testid="run-why-mobile">{runWhy}</p>}
        </div>
        <div className="flex shrink-0 gap-2">
          {/* SCR-FIX D1: 44 px targets, Save with its word, Run outlined so it reads as a button on the bar */}
          <Button size="v2" onClick={() => void doSave()} disabled={!text}><Save size={16} aria-hidden /> Save</Button>
          <Button size="v2" variant="primary" edge onClick={() => void doRun()} disabled={!canRun} loading={running} data-testid="run-scan-mobile"
                  aria-describedby={runWhy ? "run-why-mobile" : undefined}><Play size={16} aria-hidden /> Run</Button>
        </div>
      </div>
    </div>
    </PlainTimeframes.Provider>
  );
}
