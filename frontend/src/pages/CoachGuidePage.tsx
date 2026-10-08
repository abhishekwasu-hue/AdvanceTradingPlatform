import { BookOpen, GraduationCap } from "lucide-react";
import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import GuideChat from "../components/GuideChat";
import TradeCoach from "../components/TradeCoach";
import AiAcknowledgementGate from "../components/AiAcknowledgementGate";
import { Card } from "../components/ui";
import { PageHeader } from "../components/primitives";

/** Phase AW: the trade coach and the concept guide, on their own page - the AI Copilot is the
 * live-market strategist; reviewing your trades and learning concepts live here. */
export default function CoachGuidePage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<"coach" | "guide">(() => {
    try { return localStorage.getItem("atp_coach_tab") === "guide" ? "guide" : "coach"; } catch { return "coach"; }
  });
  const choose = (t: "coach" | "guide") => { setTab(t); try { localStorage.setItem("atp_coach_tab", t); } catch { /* storage unavailable */ } };
  if (!user) return <Card><p className="text-sm text-fg-muted">Log in to see your trade review.</p></Card>;
  return (
    <div className="space-y-4">
      <PageHeader title={<span className="inline-flex items-center gap-2"><GraduationCap size={18} /> Coach & Guide</span>} description="The coach shows patterns in your own trades (rules followed or broken); the guide explains the concepts behind them. Decisions are yours." />
      <div className="flex gap-1 border-b border-border">
        {([["coach", "Trade coach", GraduationCap], ["guide", "Guide", BookOpen]] as const).map(([id, label, Icon]) => (
          <button key={id} onClick={() => choose(id)}
                  className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-semibold ${tab === id ? "border-brand text-fg" : "border-transparent text-fg-muted hover:text-fg"}`}>
            <Icon size={14} />{label}
          </button>
        ))}
      </div>
      {/* P0.8-D: the same first-use acknowledgement as the AI Copilot page */}
      <AiAcknowledgementGate>{tab === "coach" ? <TradeCoach /> : <GuideChat plain />}</AiAcknowledgementGate>
    </div>
  );
}
