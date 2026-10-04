import { BookOpen, GraduationCap, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { GuideAnswer, GuideConcept } from "../types";
import { Card } from "./ui";

/**
 * Phase AT: ask the guide. Answers come from the concept library (and, for "what is X doing today",
 * from the market memory); with an AI provider set in Settings the AI answers, grounded on the same
 * notes. Marathi or English - whichever the question is written in.
 */
type Lang = "en" | "mr";
const SUGGESTIONS: { q: string; lang: Lang }[] = [
  { q: "RSI म्हणजे काय?", lang: "mr" }, { q: "Stop-loss कुठे ठेवावा?", lang: "mr" }, { q: "आज NIFTY 50 चा कल काय?", lang: "mr" },
  { q: "Position size किती घ्यावा?", lang: "mr" }, { q: "Theta म्हणजे काय?", lang: "mr" }, { q: "India VIX म्हणजे काय?", lang: "mr" },
  { q: "Revenge trading कसे टाळायचे?", lang: "mr" }, { q: "What is risk : reward?", lang: "en" },
];

interface Turn { q: string; a?: GuideAnswer; error?: string }

export default function GuideChat() {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [browse, setBrowse] = useState(false);
  const [concepts, setConcepts] = useState<GuideConcept[]>([]);
  const [lang, setLang] = useState<Lang>("mr");
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest" }); }, [turns]);
  useEffect(() => { if (browse && concepts.length === 0) api.aiConcepts(lang).then((r) => setConcepts(r.concepts)).catch(() => {}); }, [browse, lang, concepts.length]);

  async function ask(q: string) {
    const text = q.trim();
    if (text.length < 2 || busy) return;
    setBusy(true); setQuestion("");
    setTurns((t) => [...t, { q: text }]);
    try {
      const a = await api.aiAsk(text);
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, a } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error: String(e).replace(/^Error:\s*/, "") } : x)));
    } finally { setBusy(false); }
  }

  async function openConcept(id: string) {
    setBusy(true);
    try {
      const c = await api.aiConcept(id, lang);
      setTurns((t) => [...t, { q: c.title, a: { answer: c.body ?? "", source: "library", concepts: [c], related: (c.related ?? []).map((r) => ({ id: r, title: r })), used_market_memory: false } }]);
    } finally { setBusy(false); }
  }

  return (
    <Card title="मार्गदर्शक विचारा · Ask the guide">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <GraduationCap size={14} className="text-emerald-300" />
        <span>Trading च्या कोणत्याही संकल्पनेबद्दल, किंवा "आज NIFTY चा कल काय?" असे विचारा - मराठी किंवा English. उत्तरे ज्ञानकोश आणि market च्या साठ्यावरून; Settings मध्ये AI key असेल तर AI उत्तर देतो.</span>
        <button onClick={() => setBrowse(!browse)} className="ml-auto rounded border border-border px-2 py-0.5 text-slate-100 hover:bg-panel2"><BookOpen size={11} className="mr-1 inline" />{browse ? "ज्ञानकोश बंद" : "सगळ्या संकल्पना"}</button>
      </div>
      {browse && (
        <div className="mb-2 flex flex-wrap gap-1">
          <button onClick={() => { setLang(lang === "mr" ? "en" : "mr"); setConcepts([]); }} className="rounded-full border border-sky-500/50 px-2 py-0.5 text-[11px] text-sky-200">{lang === "mr" ? "English" : "मराठी"}</button>
          {concepts.map((c) => (
            <button key={c.id} onClick={() => void openConcept(c.id)} className="rounded-full border border-border px-2 py-0.5 text-[11px] text-slate-100 hover:bg-emerald-500/20">{c.title}</button>
          ))}
        </div>
      )}
      <div className="max-h-[380px] space-y-2 overflow-y-auto">
        {turns.length === 0 && (
          <div className="flex flex-wrap gap-1.5">
            {SUGGESTIONS.map((s) => (
              <button key={s.q} onClick={() => void ask(s.q)} className="rounded-full border border-emerald-500/40 px-3 py-1 text-xs text-emerald-100 hover:bg-emerald-500/15">{s.q}</button>
            ))}
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className="space-y-1">
            <div className="ml-auto w-fit max-w-[85%] rounded-lg bg-brand/30 px-3 py-1.5 text-sm text-white">{t.q}</div>
            {t.a && (
              <div className="max-w-[95%] rounded-lg bg-panel3 px-3 py-2 text-sm text-slate-100">
                <div className="whitespace-pre-wrap leading-relaxed">{t.a.answer}</div>
                <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[11px]">
                  <span className={`rounded px-1.5 py-0.5 ${t.a.source === "ai" ? "bg-purple-500/30 text-purple-100" : "bg-emerald-500/20 text-emerald-100"}`}>{t.a.source === "ai" ? "AI" : "ज्ञानकोश"}</span>
                  {t.a.used_market_memory && <span className="rounded bg-sky-500/20 px-1.5 py-0.5 text-sky-100">market चा साठा</span>}
                  {t.a.related.map((r) => (
                    <button key={r.id} onClick={() => void openConcept(r.id)} className="rounded-full border border-border px-2 py-0.5 text-slate-200 hover:bg-panel2">{r.title}</button>
                  ))}
                </div>
                {t.a.note && <div className="mt-1 text-[11px] text-amber-300">{t.a.note}</div>}
              </div>
            )}
            {t.error && <div className="text-xs text-danger">{t.error}</div>}
            {!t.a && !t.error && <div className="text-xs text-muted">विचार करत आहे…</div>}
          </div>
        ))}
        <div ref={bottom} />
      </div>
      <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); void ask(question); }}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="तुमचा प्रश्न लिहा… / Ask anything about trading"
               className="flex-1 rounded border border-border bg-panel2 px-3 py-1.5 text-sm" />
        <button disabled={busy || question.trim().length < 2} className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-40"><Send size={12} className="mr-1 inline" />विचारा</button>
      </form>
      <div className="mt-1 text-[11px] text-muted">शिक्षणासाठी माहिती - कोणता share घ्यायचा/विकायचा असा सल्ला नाही.</div>
    </Card>
  );
}
