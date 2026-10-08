import { BookOpen, GraduationCap, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { GuideAnswer, GuideConcept } from "../types";
import { Card, CollapsibleCard } from "./ui";

/**
 * Phase AT: ask the guide. Answers come from the concept library (and, for "what is X doing today",
 * from the market memory); with an AI provider set in Settings the AI answers, grounded on the same
 * notes. P0.9: the page is English; the answers are written in the user's AI language (Settings).
 */
type Lang = "en" | "mr";
const SUGGESTIONS: string[] = [
  "What is RSI?", "Where should a stop-loss go?", "What is NIFTY 50 doing today?", "How is position size calculated?",
  "What is theta?", "What is India VIX?", "How do I avoid revenge trading?", "What is risk : reward?",
];

interface Turn { q: string; a?: GuideAnswer; error?: string }

export default function GuideChat({ plain = false }: { plain?: boolean }) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [browse, setBrowse] = useState(false);
  const [concepts, setConcepts] = useState<GuideConcept[]>([]);
  const [lang, setLang] = useState<Lang>("en");
  useEffect(() => { api.aiPreferences().then((p) => setLang(p.ai_language)).catch(() => undefined); }, []);
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

  const content = (
    <>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <GraduationCap size={14} className="text-emerald-300" />
        <span>Ask about any trading concept, or "What is NIFTY doing today?". Answers come from the concept library and the market memory; with an AI key in Settings the AI answers, in your AI language ({lang === "mr" ? "Marathi" : "English"}, Settings).</span>
        <button onClick={() => setBrowse(!browse)} className="ml-auto rounded border border-border px-2 py-0.5 text-slate-100 hover:bg-panel2"><BookOpen size={11} className="mr-1 inline" />{browse ? "Close the library" : "All concepts"}</button>
      </div>
      {browse && (
        <div className="mb-2 flex flex-wrap gap-1">
          {concepts.map((c) => (
            <button key={c.id} onClick={() => void openConcept(c.id)} className="rounded-full border border-border px-2 py-0.5 text-[11px] text-slate-100 hover:bg-emerald-500/20">{c.title}</button>
          ))}
        </div>
      )}
      <div className="max-h-[380px] space-y-2 overflow-y-auto">
        {turns.length === 0 && (
          <div className="flex flex-wrap gap-1.5">
            {SUGGESTIONS.map((q) => (
              <button key={q} onClick={() => void ask(q)} className="rounded-full border border-emerald-500/40 px-3 py-1 text-xs text-emerald-100 hover:bg-emerald-500/15">{q}</button>
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
                  <span className={`rounded px-1.5 py-0.5 ${t.a.source === "ai" ? "bg-purple-500/30 text-purple-100" : "bg-emerald-500/20 text-emerald-100"}`}>{t.a.source === "ai" ? "AI" : "Concept library"}</span>
                  {t.a.used_market_memory && <span className="rounded bg-sky-500/20 px-1.5 py-0.5 text-sky-100">Market memory</span>}
                  {t.a.related.map((r) => (
                    <button key={r.id} onClick={() => void openConcept(r.id)} className="rounded-full border border-border px-2 py-0.5 text-slate-200 hover:bg-panel2">{r.title}</button>
                  ))}
                </div>
                {t.a.note && <div className="mt-1 text-[11px] text-amber-300">{t.a.note}</div>}
              </div>
            )}
            {t.error && <div className="text-xs text-danger">{t.error}</div>}
            {!t.a && !t.error && <div className="text-xs text-muted">Thinking…</div>}
          </div>
        ))}
        <div ref={bottom} />
      </div>
      <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); void ask(question); }}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Ask anything about trading…"
               className="flex-1 rounded border border-border bg-panel2 px-3 py-1.5 text-sm" />
        <button disabled={busy || question.trim().length < 2} className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-40"><Send size={12} className="mr-1 inline" />Ask</button>
      </form>
      <div className="mt-1 text-[11px] text-muted">Information for learning - never advice on what to buy or sell.</div>
    </>
  );
  return (
    plain
      ? <Card title="Ask the guide">{content}</Card>
      : <CollapsibleCard title="Ask the guide" storageKey="guide" subtitle="Ask about a concept or today's market - click to open">{content}</CollapsibleCard>
  );
}
