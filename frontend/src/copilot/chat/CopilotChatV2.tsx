import { SETTINGS_PATH } from "../../settings/paths";
import { askContextLabel, type AskContext } from "../askContext";
import { ArrowDown, KeyRound, Paperclip, SendHorizontal, Sparkles, X } from "lucide-react";
import { IndexCell } from "../../dashboard/MarketStripV2";
import { ConnectLiveButton } from "../../ui/ConnectLive";
import { useNoBroker } from "../../ui/noBroker";
import { vixBand } from "../../dashboard/model";
import type { MarketStrip } from "../../dashboard/types";
import { stripDelay, usePolled, type Loadable } from "../../dashboard/useDashboardData";
import { Skeleton } from "../../components/primitives/Feedback";
import { LevelChip } from "../../ui/LevelChip";
import { Signed } from "../../ui/numbersFirst";
import { EMPTY, formatPrice, formatTimeIST } from "../../ui/format";
import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState, type ClipboardEvent, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { InPlaceLink } from "../../inplace/InPlaceLink";
import { useInPanel } from "../../inplace/inPanel";
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
import { ChatSessionsV2 } from "./ChatSessionsV2";
import { TicketPrepV2 } from "../ticket/TicketPrepV2";
import { useAiPrefs } from "../language/model";
import { VoiceButtonV2 } from "../language/VoiceButtonV2";
import { AttachMenu, imageFrom } from "./ChartAttachV2";
import {
  CHART_SUGGESTIONS, chartPickable, chartQuestion, chartQuestionFor, chatApi, cleanQuestion, composerPlacement, DEFAULT_KEEP, jobCards, marketLineText, MAX_QUESTION, needsKey, numbersCells, quickChips, readingQuestion,
  hybridPick, keptIsCurrent, restoredTurns, settled, stampText, withChart, withTurn,
  type ChartSource, type ChatAnswer, type ChatHome, type Conversation, type ConversationTarget, type Turn,
} from "./model";
import { chartAnalyzeApi, isFeatureOff } from "../../chartAnalyze/api";
import { engineAudience } from "../../chartAnalyze/engineWords";
import type { AnalyzeStatus } from "../../chartAnalyze/types";
import { friendlyError as apiFriendlyError } from "../../api/errors";
import { Details } from "../../ui/Details";
import { recentKeyFor, SymbolPicker } from "../../ui/SymbolPicker";
import { watchIstDate } from "./istDate";
import { MarketLineV2 } from "./MarketLineV2";
import { newlySettled, showJump, useChatScroll } from "./useChatScroll";
import { focusKind, useFocus, type Focus } from "./focus";

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
const CHIP = "inline-flex min-h-11 items-center rounded-pill border border-border bg-surface-1 px-space-3 text-left text-t13 text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:cursor-not-allowed disabled:opacity-50 md:min-h-9";
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
  /** M72 PR-3 (CP-3): what the next question is about ("Ask AI about this"), with a way to drop it. */
  about?: ReactNode;
  /** CH-1b: the chat bubble's compact chat - the conversation and the ask box only (no numbers strip, job buttons or
   * Advanced), the ask box kept at the foot of the sheet. */
  compact?: boolean;
  /** COP-2 B: this user's recent-symbols key for the Read chart picker (`recentKeyFor(user.id)`); null: none kept. */
  recentKey?: string | null;
  /** Owner 13 Nov: "Ask AI" on an item - the focused chat (its header, facts and own questions; the market folded). */
  focus?: Focus | null;
  /** Owner 13 Nov: leave the item for the general chat. */
  onLeaveFocus?: () => void;
}

