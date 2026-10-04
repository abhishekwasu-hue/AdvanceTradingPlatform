import { ArrowRight, Bot, Send, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { CopilotReply } from "../types";

/**
 * Phase AV: one box for everything. The message is routed (strategy / my trades / why no trade /
 * today's plan / any concept) and answered from the platform's own facts - by the AI when an AI
 * key is set in Settings. Each reply offers the part of the page that can act on it.
 */
const SUGGESTIONS: { mr: string; en: string }[] = [
  { mr: "आज काय करू?", en: "What should I do today?" },
  { mr: "मला intraday strategy सांगा", en: "Build me an intraday strategy" },
  { mr: "माझे trades कसे आहेत?", en: "How are my trades?" },
  { mr: "माझा autopilot trade का करत नाही?", en: "Why is my autopilot not trading?" },
  { mr: "Stop-loss कुठे ठेवावा?", en: "Where should I put my stop-loss?" },
];
const INTENT_LABEL: Record<CopilotReply["intent"], string> = {
  brief: "आजचा plan", coach: "Trade coach", deployments: "Deployments", interview: "Strategy", guide: "मार्गदर्शक",
};

interface Turn { q: string; a?: CopilotReply; error?: string }

export default function CopilotAsk({ lang, onAction }: { lang: "en" | "mr"; onAction: (reply: CopilotReply) => void }) {
  const [text, setText] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest" }); }, [turns]);

  async function ask(q: string) {
    const question = q.trim();
    if (question.length < 2 || busy) return;
    setText(""); setBusy(true);
    setTurns((t) => [...t, { q: question }]);
    try {
      const a = await api.aiCopilot(question);
      setTurns((t) => t.map((turn, i) => (i === t.length - 1 ? { ...turn, a } : turn)));
      if (a.intent === "interview") onAction(a);   // building a strategy is a flow, not an answer: open it straight away
    } catch (e) {
      const msg = String(e).replace(/^Error:\s*/, "");
      setTurns((t) => t.map((turn, i) => (i === t.length - 1 ? { ...turn, error: msg } : turn)));
    } finally { setBusy(false); }
  }

  return (
    <div className="rounded-2xl border border-purple-500/30 bg-gradient-to-br from-purple-500/[0.10] via-panel to-sky-500/[0.06] p-4 shadow-card">
      <div className="mb-2 flex items-center gap-2">
        <div className="rounded-lg bg-purple-500/20 p-1.5"><Bot size={18} className="text-purple-200" /></div>
        <div>
          <div className="text-sm font-extrabold text-slate-50">Copilot ला विचारा · Ask your Copilot</div>
          <div className="text-[11px] text-muted">आजचा plan, strategy, तुमच्या trades मधल्या चुका, autopilot trade का करत नाही, किंवा कोणतीही संकल्पना - मराठी किंवा English.</div>
        </div>
      </div>
      {turns.length > 0 && (
        <div className="mb-3 max-h-[340px] space-y-3 overflow-y-auto pr-1">
          {turns.map((t, i) => (
            <div key={i} className="space-y-1.5">
              <div className="ml-auto w-fit max-w-[85%] rounded-xl rounded-br-sm bg-purple-600/30 px-3 py-1.5 text-sm text-slate-50">{t.q}</div>
              {!t.a && !t.error && <div className="text-xs text-muted">विचार करतो आहे…</div>}
              {t.error && <div className="text-xs text-danger">{t.error}</div>}
              {t.a && (
                <div className="max-w-[92%] rounded-xl rounded-bl-sm border border-border bg-panel2/70 px-3 py-2 text-sm text-slate-100">
                  <div className="mb-1 flex flex-wrap items-center gap-1.5 text-[10px] uppercase tracking-wider">
                    <span className="rounded border border-purple-400/40 px-1.5 py-0.5 text-purple-200">{INTENT_LABEL[t.a.intent]}</span>
                    <span className={`rounded border px-1.5 py-0.5 ${t.a.source === "ai" ? "border-sky-400/40 text-sky-200" : "border-border text-muted"}`}>
                      {t.a.source === "ai" ? "AI" : "नियम · rules"}
                    </span>
                  </div>
                  <div className="whitespace-pre-wrap leading-relaxed">{t.a.answer}</div>
                  {t.a.coach && t.a.coach.flags.length > 0 && (
                    <ul className="mt-1.5 space-y-0.5 text-xs">
                      {t.a.coach.flags.filter((f) => f.severity !== "good").slice(0, 3).map((f) => (
                        <li key={f.id} className="text-slate-300"><span className={f.severity === "high" ? "text-rose-300" : "text-amber-300"}>●</span> {f.tip}</li>
                      ))}
                    </ul>
                  )}
                  {t.a.note && <div className="mt-1 text-[11px] text-muted">{t.a.note}</div>}
                  <button onClick={() => onAction(t.a!)} className="mt-2 inline-flex items-center gap-1 rounded-md border border-purple-400/40 px-2 py-1 text-xs font-semibold text-purple-100 hover:bg-purple-500/15">
                    {t.a.action.label} <ArrowRight size={12} />
                  </button>
                </div>
              )}
            </div>
          ))}
          <div ref={bottom} />
        </div>
      )}
      <div className="mb-2 flex flex-wrap gap-1.5">
        {SUGGESTIONS.map((s) => (
          <button key={s.en} disabled={busy} onClick={() => void ask(lang === "mr" ? s.mr : s.en)}
                  className="rounded-full border border-border bg-panel2/60 px-2.5 py-1 text-xs text-slate-200 hover:border-purple-400/50 disabled:opacity-50">
            <Sparkles size={10} className="mr-1 inline text-purple-300" />{lang === "mr" ? s.mr : s.en}
          </button>
        ))}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); void ask(text); }} className="flex gap-2">
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={1000}
               placeholder="उदा. आज बाजार कसा आहे? / माझ्या चुका सांगा / Explain theta"
               className="flex-1 rounded-lg border border-border bg-panel2 px-3 py-2 text-sm text-slate-100 placeholder:text-muted focus:border-purple-400/60 focus:outline-none" />
        <button disabled={busy || text.trim().length < 2} className="rounded-lg bg-purple-600 px-4 py-2 text-sm font-bold text-white hover:bg-purple-500 disabled:opacity-50">
          <Send size={14} className="mr-1 inline" />विचारा
        </button>
      </form>
    </div>
  );
}
