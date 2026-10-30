import { SETTINGS_PATH } from "../../settings/paths";
import { KeyRound, SendHorizontal, Sparkles } from "lucide-react";
import { IndexCell } from "../../dashboard/MarketStripV2";
import { vixBand } from "../../dashboard/model";
import type { MarketStrip } from "../../dashboard/types";
import { stripDelay, usePolled, type Loadable } from "../../dashboard/useDashboardData";
import { Skeleton } from "../../components/primitives/Feedback";
import { LevelChip } from "../../ui/LevelChip";
import { Signed } from "../../ui/numbersFirst";
import { EMPTY, formatPrice } from "../../ui/format";
import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ClipboardEvent, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { useAuth } from "../../auth/AuthContext";
import { api } from "../../api/client";
import ConnectAiPanelV2, { ConnectAiChip } from "../../aiKeys/ConnectAiPanelV2";
import { AI_CONNECTION_CHANGED_EVENT } from "../../aiKeys/connect";
import { useFeatures } from "../../ui/useFeatures";
import { CARD } from "../../dashboard/CardV2";
import { PageIntro } from "../../ui/PageIntro";
import { friendlyError } from "../aiTask";
import FirstUseModal from "../components/FirstUseModal";
import { useCopilotT } from "../i18n";
import { COPILOT_TABS, copilotPath } from "../tabs";
import { AnswerV2 } from "./AnswerV2";
import { AttachMenu, imageFrom } from "./ChartAttachV2";
import {
  chartQuestion, chatApi, cleanQuestion, composerPlacement, DEFAULT_KEEP, jobCards, marketLineText, MAX_QUESTION, needsKey, numbersCells, quickChips, readingQuestion,
  restoredTurns, settled, withChart, withTurn,
  type ChartSource, type ChatAnswer, type ChatHome, type Turn,
} from "./model";
import { chartAnalyzeApi, isFeatureOff } from "../../chartAnalyze/api";
import { engineAudience } from "../../chartAnalyze/engineWords";
import type { AnalyzeStatus } from "../../chartAnalyze/types";
import { friendlyError as apiFriendlyError } from "../../api/errors";
import { Details } from "../../ui/Details";

// M59 CR-2: the attached chart's turn (the chart drawing and the reading) loads in its own chunk, only when used.
const ChartTurnV2 = lazy(() => import("./ChartTurnV2"));

/**
 * Item 5 (owner's handover 5 C), reworked by CP-UX CU2 (owner order 30 Oct): Copilot screen 1, phone first, top to
 * bottom: the numbers strip (NIFTY / BANK NIFTY / SENSEX last and change, the India VIX level chip, FII net - the Today
 * strip's own cells and /api/market/strip), four chips (Brief · Read chart · Check my trade · My positions), then the
 * question box - pinned above the bottom bar on a phone - as the ONLY primary action. A small "AI on / off" chip opens the
 * key setup (CU5: with no key, one line "AI is off — answers are from rules" and a Connect button instead). The
 * conversation follows (CU6: kept by the server across reloads); "What I can do" (every job) and the older Copilot
 * pages sit under one closed "Advanced"; one disclaimer line closes the page. The page intro is the (?) beside the title in the header. Shown while
 * `ui_v2` is on; with the flag off today's seven-tab Copilot renders exactly as before. Nothing here places, changes or
 * cancels an order (ADR-0006).
 */

const BUTTON = "inline-flex h-11 shrink-0 items-center justify-center gap-space-2 rounded-control bg-brand px-space-5 text-t14 font-semibold text-on-brand transition-colors duration-fast hover:bg-brand-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-50";
const CHIP = "inline-flex min-h-11 items-center rounded-pill border border-border bg-surface-1 px-space-3 text-left text-t13 text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";
const SMALL_CHIP = "inline-flex min-h-11 items-center gap-space-1 rounded-pill border border-border bg-surface-1 px-space-3 text-t13 font-medium text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8";