export function Composer({ draft, busy, placement, onDraft, onAsk, placeholder, onAttach, docked = false }: Pick<ChatViewProps, "draft" | "busy" | "placement" | "onDraft" | "onAsk" | "onAttach"> & { placeholder?: string;
  /** Owner 13 Nov: the box sits in the chat's dock (pinned at the foot with the chips above it), not placed itself. */
  docked?: boolean }) {
  const t = useCopilotT();
  const aiPrefs = useAiPrefs();          // M72 PR-10: his voice choice (flag copilot_languages); null while off
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
  // Owner 13 Nov: Shift+Enter makes a new line and the box grows with it (up to its max height), like a chat app.
  const grow = (el: HTMLTextAreaElement) => { if (!docked) return; el.style.height = "auto"; el.style.height = `${Math.min(el.scrollHeight, 160)}px`; };
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (!draft && box.current) box.current.style.height = ""; }, [draft]);     // sent: back to one line
  // M60 default 1: the Ask box's attach button opens Analyze a chart (upload a chart or a TradingView snapshot link).
  const analyzeOn = useFeatures().on("chart_analyze");
  return (
    <form onSubmit={submit} data-composer={placement} data-tour="copilot-ask"
      className={docked ? "flex items-end gap-space-2"
        : bottom
        ? "fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-10 flex items-end gap-space-2 border-t border-subtle bg-surface-1 px-space-4 py-space-2 shadow-raised"
        : "sticky top-space-2 z-10 flex items-end gap-space-2 rounded-panel border border-border bg-surface-1 p-space-3 shadow-card"}>
      {/* M59 CR-2 attaches the chart in the chat itself when the Copilot can read charts; otherwise the M60 attach
          button opens Analyze a chart. Never both. */}
      {onAttach ? <AttachMenu onAttach={onAttach} disabled={busy} /> : analyzeOn && (
        <InPlaceLink to="/copilot/analyze" aria-label={t("v2tabs.attach")} title={t("v2tabs.attach")} data-attach-chart
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control border border-border bg-surface-2 text-fg-muted hover:bg-surface-3 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          <Paperclip size={18} aria-hidden />
        </InPlaceLink>
      )}
      <label className="sr-only" htmlFor="copilot-question">{t("chat.boxLabel")}</label>
      <textarea ref={box} id="copilot-question" value={draft} maxLength={MAX_QUESTION} rows={bottom || docked ? 1 : 2} onChange={(e) => { onDraft(e.target.value); grow(e.target); }} onKeyDown={onKey} onPaste={onPaste}
        placeholder={placeholder ?? t("chat.placeholder")} disabled={busy}
        className={`min-w-0 flex-1 resize-none rounded-control border border-border bg-surface-2 px-space-3 py-space-2 text-t15 text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand disabled:opacity-60 ${bottom || docked ? "max-h-40 min-h-11" : "min-h-16"}`} />
      <VoiceButtonV2 prefs={aiPrefs} draft={draft} onDraft={onDraft} disabled={busy} />
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
  const noBroker = useNoBroker();
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
      {/* M86 D3: no broker stored - the prices above are the last close; one in-place button for live prices. */}
      {noBroker && <ConnectLiveButton />}
    </section>
  );
}

/** Owner 13 Nov (chat redesign): "HH:MM" of a message sent or answered on this screen (IST). */
function clock(iso: string | undefined): string | null {
  if (!iso) return null;
  const text = formatTimeIST(iso, { suffix: false });
  return text === EMPTY ? null : text;
}

/** The Copilot's small round mark beside its messages. */
function Avatar() {
  return (
    <span aria-hidden data-copilot-avatar className="flex size-8 shrink-0 items-center justify-center rounded-full bg-ai-tint text-ai">
      <Sparkles size={16} />
    </span>
  );
}

/** Owner 13 Nov: the answer is on its way - three dots, said in words for a screen reader. */
export function TypingIndicator() {
  const t = useCopilotT();
  return (
    <p role="status" aria-busy="true" data-typing className="inline-flex min-h-9 items-center gap-space-1 rounded-panel bg-surface-2 px-space-3 text-fg-muted">
      <span className="sr-only">{t("chat.asking")}</span>
      <span aria-hidden className="flex gap-space-1">
        {[0, 1, 2].map((i) => <span key={i} className="size-2 animate-pulse rounded-full bg-current motion-reduce:animate-none" style={{ animationDelay: `${i * 160}ms` }} />)}
      </span>
      <span aria-hidden className="ml-space-1 text-t13">{t("chat.asking")}</span>
    </p>
  );
}

