import { SETTINGS_PATH } from "../../settings/paths";
import { KeyRound, SendHorizontal, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
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
import {
  chatApi, cleanQuestion, composerPlacement, jobCards, marketLineText, MAX_QUESTION, needsKey, settled, withTurn,
  type ChatAnswer, type ChatHome, type Turn,
} from "./model";

/**
 * Item 5 (owner's handover 5 C): the chat-first Copilot page, like a chat app. Top to bottom: "Ask me anything about the
 * market or your trades.", the no-key card ("Add a free AI key to start" -> Settings > AI) while no AI provider is set,
 * "Today's market in one line" (or the plain reason it is empty), the large question box with the starter buttons
 * (fixed above the bottom bar on a phone), the conversation, "What I can do" (the eight jobs; those not built yet say so
 * and link today's page), the older Copilot sections and the one-line disclaimer. Shown while `ui_v2` is on; with the flag
 * off today's seven-tab Copilot renders exactly as before. Nothing here places, changes or cancels an order (ADR-0006).
 */

const BUTTON = "inline-flex h-11 shrink-0 items-center justify-center gap-space-2 rounded-control bg-brand px-space-5 text-t14 font-semibold text-on-brand transition-colors duration-fast hover:bg-brand-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-50";
const CHIP = "inline-flex min-h-11 items-center rounded-pill border border-border bg-surface-1 px-space-3 text-left text-t13 text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";

export interface ChatViewProps {
  home: ChatHome | null;
  homeError: string | null;
  turns: readonly Turn[];
  draft: string;
  busy: boolean;
  placement: "bottom" | "inline";
  onDraft: (text: string) => void;
  onAsk: (question: string) => void;
  /** Settings redesign A (ai_one_key): the Connect AI panel, shown in place of the no-key card. */
  connect?: ReactNode;
  /** Settings redesign A: the small "AI connected" chip once the user's own key answers. */
  aiChip?: ReactNode;
}

export function Composer({ draft, busy, placement, onDraft, onAsk }: Pick<ChatViewProps, "draft" | "busy" | "placement" | "onDraft" | "onAsk">) {
  const t = useCopilotT();
  const submit = (e: FormEvent) => { e.preventDefault(); const q = cleanQuestion(draft); if (q && !busy) onAsk(q); };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); const q = cleanQuestion(draft); if (q && !busy) onAsk(q); }
  };
  const bottom = placement === "bottom";
  return (
    <form onSubmit={submit} data-composer={placement} data-tour="copilot-ask"
      className={bottom
        ? "fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-10 flex items-end gap-space-2 border-t border-subtle bg-surface-1 px-space-4 py-space-2 shadow-raised"
        : "flex items-end gap-space-2 rounded-panel border border-border bg-surface-1 p-space-3 shadow-card"}>
      <label className="sr-only" htmlFor="copilot-question">{t("chat.boxLabel")}</label>
      <textarea id="copilot-question" value={draft} maxLength={MAX_QUESTION} rows={bottom ? 1 : 3} onChange={(e) => onDraft(e.target.value)} onKeyDown={onKey}
        placeholder={t("chat.placeholder")} disabled={busy}
        className={`min-w-0 flex-1 resize-none rounded-control border border-border bg-surface-2 px-space-3 py-space-2 text-t15 text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand disabled:opacity-60 ${bottom ? "min-h-11" : "min-h-24"}`} />
      <button type="submit" className={BUTTON} disabled={busy || !cleanQuestion(draft)} aria-label={t("chat.ask")}>
        <SendHorizontal size={16} aria-hidden /><span className={bottom ? "sr-only" : ""}>{busy ? t("chat.asking") : t("chat.ask")}</span>
      </button>
    </form>
  );
}