export interface ChatViewProps {
  home: ChatHome | null;
  homeError: string | null;
  turns: readonly Turn[];
  draft: string;
  busy: boolean;
  placement: "bottom" | "inline";
  onDraft: (text: string) => void;
  onAsk: (question: string) => void;
  /** CU2: the numbers strip (GET /api/market/strip with SENSEX); left out: its loading state. */
  strip?: Loadable<MarketStrip>;
  /** Settings redesign A (ai_one_key): the Connect AI panel, opened from the "AI off" chip. */
  connect?: ReactNode;
  /** Settings redesign A: the small "AI connected" chip once the user's own key answers. */
  aiChip?: ReactNode;
  /** CU2: the key setup is open (the "AI off" chip toggles it). */
  keyOpen?: boolean;
  onKeyOpen?: (open: boolean) => void;
  /** M59 CR-2: a chart in the Ask box (upload / TradingView snapshot link); left out: no attach button. */
  onAttach?: (source: ChartSource) => void;
}

export function Composer({ draft, busy, placement, onDraft, onAsk, placeholder, onAttach }: Pick<ChatViewProps, "draft" | "busy" | "placement" | "onDraft" | "onAsk" | "onAttach"> & { placeholder?: string }) {
  const t = useCopilotT();
  const submit = (e: FormEvent) => { e.preventDefault(); const q = cleanQuestion(draft); if (q && !busy) onAsk(q); };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); const q = cleanQuestion(draft); if (q && !busy) onAsk(q); }
  };
  // M59 CR-2: a chart screenshot pasted in the box is attached as a chart (a text paste stays text)
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const image = onAttach && !busy ? imageFrom(e.clipboardData?.files) : null;
    if (image) { e.preventDefault(); onAttach!({ kind: "file", file: image, name: image.name || "pasted-chart.png" }); }
  };
  const bottom = placement === "bottom";
  return (
    <form onSubmit={submit} data-composer={placement} data-tour="copilot-ask"
      className={bottom
        ? "fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-10 flex items-end gap-space-2 border-t border-subtle bg-surface-1 px-space-4 py-space-2 shadow-raised"
        : "sticky top-space-2 z-10 flex items-end gap-space-2 rounded-panel border border-border bg-surface-1 p-space-3 shadow-card"}>
      {onAttach && <AttachMenu onAttach={onAttach} disabled={busy} />}
      <label className="sr-only" htmlFor="copilot-question">{t("chat.boxLabel")}</label>
      <textarea id="copilot-question" value={draft} maxLength={MAX_QUESTION} rows={bottom ? 1 : 2} onChange={(e) => onDraft(e.target.value)} onKeyDown={onKey} onPaste={onPaste}
        placeholder={placeholder ?? t("chat.placeholder")} disabled={busy}
        className={`min-w-0 flex-1 resize-none rounded-control border border-border bg-surface-2 px-space-3 py-space-2 text-t15 text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand disabled:opacity-60 ${bottom ? "min-h-11" : "min-h-16"}`} />
      <button type="submit" className={BUTTON} disabled={busy || !cleanQuestion(draft)} aria-label={t("chat.ask")} data-primary>
        <SendHorizontal size={16} aria-hidden /><span className={bottom ? "sr-only" : ""}>{busy ? t("chat.asking") : t("chat.ask")}</span>
      </button>
    </form>
  );
}

/** CU2: the numbers strip - the Today strip's own index cells for NIFTY 50, BANK NIFTY and SENSEX, then India VIX as its
 * level chip and the FII net (crore, signed). A missing number is "–", never 0. */
