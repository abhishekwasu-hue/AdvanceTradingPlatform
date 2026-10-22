import { LifeBuoy, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { Button } from "../components/primitives/Button";
import { Dialog } from "../components/primitives/Dialog";
import ShowGetStartedV2 from "../setup/ShowGetStartedV2";
import { QUESTION_MAX_CHARS, SUGGESTED_QUESTIONS, safeLinks, sourceLabel, supportQuestion, type SupportReply } from "./support";

interface Turn { q: string; reply?: SupportReply; error?: string }

/** Z10-f: the help desk. How-to answers from the help and legal pages; it never reads positions or orders and never
 * places, changes or cancels anything. When it cannot answer, "Send feedback" opens the feedback form with the
 * question filled in. Shown only while the `support_bot` flag is on for the organisation. P3: it also offers "Show Get
 * started" (the Dashboard's three setup steps), as Settings → Help does. */
export default function SupportDialog({ open, onOpenChange, onFeedback }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Opens the feedback form with `draft` as its text (the hand-off). */
  onFeedback: (draft: string) => void;
}) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { bottom.current?.scrollIntoView?.({ block: "nearest" }); }, [turns]);

  async function ask(text: string) {
    const q = supportQuestion(text);
    if (!q || busy) return;
    setBusy(true);
    setQuestion("");
    setTurns((t) => [...t, { q }]);
    try {
      const reply = await api.supportAsk(q);
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, reply } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error: String(e).replace(/^Error:\s*/, "") } : x)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={<span className="inline-flex items-center gap-2"><LifeBuoy size={16} aria-hidden="true" /> Help</span>}
      description="How-to answers from the help pages and the legal pages. The help desk cannot see your account, give trading advice or touch orders."
    >
      <div className="space-y-3">
        <ShowGetStartedV2 onDone={() => onOpenChange(false)} />
        {turns.length === 0 && (
          <ul className="flex flex-wrap gap-1.5" aria-label="Suggested questions">
            {SUGGESTED_QUESTIONS.map((q) => (
              <li key={q}>
                <button type="button" onClick={() => void ask(q)}
                        className="rounded-full border border-brand/40 px-3 py-1 text-xs text-brand hover:bg-brand/10">{q}</button>
              </li>
            ))}
          </ul>
        )}
        <div className="space-y-3" role="log" aria-live="polite" aria-label="Help conversation">
          {turns.map((t, i) => (
            <div key={i} className="space-y-1">
              <p className="ml-auto w-fit max-w-[85%] rounded-lg bg-brand/20 px-3 py-1.5 text-sm text-fg"><span className="sr-only">You asked: </span>{t.q}</p>
              {t.reply && (
                <div className="max-w-[95%] rounded-lg bg-surface-2 px-3 py-2 text-sm text-fg">
                  <p className="whitespace-pre-wrap leading-relaxed">{t.reply.answer}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-fg-muted">
                    <span>{sourceLabel(t.reply)}</span>
                    {safeLinks(t.reply.links).map((l) => (
                      <Link key={l.url} to={l.url} onClick={() => onOpenChange(false)}
                            className="rounded-full border border-border px-2 py-0.5 text-fg hover:bg-surface-1">{l.title}</Link>
                    ))}
                  </div>
                  {t.reply.handoff && (
                    <Button className="mt-2" variant="primary" onClick={() => onFeedback(t.reply?.feedback_draft ?? t.q)}>Send feedback</Button>
                  )}
                </div>
              )}
              {t.error && <p className="text-xs text-down" role="alert">{t.error}</p>}
              {!t.reply && !t.error && <p className="text-xs text-fg-muted">Looking it up…</p>}
            </div>
          ))}
          <div ref={bottom} />
        </div>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void ask(question); }}>
          <label htmlFor="support-question" className="sr-only">Your question</label>
          <input id="support-question" value={question} maxLength={QUESTION_MAX_CHARS} autoComplete="off"
                 onChange={(e) => setQuestion(e.target.value)} placeholder="How do I…?"
                 className="min-w-0 flex-1 rounded border border-border bg-surface-2 px-3 py-1.5 text-sm focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand" />
          <Button type="submit" variant="primary" disabled={busy || !supportQuestion(question)} aria-label="Ask">
            <Send size={13} aria-hidden="true" />
          </Button>
        </form>
        <p className="text-xs text-fg-muted">For how-to questions only - never advice on what to buy or sell.</p>
      </div>
    </Dialog>
  );
}