export function ChatView({ home, homeError, turns, draft, busy, placement, onDraft, onAsk, connect, aiChip }: ChatViewProps) {
  const t = useCopilotT();
  const line = marketLineText(home);
  const starters = home?.starters ?? [];
  const empty = turns.length === 0;
  return (
    <div className="space-y-space-4" data-page="copilot-v2" data-placement={placement}>
      <header className="space-y-space-1">
        <h1 className="sr-only">{t("chat.title")}</h1>
        <p className="flex items-center gap-space-2 text-t20 font-semibold text-fg md:text-t24" data-top-line>
          <Sparkles size={20} aria-hidden className="shrink-0 text-brand" />{home?.top_line ?? t("chat.topLine")}
        </p>
        {aiChip && !needsKey(home) && <div className="pt-space-1">{aiChip}</div>}
      </header>

      {needsKey(home) && connect && <div data-connect-slot>{connect}</div>}

      {needsKey(home) && !connect && (
        <section className={`${CARD} flex flex-col gap-space-3 p-space-4 md:flex-row md:items-center md:justify-between`} data-no-key>
          <div className="flex min-w-0 items-start gap-space-3">
            <KeyRound size={18} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
            <div className="min-w-0">
              <h2 className="text-t15 font-semibold text-fg">{home?.ai.title ?? t("chat.noKeyTitle")}</h2>
              <p className="text-t14 text-fg-muted">{home?.ai.line ?? t("chat.noKeyLine")}</p>
            </div>
          </div>
          <Link to={home?.ai.settings ?? SETTINGS_PATH.ai} className={BUTTON}>{t("chat.noKeyAction")}</Link>
        </section>
      )}

      <section className={`${CARD} p-space-4`} data-tour="copilot-market" data-market-line={line.empty ? "empty" : "ok"}>
        <h2 className="text-t12 font-semibold uppercase tracking-wide text-fg-muted">{t("chat.marketLine")}</h2>
        <p className={`mt-space-1 text-t15 ${line.empty ? "text-fg-muted" : "text-fg"}`}>{homeError ?? (home ? line.text : t("chat.loading"))}</p>
      </section>

      {placement === "inline" && <Composer draft={draft} busy={busy} placement={placement} onDraft={onDraft} onAsk={onAsk} />}

      {starters.length > 0 && (
        <section aria-labelledby="copilot-starters">
          <h2 id="copilot-starters" className="mb-space-2 text-t13 font-semibold text-fg-muted">{t("chat.starters")}</h2>
          <div className="flex flex-wrap gap-space-2" data-starters>
            {starters.map((s) => <button key={s} type="button" className={CHIP} disabled={busy} onClick={() => onAsk(s)}>{s}</button>)}
          </div>
        </section>
      )}

      {!empty && (
        <ol className="space-y-space-4" aria-live="polite" data-conversation>
          {turns.map((turn) => (
            <li key={turn.id} className="space-y-space-2" data-turn={turn.id}>
              <p className="ml-auto w-fit max-w-full rounded-panel bg-surface-2 px-space-4 py-space-2 text-t14 text-fg">
                <span className="sr-only">{t("chat.youAsked")}: </span>{turn.question}
              </p>
              <div className={`${CARD} p-space-4`}>
                {turn.answer ? <AnswerV2 answer={turn.answer} onAsk={onAsk} />
                  : turn.error ? <p role="alert" className="text-t14 text-fg">{turn.error}</p>
                  : <p aria-busy="true" className="text-t14 text-fg-muted">{t("chat.asking")}</p>}
              </div>
            </li>
          ))}
        </ol>
      )}

      {home && (
        <section aria-labelledby="copilot-can-do" data-can-do data-tour="copilot-jobs">
          <h2 id="copilot-can-do" className="mb-space-2 text-t15 font-semibold text-fg">{t("chat.canDo")}</h2>
          <ul className="grid gap-space-3 sm:grid-cols-2 xl:grid-cols-4">
            {jobCards(home.jobs).map(({ job, link }) => (
              <li key={job.id} className={`${CARD} flex min-w-0 flex-col gap-space-2 p-space-4`} data-job={job.id} data-built={job.built}>
                <div className="flex items-center justify-between gap-space-2">
                  <h3 className="text-t14 font-semibold text-fg">{job.title}</h3>
                  <span className="shrink-0 rounded-pill border border-border px-space-2 text-t12 text-fg-muted">{job.built ? t("chat.ready") : t("chat.later")}</span>
                </div>
                <p className="flex-1 text-t13 text-fg-muted">{job.line}</p>
                {job.built ? (
                  <button type="button" className={`${CHIP} self-start`} disabled={busy} onClick={() => onAsk(job.starter)}>{job.starter}</button>
                ) : link ? (
                  <Link to={link.to} className="inline-flex min-h-11 items-center self-start text-t13 font-semibold text-brand hover:underline md:min-h-0">{link.label}</Link>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      )}

      <nav aria-labelledby="copilot-more" className="text-t13" data-tour="copilot-more">
        <h2 id="copilot-more" className="mb-space-1 font-semibold text-fg-muted">{t("chat.more")}</h2>
        <ul className="flex flex-wrap gap-x-space-4">
          {COPILOT_TABS.map((tab) => (
            <li key={tab}><Link to={copilotPath(tab)} className="inline-flex min-h-11 items-center text-brand hover:underline md:min-h-0">{t(`tabs.${tab}`)}</Link></li>
          ))}
        </ul>
      </nav>

      <p className="text-t12 text-fg-muted" data-disclaimer>{home?.disclaimer ?? t("chat.disclaimer")}</p>

      {placement === "bottom" && (
        <>
          <div aria-hidden className="h-20" />
          <Composer draft={draft} busy={busy} placement={placement} onDraft={onDraft} onAsk={onAsk} />
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

/** The page's state: the home read once, then each question as one turn (in memory only). */
export function CopilotChatV2() {
  const t = useCopilotT();
  const [home, setHome] = useState<ChatHome | null>(null);
  const [homeError, setHomeError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const next = useRef(1);
  const placement = usePlacement();
  const features = useFeatures();
  const oneKey = features.on("ui_v2") && features.on("ai_one_key");
  const [aiLabel, setAiLabel] = useState<string | null>(null);

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
    setTurns((all) => withTurn(all, { id, question, answer: null, error: null }));
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

  return (
    <>
      <PageIntro id="copilot-chat" title={t("chat.introTitle")} lines={[t("chat.intro1"), t("chat.intro2")]} />
      <ChatView home={home} homeError={homeError} turns={turns} draft={draft} busy={busy} placement={placement} onDraft={setDraft} onAsk={(q) => void ask(q)}
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
