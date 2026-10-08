import { ArrowRight, Bot, Send, ShieldAlert, Square, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../../api/client";
import { Badge, Button, fieldClass } from "../../components/primitives";
import { useReducedMotion } from "../../theme";
import type { CopilotReply } from "../../types";
import { useAiTask } from "../aiTask";
import AiProgress from "../components/AiProgress";
import CostChip from "../components/CostChip";
import PrivacyNote from "../components/PrivacyNote";
import SourceChips, { type SourceItem } from "../components/SourceChips";
import { useCopilotT } from "../i18n";
import { copilotPath, tabForAction } from "../tabs";
import { Panel } from "./shared";

/**
 * Ask Copilot: one box for questions about the trader's own bot, positions, today's market and trading concepts
 * (the server's /ai/copilot router). Each answer shows what it was built from ("Based on"), whether the AI wrote it
 * (numbers checked against the facts) or the rules did - with the reason when an AI answer failed the numbers check -
 * and its metered cost. The reply appears progressively; Cancel stops a pending question, Stop shows the rest at once.
 */
interface Turn { id: number; role: "user" | "ai"; text: string; reply?: CopilotReply; shown: number }

/** The facts an answer stands on, as chips. */
export function sourcesOf(r: CopilotReply): SourceItem[] {
  const out: SourceItem[] = [];
  if (r.intent === "brief") out.push({ kind: "brief" });
  if (r.intent === "deployments") out.push({ kind: "brief" }, { kind: "deployments" });
  if (r.intent === "coach") out.push({ kind: "coach" });
  if (r.intent === "interview") out.push({ kind: "interview" });
  if (r.intent === "guide") out.push({ kind: "guide" });
  if (r.used_market_memory) out.push({ kind: "memory" });
  for (const c of (r.concepts ?? []).slice(0, 3)) out.push({ kind: "concept", label: c.title });
  out.push({ kind: "rules" });
  return out;
}

const WORD_MS = 22;

export default function AskCopilotTab() {
  const t = useCopilotT();
  const navigate = useNavigate();
  const reduced = useReducedMotion();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const task = useAiTask(t, 60_000);
  const bottom = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);
  const revealing = turns.some((x) => x.role === "ai" && x.shown < x.text.length);

  useEffect(() => { bottom.current?.scrollIntoView?.({ block: "nearest" }); }, [turns.length, task.state.phase]);

  // The progressive reveal of the newest answer (instant when motion is reduced).
  useEffect(() => {
    const pending = turns.find((x) => x.role === "ai" && x.shown < x.text.length);
    if (!pending) return;
    if (reduced) { setTurns((all) => all.map((x) => (x.id === pending.id ? { ...x, shown: x.text.length } : x))); return; }
    const h = window.setTimeout(() => {
      setTurns((all) => all.map((x) => {
        if (x.id !== pending.id) return x;
        const nextSpace = x.text.indexOf(" ", x.shown + 1);
        return { ...x, shown: nextSpace === -1 ? x.text.length : nextSpace };
      }));
    }, WORD_MS);
    return () => window.clearTimeout(h);
  }, [turns, reduced]);

  const stopReveal = () => setTurns((all) => all.map((x) => ({ ...x, shown: x.text.length })));

  async function ask(question: string) {
    const q = question.trim();
    if (q.length < 2 || task.busy) return;
    stopReveal();
    setText("");
    setTurns((all) => [...all, { id: nextId.current++, role: "user", text: q, shown: q.length }]);
    const reply = await task.run([t("ask.step.route"), t("ask.step.facts"), t("ask.step.write")], (signal) => api.aiCopilot(q, undefined, signal));
    if (reply) setTurns((all) => [...all, { id: nextId.current++, role: "ai", text: reply.answer, reply, shown: 0 }]);
  }

  const suggestions = ["skip", "risk", "regime", "month"].map((k) => t(`ask.suggest.${k}`));

  return (
    <div className="space-y-4" data-testid="tab-panel-ask">
      <Panel title={t("ask.title")} icon={<Bot size={15} />}>
        <p className="mb-3 text-xs text-fg-muted">{t("ask.intro")}</p>
        <div className="max-h-[480px] min-h-[200px] space-y-3 overflow-y-auto rounded-xl border border-border/70 bg-surface-2/30 p-3" aria-live="polite" data-testid="ask-log">
          {turns.length === 0 && (
            <div className="flex flex-col items-center gap-3 py-6 text-center">
              <Bot size={28} className="text-ai" aria-hidden />
              <p className="text-sm text-fg-muted">{t("ask.empty")}</p>
              <div className="flex flex-wrap justify-center gap-2">
                {suggestions.map((s) => (
                  <button key={s} onClick={() => void ask(s)} className="rounded-full border border-ai/40 bg-ai/10 px-3 py-1.5 text-xs text-fg hover:bg-ai/20" data-testid="ask-suggestion">{s}</button>
                ))}
              </div>
            </div>
          )}
          {turns.map((m) => (
            <div key={m.id} className={`flex gap-2 ${m.role === "user" ? "justify-end" : ""}`}>
              {m.role === "ai" && <Bot size={18} className="mt-1 shrink-0 text-ai" aria-hidden />}
              <div className={`max-w-[88%] space-y-2 rounded-2xl px-3.5 py-2.5 text-sm ${m.role === "user" ? "bg-ai/15 text-fg" : "copilot-glass text-fg"}`}>
                <p className="whitespace-pre-wrap leading-relaxed">{m.text.slice(0, m.shown)}{m.shown < m.text.length && <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-ai align-middle" aria-hidden />}</p>
                {m.reply && m.shown >= m.text.length && (
                  <div className="space-y-2 border-t border-border/60 pt-2" data-testid="ask-meta">
                    <SourceChips items={sourcesOf(m.reply)} />
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge tone={m.reply.source === "ai" ? "brand" : "neutral"}>{m.reply.source === "ai" ? t("ask.byAi") : t("ask.byRules")}</Badge>
                      {m.reply.note && (
                        <Badge tone="warn" className="max-w-full"><ShieldAlert size={11} className="mr-1 inline" />{t("ask.fallback")}</Badge>
                      )}
                      {m.reply.usage && <CostChip tokens={m.reply.usage.tokens_input + m.reply.usage.tokens_output} inr={m.reply.usage.cost_inr} />}
                      {m.reply.action && (
                        <Button size="sm" variant="ghost" icon={<ArrowRight size={13} />}
                                onClick={() => navigate(copilotPath(tabForAction(m.reply!.action.tab)), m.reply!.prompt ? { state: { prompt: m.reply!.prompt } } : undefined)}>
                          {m.reply.action.label}
                        </Button>
                      )}
                    </div>
                    {m.reply.note && <p className="text-[11px] text-fg-muted">{m.reply.note}</p>}
                  </div>
                )}
              </div>
              {m.role === "user" && <User size={18} className="mt-1 shrink-0 text-ai-2" aria-hidden />}
            </div>
          ))}
          <AiProgress state={task.state} onCancel={task.cancel} onRetry={() => { const last = [...turns].reverse().find((x) => x.role === "user"); if (last) void ask(last.text); }} />
          <div ref={bottom} />
        </div>
        <form className="mt-3 flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); void ask(text); }}>
          <label htmlFor="ask-input" className="sr-only">{t("ask.label")}</label>
          <textarea id="ask-input" rows={2} className={`${fieldClass} min-h-[44px] flex-1 resize-none py-2`} placeholder={t("ask.placeholder")} value={text} maxLength={1000}
                    onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void ask(text); } }} />
          {revealing ? (
            <Button type="button" icon={<Square size={13} />} onClick={stopReveal}>{t("ask.stop")}</Button>
          ) : (
            <Button type="submit" variant="primary" icon={<Send size={14} />} disabled={task.busy || text.trim().length < 2} data-testid="ask-send">{t("ask.send")}</Button>
          )}
        </form>
        {turns.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {suggestions.map((s) => <button key={s} onClick={() => void ask(s)} disabled={task.busy} className="rounded-full border border-border px-2.5 py-1 text-[11px] text-fg-muted hover:text-fg disabled:opacity-50">{s}</button>)}
          </div>
        )}
      </Panel>
      <PrivacyNote />
    </div>
  );
}