/** The focused chat's header: the item in plain words, then its facts as one line of chips (numbers first). */
export function FocusHeader({ focus, onLeave }: { focus: Focus; onLeave?: () => void }) {
  const t = useCopilotT();
  const TONE = { up: "bg-up-tint text-up", down: "bg-down-tint text-down", warn: "bg-warn-tint text-warn", neutral: "bg-surface-2 text-fg" } as const;
  return (
    <div className="min-w-0 flex-1 space-y-space-2" data-focus-header={focus.kind}>
      <div className="flex min-w-0 items-start gap-space-2">
        <h2 className="min-w-0 flex-1 text-t15 font-semibold text-fg" data-focus-title>{focus.title}</h2>
        {onLeave && (
          <button type="button" onClick={onLeave} aria-label={t("chat.focusLeave")} title={t("chat.focusLeave")} data-focus-leave
            className="inline-flex size-11 shrink-0 md:size-9 items-center justify-center rounded-pill text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
            <X size={16} aria-hidden />
          </button>
        )}
      </div>
      {focus.facts.length > 0 && (
        <ul className="flex flex-wrap gap-space-2" data-focus-facts>
          {focus.facts.map((f) => (
            <li key={f.key} className={`inline-flex min-h-7 items-center gap-space-1 rounded-pill px-space-3 text-t13 ${TONE[f.tone]}`} data-fact={f.key} data-tone={f.tone}>
              <span className={f.tone === "neutral" ? "text-fg-muted" : ""}>{f.label}</span>
              <span className="font-tabular font-semibold">{f.value}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Turns({ turns, busy, onAsk }: { turns: readonly Turn[]; busy: boolean; onAsk: (q: string) => void }) {
  const t = useCopilotT();
  return (
    <ol className="space-y-space-5" aria-live="polite" data-conversation>
      {turns.map((turn) => {
        const sent = turn.at ? `${t("chat.askedEarlier")}${stampText(turn.at) ? ` · ${stampText(turn.at)}` : ""}` : clock(turn.sentAt);
        const answered = clock(turn.answeredAt);
        return (
          <li key={turn.id} className="space-y-space-3" data-turn={turn.id}>
            <div className="flex flex-col items-end gap-space-1" data-msg="user">
              <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-panel rounded-br-sm bg-brand-tint px-space-4 py-space-2 text-t14 text-fg">
                <span className="sr-only">{t("chat.youAsked")}: </span>{turn.question}
              </p>
              {sent && <p className="text-t12 text-fg-muted" data-turn-at={turn.at ? true : undefined} data-sent-at={turn.at ? undefined : true}>{sent}</p>}
            </div>
            <div className="min-w-0 space-y-space-2" data-msg="copilot">
              <p className="flex items-center gap-space-2 text-t12 text-fg-muted" data-msg-head>
                <Avatar />
                <span className="font-semibold text-fg">{t("chat.copilotName")}</span>{answered && <span>{answered}</span>}
              </p>
              <div className="min-w-0">
                {turn.chart ? (
                  <>
                    {/* M59 CR-2: the attached chart's analysis, then the Copilot's own structure card for its symbol (CR-1) */}
                    <div className={`${CARD} p-space-4`} data-chart-card>
                      <Suspense fallback={<TypingIndicator />}><ChartTurnV2 chart={turn.chart} /></Suspense>
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
                ) : turn.answer ? (
                  <div className={`${CARD} p-space-3 md:p-space-4`} data-answer-card>
                    <AnswerV2 answer={turn.answer} onAsk={onAsk} disclaimer={false} />
                    {/* M72 PR-9: the order ticket prepared from his own message (never on a refused question) */}
                    {!turn.answer.refused && <TicketPrepV2 question={turn.question} />}
                  </div>
                ) : turn.error ? <p role="alert" className="rounded-panel border border-subtle bg-surface-1 px-space-3 py-space-2 text-t14 text-fg">{turn.error}</p>
                  : <TypingIndicator />}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function ChatView({ home, homeError, turns, draft, busy, placement, onDraft, onAsk, strip = { status: "loading" }, connect, aiChip, keyOpen = false, onKeyOpen, onAttach, about, compact = false, recentKey = null, focus = null, onLeaveFocus }: ChatViewProps) {
  const t = useCopilotT();
  // COP-2 B (owner 11 Nov): "Read chart" asks which chart first - any share or index the chart reader reads.
  const [picking, setPicking] = useState(false);
  const pickerId = useId();
  useEffect(() => {
    if (!picking) return;
    const field = document.getElementById(pickerId);
    field?.focus({ preventScroll: true });
    field?.scrollIntoView?.({ block: "center", behavior: "smooth" });              // room for the list above the phone's ask box
  }, [picking, pickerId]);
  const noBroker = useNoBroker();
  const line = marketLineText(home);
  const chips = home && !focus ? quickChips(home.jobs) : [];
  const empty = turns.length === 0;
  const aiOff = needsKey(home);
  // Owner 13 Nov: the conversation scrolls to its newest message; an answer arriving is brought into view only while
  // he is at the latest, else a "Jump to latest" button shows.
  const lastId = turns.length ? turns[turns.length - 1].id : null;
  const scroll = useChatScroll(lastId);
  const settledIds = useRef(new Set<number>());
  useEffect(() => { for (const id of newlySettled(turns, settledIds.current)) scroll.reveal(id); }, [turns, scroll]);
  const docked = placement === "bottom" && !compact;
  const composer = <Composer draft={draft} busy={busy} placement={placement} onDraft={onDraft} onAsk={onAsk} onAttach={onAttach} docked
    placeholder={focus ? t("chat.focusPlaceholder") : placement === "bottom" || compact ? t("chat.placeholderShort") : home?.top_line} />;
  const chipButton = (c: { id: string; label: string; question: string }, wide: boolean) => (
    c.id === "chart" ? (
      <button key={c.id} type="button" className={`${CHIP} ${wide ? "justify-center font-medium sm:justify-start" : "shrink-0 whitespace-nowrap font-medium"}`} disabled={busy} aria-expanded={picking}
        onClick={() => setPicking((p) => !p)} data-quick={c.id}>{c.label}</button>
    ) : (
      <button key={c.id} type="button" className={`${CHIP} ${wide ? "justify-center font-medium sm:justify-start" : "shrink-0 whitespace-nowrap font-medium"}`} disabled={busy} onClick={() => onAsk(c.question)} data-quick={c.id}>{c.label}</button>
    )
  );
  const suggestions = focus?.suggestions ?? [];
  const suggestionList = (wide: boolean) => (
    <nav aria-label={t("chat.quick")} data-focus-suggestions className={wide ? "flex flex-col gap-space-2" : "flex gap-space-2 overflow-x-auto pb-space-1"}>
      {suggestions.map((q) => (
        <button key={q} type="button" disabled={busy} onClick={() => onAsk(q)} data-suggestion
          className={`${CHIP} ${wide ? "w-full" : "max-w-[80%] shrink-0 truncate"}`} title={q}>{q}</button>
      ))}
    </nav>
  );
  return (
    // Owner 13 Nov: the chat fills its panel / sheet / page, so the input sits at the foot even before a first question.
    <div className={`flex min-w-0 flex-col gap-space-3 ${docked ? "" : "min-h-[calc(100dvh-12rem)]"}`} data-page="copilot-v2" data-placement={placement} data-focus={focus?.kind}>
      <header className="flex min-w-0 items-start justify-between gap-space-2" data-chat-header>
        <h1 className="sr-only">{t("chat.title")}</h1>
        <p className="sr-only" data-top-line>{home?.top_line ?? t("chat.topLine")}</p>
        {focus ? <FocusHeader focus={focus} onLeave={onLeaveFocus} /> : (
          // Owner 13 Nov: the one-line summary wraps - never cut mid-sentence.
          <p className={`min-w-0 flex-1 break-words text-t13 ${line.empty ? "text-fg-muted" : "text-fg"}`} data-market-line={line.empty ? "empty" : "ok"}>
            {homeError ?? (home ? line.text : t("chat.loading"))}
          </p>
        )}
        <div className="shrink-0">
          {home && !aiOff && aiChip ? aiChip : home && !aiOff ? (
            <InPlaceLink to={home.ai.settings ?? SETTINGS_PATH.ai} className={SMALL_CHIP} data-ai-state="on">
              <Sparkles size={14} aria-hidden className="text-up" />{t("chat.aiOn")}
            </InPlaceLink>
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
            <InPlaceLink to={home.ai.settings ?? SETTINGS_PATH.ai} className={SMALL_CHIP} data-connect>{t("chat.connect")}</InPlaceLink>
          )}
        </div>
      )}

      {aiOff && connect && keyOpen && <div data-connect-slot>{connect}</div>}

      {/* Owner 13 Nov: the market numbers as one compact, foldable line; a focused chat starts with it folded. */}
      {!compact && (
        <MarketLineV2 strip={strip} fii={home?.market_line.fii_net_crore ?? null} fiiAsOf={home?.market_line.fii_as_of ?? null} nowMs={Date.now()}
          startFolded={focus ? true : undefined} />
      )}
      {!compact && !focus && noBroker && <ConnectLiveButton />}

      {/* CU2: every job and the older Copilot pages fold under one closed "Advanced" (nothing removed; each still opens). */}
      {!compact && !focus && <div data-tour="copilot-more"><Details id="copilot.chat.advanced" label="Advanced">
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
                    <InPlaceLink to={link.to} className="inline-flex min-h-11 shrink-0 items-center self-start text-t13 font-semibold text-brand hover:underline md:min-h-0">{link.label}</InPlaceLink>
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
              <li key={tab}><InPlaceLink to={copilotPath(tab)} className="inline-flex min-h-11 items-center text-brand hover:underline md:min-h-0">{t(`tabs.${tab}`)}</InPlaceLink></li>
            ))}
          </ul>
        </nav>
      </Details></div>}

      <section className="min-w-0 flex-1" data-conversation-area aria-label={t("chat.conversations")}>
        {empty ? (
          // Owner 13 Nov: the empty state - a short welcome and the four chips (or, about an item, its own questions).
          <div className="flex flex-col items-center gap-space-3 py-space-6 text-center" data-chat-welcome>
            <Avatar />
            {!focus && <p className="text-t15 font-semibold text-fg">{t("chat.welcomeTitle")}</p>}
            <p className="max-w-md text-t13 text-fg-muted">{focus ? t("chat.focusWelcome") : t("chat.welcomeLine")}</p>
            {!compact && chips.length > 0 && (
              <nav aria-label={t("chat.quick")} data-quick-chips data-tour="copilot-jobs" className="grid w-full max-w-md grid-cols-2 gap-space-2">
                {chips.map((c) => chipButton(c, true))}
              </nav>
            )}
            {focus && suggestions.length > 0 && <div className="w-full max-w-md text-left">{suggestionList(true)}</div>}
          </div>
        ) : <Turns turns={turns} busy={busy} onAsk={onAsk} />}
        {!compact && picking && (
          <div className="mt-space-3 flex min-w-0 flex-wrap items-end gap-space-2 rounded-panel border border-subtle bg-surface-1 p-space-3" data-chart-picker>
            <div className="min-w-0 flex-1 space-y-space-1">
              <label htmlFor={pickerId} className="block text-t13 font-medium text-fg">{t("chat.pickChart")}</label>
              <SymbolPicker id={pickerId} label={t("chat.pickChart")} value="" exchange="NSE" storageKey={recentKey} suggestions={CHART_SUGGESTIONS}
                rowFilter={chartPickable} placeholder={t("chat.pickChartPlaceholder")} className="min-w-0 w-full"
                onPick={(p) => { setPicking(false); onAsk(chartQuestionFor(p.symbol)); }} />
            </div>
            <button type="button" className={SMALL_CHIP} onClick={() => setPicking(false)} data-chart-picker-close>{t("chat.pickChartClose")}</button>
          </div>
        )}
        <div ref={scroll.end} aria-hidden className="h-px" data-chat-end />
      </section>

      <div data-composer-dock={docked ? "fixed" : "sticky"}
        className={docked
          ? "fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-10 space-y-space-2 border-t border-subtle bg-surface-1 px-space-4 pb-space-2 pt-space-2 shadow-raised"
          // -bottom-4 + its own bottom padding: a panel's scroll area keeps 1rem of padding under the sticky box, where
          // the conversation would otherwise show through below the input.
          : "sticky -bottom-4 z-10 -mx-space-1 space-y-space-2 border-t border-subtle bg-surface-1 px-space-1 pb-space-4 pt-space-2"}>
        {showJump(scroll.atLatest, turns.length) && (
          <div className="pointer-events-none absolute inset-x-0 -top-space-12 flex justify-center">
            <button type="button" onClick={() => scroll.jump("smooth")} data-jump-latest
              className="pointer-events-auto inline-flex min-h-11 items-center md:min-h-9 gap-space-1 rounded-pill border border-border bg-surface-1 px-space-3 text-t13 font-medium text-fg shadow-raised hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
              <ArrowDown size={14} aria-hidden />{t("chat.jumpLatest")}
            </button>
          </div>
        )}
        {!focus && about}
        {!empty && !compact && chips.length > 0 && (
          <nav aria-label={t("chat.quick")} data-quick-chips data-tour="copilot-jobs" className="flex gap-space-2 overflow-x-auto pb-space-1">
            {chips.map((c) => chipButton(c, false))}
          </nav>
        )}
        {!empty && focus && suggestions.length > 0 && suggestionList(false)}
        {compact && <div data-compact-composer>{composer}</div>}
        {!compact && composer}
        <p className="text-t12 text-fg-muted" data-disclaimer>{home?.disclaimer ?? t("chat.disclaimer")}</p>
      </div>
      {docked && <div aria-hidden className="h-48" />}
    </div>
  );
}

function usePlacement(): "bottom" | "inline" {
  // M75 NAV-2: in an in-place panel the composer sits in the panel (never fixed to the bottom of the screen).
  const inPanel = useInPanel();
  const read = () => composerPlacement(typeof window === "undefined" ? 1440 : window.innerWidth);
  const [placement, setPlacement] = useState(read);
  useEffect(() => {
    const onResize = () => setPlacement(read());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return inPanel ? "inline" : placement;
}

/** CU2: the numbers strip's prices - the Today strip's own polled read (GET /api/market/strip, with SENSEX), again
 * after the server's `refresh_seconds`, paused while the browser tab is hidden. Read-only. */
const readNumbers = () => api.marketStrip({ sensex: true });

/** The page's state: the home read once, the kept conversation (CU6), then each question as one turn. */
/** M72 PR-3: the "About NIFTY · 15min" chip above the questions, with its remove button. */
export function AboutChip({ ctx, onClear }: { ctx: AskContext; onClear: () => void }) {
  const t = useCopilotT();
  return (
    <p className="flex min-w-0 items-center gap-space-2 text-t13" data-ask-context={ctx.object?.kind ?? "symbol"}>
      <span className="text-fg-muted">{t("chat.about")}</span>
      <span className="inline-flex min-h-9 min-w-0 items-center gap-space-1 rounded-pill border border-border bg-surface-2 pl-space-3 pr-space-1 text-fg">
        <span className="truncate font-medium">{askContextLabel(ctx)}</span>
        <button type="button" onClick={onClear} aria-label={t("chat.aboutClear")} title={t("chat.aboutClear")} data-ask-context-clear
          className="inline-flex size-9 shrink-0 items-center justify-center rounded-pill text-fg-muted hover:bg-surface-1 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          <X size={14} aria-hidden />
        </button>
      </span>
    </p>
  );
}

export function CopilotChatV2({ context = null, starters = [], compact = false, recentKey = null }: { context?: AskContext | null; starters?: readonly string[]; compact?: boolean; recentKey?: string | null }) {
  const t = useCopilotT();
  // M72 PR-3 (F8): the context the page was opened with rides on every question until the user drops it
  const contextKey = JSON.stringify(context);
  const [about, setAbout] = useState<AskContext | null>(context);
  useEffect(() => setAbout(JSON.parse(contextKey) as AskContext | null), [contextKey]);
  // Owner 13 Nov: "Ask AI" on an item (a position, an order, a signal, a scan result...) opens a FOCUSED chat - a new
  // conversation about that item only; the earlier one stays in the Conversations list. The bubble's compact chat and
  // the Copilot opened from the top bar (no item) are the general chat, as before.
  const focused = !compact && focusKind(context) !== null;
  const focus = useFocus(compact ? null : about);
  const [home, setHome] = useState<ChatHome | null>(null);
  const [homeError, setHomeError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [keep, setKeep] = useState(DEFAULT_KEEP);
  const next = useRef(1);
  // Owner 8 Nov: the asked turn is brought into view as soon as it is on screen; owner 13 Nov (chat redesign): ChatView
  // scrolls to the newest message (useChatScroll) and brings an answer into view while he is at the latest.
  // CH-2: the conversation on screen (null: none yet, or "New chat" pressed - the next question starts a new one)
  const [convo, setConvo] = useState<Conversation | null>(null);
  const fresh = useRef(false);
  const convoRef = useRef<Conversation | null>(null);
  convoRef.current = convo;
  const target = (): ConversationTarget => (fresh.current || !convo ? { newChat: fresh.current } : { sessionId: convo.id });
  const opened = (answer: ChatAnswer) => { if (answer.conversation) { setConvo(answer.conversation); fresh.current = false; } };
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

  // CU6: the conversation the server kept for this person comes back after a reload (read once, before any new turn) -
  // owner 9 Nov: only when it is from today (keptIsToday); COP-2 A (owner 11 Nov): and only while its newest chart
  // answer is current (no newer close stored, said by the server). Otherwise the screen starts fresh, the first question
  // starts a new chat, and the conversation stays in the Conversations list (it opens from there as it was).
  useEffect(() => {
    let live = true;
    chatApi.history().then((h) => {
      if (!live) return;
      if (focused || (h.turns.length > 0 && !keptIsCurrent(h))) {      // an item asked about, or an old chat: start a new one
        setKeep(h.keep);
        if (!convoRef.current) fresh.current = true;
        return;
      }
      const restored = restoredTurns(h, t("chat.unreadable"));
      setKeep(h.keep);
      setTurns((now) => (now.length ? now : restored));
      setConvo((now) => now ?? h.session ?? null);
      next.current = Math.max(next.current, restored.length + 1);
    }).catch(() => { /* intentional: nothing kept or not reachable: the page starts empty */ });
    return () => { live = false; };
  }, [t, focused]);

  // Owner 13 Nov: another item asked about while the chat is open (the panel re-used) starts its own new chat too.
  const firstContext = useRef(contextKey);
  useEffect(() => {
    if (contextKey === firstContext.current) return;
    firstContext.current = contextKey;
    if (!focused) return;
    fresh.current = true;
    setConvo(null);
    setTurns([]);
    setDraft("");
  }, [contextKey, focused]);

  // COP-2 A (owner 11 Nov): when the India date changes while the page stays open (midnight, or the tab coming back on a
  // later date), the conversation on screen goes away and the next question starts a new chat; it stays in the list.
  useEffect(() => watchIstDate(() => {
    fresh.current = true;
    setConvo(null);
    setTurns([]);
  }), []);

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
    setTurns((all) => withTurn(all, { id, question, answer: null, error: null, sentAt: new Date().toISOString() }, home?.keep ?? keep));
    setDraft("");
    setBusy(true);
    try {
      const answer: ChatAnswer = await chatApi.ask(question, about, target());
      opened(answer);
      setTurns((all) => settled(all, id, answer, null));
    } catch (e) {
      setTurns((all) => settled(all, id, null, friendlyError(e, t) || t("chat.failed")));
    } finally {
      setBusy(false);
    }
  }

  // M59 CR-2: an attached chart goes through the SAME chart_analyze pipeline as the Analyze a chart page (store, then
  // analyse, Image only); the result is this turn; then the Copilot's own reading of the symbol the picture shows.
  async function attachChart(source: ChartSource) {
    const id = next.current++;
    setTurns((all) => withTurn(all, { id, question: chartQuestion(source), answer: null, error: null, chart: { status: "working", step: "adding" }, sentAt: new Date().toISOString() }, home?.keep ?? keep));
    setBusy(true);
    const show = (chart: Parameters<typeof withChart>[2]) => setTurns((all) => withChart(all, id, chart));
    try {
      const status = await chartAnalyzeApi.status();
      if (!status.engine.installed) { show({ status: "unavailable", engine: status.engine }); return; }
      // M72 PR-5 (CP-4): "Advanced analysis" (ATP's reading beside the picture) when it is on and the page the user asked
      // from names the symbol and timeframe; otherwise AI only, as before
      const pick = status.modes.hybrid ? hybridPick(about) : null;
      const added = source.kind === "file" ? { file: source.file, fileName: source.name } : { link: source.link };
      const item = pick ? await chartAnalyzeApi.add(added, "hybrid", pick) : await chartAnalyzeApi.add(added, "image_only");
      show({ status: "working", step: "analysing" });
      const done = await chartAnalyzeApi.analyze(item.id);
      if (done.status !== "done" || !(done.result || done.hybrid)) { show({ status: "failed", message: done.error ?? t("chat.chartFailed") }); return; }
      let imageSrc: string | null = null;
      try { imageSrc = URL.createObjectURL(await chartAnalyzeApi.image(done.id)); images.current.push(imageSrc); } catch { /* intentional: the reading shows without the picture */ }
      let readingSrc: string | null = null;
      if (done.hybrid) {
        try { readingSrc = URL.createObjectURL(await chartAnalyzeApi.readingPng(done.id)); images.current.push(readingSrc); } catch { /* intentional: the reading's table shows without its chart */ }
      }
      show({ status: "done", item: done, imageSrc, readingSrc });
      // ATP's own structure card for the picture's symbol: the rule answer only (wording=false) - no AI call here; the
      // AI words come only when the user taps "Explain in words" (an ordinary question of his own)
      const question = readingQuestion(done);
      if (question) {
        const answer = await chatApi.askRules(question, target()).catch(() => null);   // intentional: the optional structure card is skipped; the reading above already shows
        if (answer) { opened(answer); setTurns((all) => settled(all, id, answer, null)); }
      }
    } catch (e) {
      if (isFeatureOff(e)) show({ status: "unavailable", engine: { installed: false } });
      else show({ status: "failed", message: apiFriendlyError(e) || t("chat.failed") });
    } finally {
      setBusy(false);
    }
  }

  // CH-2: "New chat" clears the screen; an earlier conversation is read back from the server.
  function newChat() {
    fresh.current = true;
    setConvo(null);
    setTurns([]);
    setDraft("");
  }
  async function openConversation(sessionId: number) {
    try {
      const h = await chatApi.history(sessionId);
      const restored = restoredTurns(h, t("chat.unreadable"));
      fresh.current = false;
      setConvo(h.session ?? null);
      setKeep(h.keep);
      setTurns(restored);
      next.current = Math.max(next.current, restored.length + 1);
    } catch { /* intentional: the list stays; the conversation on screen is unchanged */ }
  }

  return (
    <>
      {!compact && <PageIntro id="copilot-chat" title={t("chat.introTitle")} lines={[t("chat.intro1"), t("chat.intro2")]} />}
      <ChatSessionsV2 current={convo} busy={busy} onNew={newChat} onOpen={(sid) => void openConversation(sid)}
        onRenamed={(c) => setConvo((now) => (now?.id === c.id ? c : now))} onDeleted={(sid) => { if (convo?.id === sid) newChat(); }} />
      {/* CH-1: the chat bubble's ready questions about the page's symbol, until the first question is asked */}
      {starters.length > 0 && turns.length === 0 && (
        <div className="flex flex-wrap gap-space-2" data-chat-starters>
          {starters.map((q) => (
            <button key={q} type="button" disabled={busy} onClick={() => void ask(q)}
              className="inline-flex min-h-11 items-center rounded-pill border border-border bg-surface-1 px-space-3 text-left text-t13 text-fg hover:bg-surface-2 md:min-h-9">{q}</button>
          ))}
        </div>
      )}
      <ChatView home={home} homeError={homeError} turns={turns} draft={draft} busy={busy} placement={compact ? "inline" : placement} compact={compact} recentKey={recentKey} onDraft={setDraft} onAsk={(q) => void ask(q)}
        strip={strip} keyOpen={keyOpen} onKeyOpen={setKeyOpen} onAttach={canAttach ? (source) => void attachChart(source) : undefined}
        connect={oneKey ? <ConnectAiPanelV2 /> : undefined} aiChip={aiLabel ? <ConnectAiChip label={aiLabel} /> : undefined}
        about={about ? <AboutChip ctx={about} onClear={() => setAbout(null)} /> : undefined}
        focus={focus} onLeaveFocus={() => setAbout(null)} />
    </>
  );
}

/** Before the first-use acknowledgement: the top line only, nothing AI-written (P0.8-D). */
function Waiting() {
  const t = useCopilotT();
  return <p className="text-t20 font-semibold text-fg md:text-t24">{t("chat.topLine")}</p>;
}

export default function CopilotChatGate({ context = null, starters = [], compact = false }: { context?: AskContext | null; starters?: readonly string[]; compact?: boolean }) {
  const t = useCopilotT();
  const { user } = useAuth();
  if (!user) return <p className="text-t14 text-fg-muted">{t("shell.signIn")}</p>;
  return <FirstUseModal placeholder={<Waiting />}><CopilotChatV2 context={context} starters={starters} compact={compact} recentKey={recentKeyFor(user.id)} /></FirstUseModal>;
}