export function NumbersStrip({ strip, home, nowMs }: { strip: Loadable<MarketStrip>; home: ChatHome | null; nowMs: number }) {
  const t = useCopilotT();
  const data = strip.status === "ok" ? strip.data : null;
  const cells = numbersCells(data);
  const vix = data?.indices.find((i) => i.key === "INDIA VIX") ?? null;
  const band = vixBand(vix?.ltp);
  const fii = home?.market_line.fii_net_crore ?? null;
  return (
    <section aria-label={t("chat.numbers")} data-numbers-strip data-tour="copilot-market" className={`${CARD} space-y-space-3 p-space-3 md:p-space-4`}>
      <div className="grid grid-cols-3 gap-space-2 md:gap-space-4">
        {cells.map((c) => c.index ? (
          <IndexCell key={c.key} index={c.index} nowMs={nowMs} compact />
        ) : (
          <div key={c.key} className="min-w-0" data-index={c.key}>
            <div className="truncate text-t12 font-medium uppercase text-fg-muted">{c.label}</div>
            {strip.status === "loading"
              ? <Skeleton className="mt-space-2 h-6 w-16" />
              : <div className="mt-space-1 font-tabular text-t15 font-semibold text-fg md:text-t20">{EMPTY}</div>}
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-space-2 border-t border-subtle pt-space-3 text-t13">
        <span className="inline-flex min-h-7 items-center gap-space-1 rounded-pill bg-surface-2 px-space-3 text-fg" data-vix-level={band?.level.toLowerCase() ?? "unknown"}>
          <span className="text-fg-muted">India VIX</span>
          <span className="font-tabular font-semibold">{vix?.ltp != null ? formatPrice(vix.ltp) : EMPTY}</span>
          {band && <LevelChip scale="vix" level={band.level} />}
        </span>
        <span className="inline-flex min-h-7 items-center gap-space-1 rounded-pill bg-surface-2 px-space-3 text-fg" data-fii-net={fii === null ? "unknown" : "ok"}>
          <span className="text-fg-muted">{t("chat.fiiNet")}</span>
          {fii === null ? <span>{EMPTY}</span> : <><Signed value={fii} kind="count" decimals={0} /><span className="text-fg-muted">cr</span></>}
        </span>
      </div>
      {strip.status === "error" && <p className="text-t13 text-fg-muted">{t("chat.numbersError")}</p>}
    </section>
  );
}

export function ChatView({ home, homeError, turns, draft, busy, placement, onDraft, onAsk, strip = { status: "loading" }, connect, aiChip, keyOpen = false, onKeyOpen, onAttach }: ChatViewProps) {
  const t = useCopilotT();
  const line = marketLineText(home);
  const chips = home ? quickChips(home.jobs) : [];
  const empty = turns.length === 0;
  const aiOff = needsKey(home);
  const composer = <Composer draft={draft} busy={busy} placement={placement} onDraft={onDraft} onAsk={onAsk} onAttach={onAttach}
    placeholder={placement === "bottom" ? t("chat.placeholderShort") : home?.top_line} />;
  return (
    <div className="space-y-space-4" data-page="copilot-v2" data-placement={placement}>
      <header className="flex items-center justify-between gap-space-2">
        <h1 className="sr-only">{t("chat.title")}</h1>
        <p className="sr-only" data-top-line>{home?.top_line ?? t("chat.topLine")}</p>
        <p className={`min-w-0 truncate text-t13 ${line.empty ? "text-fg-muted" : "text-fg"}`} data-market-line={line.empty ? "empty" : "ok"} title={line.text || undefined}>
          {homeError ?? (home ? line.text : t("chat.loading"))}
        </p>
        <div className="shrink-0">
          {home && !aiOff && aiChip ? aiChip : home && !aiOff ? (
            <Link to={home.ai.settings ?? SETTINGS_PATH.ai} className={SMALL_CHIP} data-ai-state="on">
              <Sparkles size={14} aria-hidden className="text-up" />{t("chat.aiOn")}
            </Link>
          ) : null}
        </div>
      </header>

      {/* CU5: the no-key state, said once - one line and a Connect button, never a card of fixed sentences first. */}
      {home && aiOff && (
        <div data-no-key data-ai-state="off" className="flex min-w-0 items-center justify-between gap-space-3 rounded-panel border border-subtle bg-surface-1 px-space-3 py-space-1">
          <p className="flex min-w-0 items-center gap-space-2 text-t13 text-fg">
            <KeyRound size={14} aria-hidden className="shrink-0 text-fg-muted" />
            <span className="min-w-0">{home.ai.title ?? t("chat.aiOffLine")}</span>
          </p>
          {connect ? (
            <button type="button" className={SMALL_CHIP} aria-expanded={keyOpen} onClick={() => onKeyOpen?.(!keyOpen)} data-connect>{t("chat.connect")}</button>
          ) : (
            <Link to={home.ai.settings ?? SETTINGS_PATH.ai} className={SMALL_CHIP} data-connect>{t("chat.connect")}</Link>
          )}
        </div>
      )}

      {aiOff && connect && keyOpen && <div data-connect-slot>{connect}</div>}

      <NumbersStrip strip={strip} home={home} nowMs={Date.now()} />

      {chips.length > 0 && (
        <nav aria-label={t("chat.quick")} data-quick-chips data-tour="copilot-jobs" className="grid grid-cols-2 gap-space-2 sm:flex sm:flex-wrap">
          {chips.map((c) => (
            <button key={c.id} type="button" className={`${CHIP} justify-center font-medium sm:justify-start`} disabled={busy} onClick={() => onAsk(c.question)} data-quick={c.id}>
              {c.label}
            </button>
          ))}
        </nav>
      )}

      {placement === "inline" && composer}

      {!empty && (
        <ol className="space-y-space-4" aria-live="polite" data-conversation>
          {turns.map((turn) => (
            <li key={turn.id} className="space-y-space-2" data-turn={turn.id}>
              <p className="ml-auto w-fit max-w-full rounded-panel bg-surface-2 px-space-4 py-space-2 text-t14 text-fg">
                <span className="sr-only">{t("chat.youAsked")}: </span>{turn.question}
              </p>
              {turn.chart ? (
                <>
                  {/* M59 CR-2: the attached chart's analysis, then the Copilot's own structure card for its symbol (CR-1) */}
                  <div className={`${CARD} p-space-4`} data-chart-card>
                    <Suspense fallback={<p aria-busy="true" className="text-t14 text-fg-muted">{t("chat.chartAnalysing")}</p>}><ChartTurnV2 chart={turn.chart} /></Suspense>
                  </div>
                  {turn.answer && (
                    <div className={`${CARD} space-y-space-2 p-space-4`} data-chart-reading>
                      <h3 className="text-t13 font-semibold text-fg-muted">{t("chat.chartReading")}</h3>
                      <AnswerV2 answer={turn.answer} onAsk={onAsk} disclaimer={false} />
                      {(() => {
                        // M59 CR-2: the AI words only on the user's own tap (an ordinary question, metered as any other)
                        const question = turn.chart.status === "done" ? readingQuestion(turn.chart.item) : null;
                        return question ? (
                          <button type="button" className={CHIP} disabled={busy} data-explain-words onClick={() => onAsk(question)}>
                            {t("chat.explainWords")}
                          </button>
                        ) : null;
                      })()}
                    </div>
                  )}
                </>
              ) : (
                <div className={`${CARD} p-space-4`}>
                  {turn.answer ? <AnswerV2 answer={turn.answer} onAsk={onAsk} disclaimer={false} />
                    : turn.error ? <p role="alert" className="text-t14 text-fg">{turn.error}</p>
                    : <p aria-busy="true" className="text-t14 text-fg-muted">{t("chat.asking")}</p>}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}

      {/* CU2: every job and the older Copilot pages fold under one closed "Advanced" (nothing removed; each still opens). */}
      <div data-tour="copilot-more"><Details id="copilot.chat.advanced" label="Advanced">
        {home && (
          <section aria-labelledby="copilot-can-do" data-can-do>
            <h2 id="copilot-can-do" className="mb-space-1 font-semibold text-fg-muted">{t("chat.canDo")}</h2>
            <ul className="divide-y divide-subtle">
              {jobCards(home.jobs).map(({ job, link }) => (
                <li key={job.id} className="flex min-w-0 flex-col gap-space-1 py-space-2 sm:flex-row sm:items-center sm:justify-between" data-job={job.id} data-built={job.built}>
                  <div className="min-w-0">
                    <h3 className="text-t14 font-semibold text-fg">{job.title}</h3>
                    <p className="text-t13 text-fg-muted">{job.line}</p>
                  </div>
                  {job.built ? (
                    <button type="button" className={`${CHIP} shrink-0 self-start`} disabled={busy} onClick={() => onAsk(job.starter)}>{job.starter}</button>
                  ) : link ? (
                    <Link to={link.to} className="inline-flex min-h-11 shrink-0 items-center self-start text-t13 font-semibold text-brand hover:underline md:min-h-0">{link.label}</Link>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        )}
        <nav aria-labelledby="copilot-more" className="text-t13">
          <h2 id="copilot-more" className="mb-space-1 font-semibold text-fg-muted">{t("chat.more")}</h2>
          <ul className="flex flex-wrap gap-x-space-4">
            {COPILOT_TABS.map((tab) => (
              <li key={tab}><Link to={copilotPath(tab)} className="inline-flex min-h-11 items-center text-brand hover:underline md:min-h-0">{t(`tabs.${tab}`)}</Link></li>
            ))}
          </ul>
        </nav>
      </Details></div>

      <p className="text-t12 text-fg-muted" data-disclaimer>{home?.disclaimer ?? t("chat.disclaimer")}</p>

      {placement === "bottom" && (
        <>
          <div aria-hidden className="h-20" />
          {composer}
        </>
      )}
    </div>
  );
}

function usePlacement(): "bottom" | "inline" {
  const read = () => composerPlacement(typeof window === "undefined" ? 1440 : window.innerWidth);
  const [placement, setPlacement] = useState(read);
  useEffect(() => {
    const onResize = () => setPlacement(read());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return placement;
}

/** CU2: the numbers strip's prices - the Today strip's own polled read (GET /api/market/strip, with SENSEX), again
 * after the server's `refresh_seconds`, paused while the browser tab is hidden. Read-only. */
const readNumbers = () => api.marketStrip({ sensex: true });

/** The page's state: the home read once, the kept conversation (CU6), then each question as one turn. */
export function CopilotChatV2() {
  const t = useCopilotT();
  const [home, setHome] = useState<ChatHome | null>(null);
  const [homeError, setHomeError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [keep, setKeep] = useState(DEFAULT_KEEP);
  const next = useRef(1);
  const placement = usePlacement();
  const features = useFeatures();
  const oneKey = features.on("ui_v2") && features.on("ai_one_key");
  const [aiLabel, setAiLabel] = useState<string | null>(null);
  const [keyOpen, setKeyOpen] = useState(false);
  const [strip] = usePolled(readNumbers, stripDelay, true);
  // M59 CR-2: the attach button shows while chart analysis is on for this account; with the flag answered off, the
  // status is asked once - an admin then gets the button and the engine's owner action (CR-3), a user nothing.
  const chartOn = features.on("chart_analyze");
  const [engine, setEngine] = useState<AnalyzeStatus["engine"] | null>(null);
  useEffect(() => {
    if (!features.loaded || chartOn) return undefined;
    let live = true;
    chartAnalyzeApi.status().then((s) => { if (live) setEngine(s.engine); }, () => undefined);
    return () => { live = false; };
  }, [features.loaded, chartOn]);
  const canAttach = chartOn || (engine !== null && !engine.installed && engineAudience(engine) === "admin");
  const images = useRef<string[]>([]);
  useEffect(() => () => { for (const url of images.current) URL.revokeObjectURL(url); }, []);

  // CU6: the conversation the server kept for this person comes back after a reload (read once, before any new turn).
  useEffect(() => {
    let live = true;
    chatApi.history().then((h) => {
      if (!live) return;
      const restored = restoredTurns(h, t("chat.unreadable"));
      setKeep(h.keep);
      setTurns((now) => (now.length ? now : restored));
      next.current = Math.max(next.current, restored.length + 1);
    }).catch(() => { /* nothing kept or not reachable: the page starts empty */ });
    return () => { live = false; };
  }, [t]);

  const loadHome = useCallback(() => {
    chatApi.home().then((h) => { setHome(h); setHomeError(null); }).catch((e: unknown) => setHomeError(friendlyError(e, t)));
  }, [t]);
  useEffect(() => { loadHome(); }, [loadHome]);

  // Settings redesign A: the page reads the AI state again after the panel connected or removed the key.
  useEffect(() => {
    if (!oneKey) return;
    window.addEventListener(AI_CONNECTION_CHANGED_EVENT, loadHome);
    return () => window.removeEventListener(AI_CONNECTION_CHANGED_EVENT, loadHome);
  }, [oneKey, loadHome]);
  const ownKey = oneKey && !!home?.ai.ready && !!home.ai.own_key;
  useEffect(() => {
    if (!ownKey) { setAiLabel(null); return; }
    let live = true;
    api.aiConnection().then((c) => { if (live) setAiLabel(c.connected ? c.label : null); }).catch(() => { if (live) setAiLabel(null); });
    return () => { live = false; };
  }, [ownKey]);

  async function ask(question: string) {
    const id = next.current++;
    setTurns((all) => withTurn(all, { id, question, answer: null, error: null }, home?.keep ?? keep));
    setDraft("");
    setBusy(true);
    try {
      const answer: ChatAnswer = await chatApi.ask(question);
      setTurns((all) => settled(all, id, answer, null));
    } catch (e) {
      setTurns((all) => settled(all, id, null, friendlyError(e, t) || t("chat.failed")));
    } finally {
      setBusy(false);
      document.querySelector(`[data-turn='${id}']`)?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    }
  }

  // M59 CR-2: an attached chart goes through the SAME chart_analyze pipeline as the Analyze a chart page (store, then
  // analyse, Image only); the result is this turn; then the Copilot's own reading of the symbol the picture shows.
  async function attachChart(source: ChartSource) {
    const id = next.current++;
    setTurns((all) => withTurn(all, { id, question: chartQuestion(source), answer: null, error: null, chart: { status: "working", step: "adding" } }, home?.keep ?? keep));
    setBusy(true);
    const show = (chart: Parameters<typeof withChart>[2]) => setTurns((all) => withChart(all, id, chart));
    try {
      const status = await chartAnalyzeApi.status();
      if (!status.engine.installed) { show({ status: "unavailable", engine: status.engine }); return; }
      const item = await chartAnalyzeApi.add(source.kind === "file" ? { file: source.file, fileName: source.name } : { link: source.link }, "image_only");
      show({ status: "working", step: "analysing" });
      const done = await chartAnalyzeApi.analyze(item.id);
      if (done.status !== "done" || !done.result) { show({ status: "failed", message: done.error ?? t("chat.chartFailed") }); return; }
      let imageSrc: string | null = null;
      try { imageSrc = URL.createObjectURL(await chartAnalyzeApi.image(done.id)); images.current.push(imageSrc); } catch { /* the reading shows without the picture */ }
      show({ status: "done", item: done, imageSrc });
      // ATP's own structure card for the picture's symbol: the rule answer only (wording=false) - no AI call here; the
      // AI words come only when the user taps "Explain in words" (an ordinary question of his own)
      const question = readingQuestion(done);
      if (question) {
        const answer = await chatApi.askRules(question).catch(() => null);
        if (answer) setTurns((all) => settled(all, id, answer, null));
      }
    } catch (e) {
      if (isFeatureOff(e)) show({ status: "unavailable", engine: { installed: false } });
      else show({ status: "failed", message: apiFriendlyError(e) || t("chat.failed") });
    } finally {
      setBusy(false);
      document.querySelector(`[data-turn='${id}']`)?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    }
  }

  return (
    <>
      <PageIntro id="copilot-chat" title={t("chat.introTitle")} lines={[t("chat.intro1"), t("chat.intro2")]} />
      <ChatView home={home} homeError={homeError} turns={turns} draft={draft} busy={busy} placement={placement} onDraft={setDraft} onAsk={(q) => void ask(q)}
        strip={strip} keyOpen={keyOpen} onKeyOpen={setKeyOpen} onAttach={canAttach ? (source) => void attachChart(source) : undefined}
        connect={oneKey ? <ConnectAiPanelV2 /> : undefined} aiChip={aiLabel ? <ConnectAiChip label={aiLabel} /> : undefined} />
    </>
  );
}

/** Before the first-use acknowledgement: the top line only, nothing AI-written (P0.8-D). */
function Waiting() {
  const t = useCopilotT();
  return <p className="text-t20 font-semibold text-fg md:text-t24">{t("chat.topLine")}</p>;
}

export default function CopilotChatGate() {
  const t = useCopilotT();
  const { user } = useAuth();
  if (!user) return <p className="text-t14 text-fg-muted">{t("shell.signIn")}</p>;
  return <FirstUseModal placeholder={<Waiting />}><CopilotChatV2 /></FirstUseModal>;
}
