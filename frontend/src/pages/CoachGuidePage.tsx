import { BookOpen, GraduationCap } from "lucide-react";
import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import GuideChat from "../components/GuideChat";
import TradeCoach from "../components/TradeCoach";
import { Card } from "../components/ui";

/** Phase AW: the trade coach and the concept guide, on their own page - the AI Copilot is the
 * live-market strategist; reviewing your trades and learning concepts live here. */
export default function CoachGuidePage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<"coach" | "guide">(() => {
    try { return localStorage.getItem("atp_coach_tab") === "guide" ? "guide" : "coach"; } catch { return "coach"; }
  });
  const choose = (t: "coach" | "guide") => { setTab(t); try { localStorage.setItem("atp_coach_tab", t); } catch { /* storage unavailable */ } };
  if (!user) return <Card><p className="text-sm text-muted">Log in to see your trade review.</p></Card>;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-sky-300 flex items-center gap-2"><GraduationCap size={18} /> Coach & मार्गदर्शक</h1>
        <p className="text-sm text-slate-300">तुमच्या trades चे विश्लेषण आणि trading च्या संकल्पना - मराठी किंवा English.</p>
      </div>
      <div className="flex gap-1 border-b border-border">
        {([["coach", "Trade coach", GraduationCap], ["guide", "मार्गदर्शक", BookOpen]] as const).map(([id, label, Icon]) => (
          <button key={id} onClick={() => choose(id)}
                  className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-semibold ${tab === id ? "border-sky-400 text-sky-100" : "border-transparent text-muted hover:text-slate-200"}`}>
            <Icon size={14} />{label}
          </button>
        ))}
      </div>
      {tab === "coach" ? <TradeCoach lang="mr" /> : <GuideChat plain />}
    </div>
  );
}
