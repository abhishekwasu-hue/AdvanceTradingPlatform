import { AnimatePresence, m } from "framer-motion";
import { Sparkles } from "lucide-react";
import { lazy, Suspense, useEffect, type ComponentType } from "react";
import { I18nextProvider } from "react-i18next";
import { useNavigate, useParams } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { Card } from "../components/ui";
import "../copilot/copilot.css";
import AICore from "../copilot/components/AICore";
import { toneFor, type CoreVariant } from "../copilot/components/aiCoreTypes";
import CopilotMotion from "../copilot/components/CopilotMotion";
import CopilotTabBar from "../copilot/components/CopilotTabBar";
import FirstUseModal from "../copilot/components/FirstUseModal";
import { CopilotDataProvider, useCopilotData } from "../copilot/data";
import { copilotI18n, useCopilotT } from "../copilot/i18n";
import { copilotPath, DEFAULT_TAB, isCopilotTab, type CopilotTab } from "../copilot/tabs";
import { TabSkeleton } from "../copilot/tabs/shared";

/**
 * The AI Copilot (redesign): seven tabs with their own addresses (/copilot/<tab>), an "AI Core" hero coloured by
 * today's regime, glass + tilt cards, motion that stops when motion is reduced, and every string through i18n keys.
 * It explains rules and data; it never places an order (ADR-0006) and every strategy shows its rules in words.
 * Each tab is its own chunk; the 3D scene is another, loaded when the browser is idle.
 */
const TAB_COMPONENTS: Record<CopilotTab, ComponentType> = {
  "market-pulse": lazy(() => import("../copilot/tabs/MarketPulseTab")),
  "strategy-lab": lazy(() => import("../copilot/tabs/StrategyLabTab")),
  "idea-builder": lazy(() => import("../copilot/tabs/IdeaBuilderTab")),
  ask: lazy(() => import("../copilot/tabs/AskCopilotTab")),
  watchtower: lazy(() => import("../copilot/tabs/WatchtowerTab")),
  "news-radar": lazy(() => import("../copilot/tabs/NewsRadarTab")),
  coach: lazy(() => import("../copilot/tabs/CoachTab")),
};

const TAB_KEY = "atp_copilot_tab2";
const CORE_KEY = "atp_copilot_core";

function storedTab(): CopilotTab {
  try { const v = localStorage.getItem(TAB_KEY) ?? undefined; return isCopilotTab(v) ? v : DEFAULT_TAB; } catch { return DEFAULT_TAB; }
}
function storedCore(): CoreVariant {
  try { return localStorage.getItem(CORE_KEY) === "particles" ? "particles" : "orb"; } catch { return "orb"; }
}

function Hero() {
  const t = useCopilotT();
  const { brief } = useCopilotData();
  const kind = brief?.day_type.kind ?? "UNKNOWN";
  const tone = toneFor(kind);
  return (
    <section className="copilot-hero relative overflow-hidden rounded-2xl border border-border p-4 md:p-6" aria-labelledby="copilot-title">
      <div className="flex items-center gap-4">
        <div className="min-w-0 flex-1">
          <div className="mb-1 inline-flex items-center gap-1.5 rounded-full border border-ai/40 bg-ai/10 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-ai">
            <Sparkles size={12} aria-hidden />{t("shell.badge")}
          </div>
          <h1 id="copilot-title" className="text-2xl font-bold tracking-tight text-fg md:text-3xl">{t("shell.title")}</h1>
          <p className="mt-1 max-w-xl text-sm text-fg-muted">{t("shell.tagline")}</p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full border border-border bg-surface-2/70 px-2.5 py-1 text-fg" data-testid="hero-regime">
              {t("shell.regime")}: <b>{t(`pulse.regime.${kind}`)}</b>
            </span>
            <span className="rounded-full border border-border bg-surface-2/70 px-2.5 py-1 text-fg-muted">{t("shell.decisionsYours")}</span>
          </div>
        </div>
        <AICore tone={tone} variant={storedCore()} className="h-28 w-28 shrink-0 md:h-44 md:w-44" />
      </div>
    </section>
  );
}

function Tabs() {
  const params = useParams<{ tab?: string }>();
  const navigate = useNavigate();
  const { pending, newHighNews } = useCopilotData();
  const fromUrl = isCopilotTab(params.tab) ? params.tab : undefined;
  const tab: CopilotTab = fromUrl ?? storedTab();
  useEffect(() => {
    if (!fromUrl) navigate(copilotPath(tab), { replace: true });          // /copilot (or an unknown tab) -> a real address
    else { try { localStorage.setItem(TAB_KEY, fromUrl); } catch { /* storage unavailable */ } }
  }, [fromUrl]); // eslint-disable-line react-hooks/exhaustive-deps
  const Panel = TAB_COMPONENTS[tab];
  return (
    <>
      <CopilotTabBar active={tab} onSelect={(next) => navigate(copilotPath(next))} badges={{ watchtower: pending.length, "news-radar": newHighNews }} />
      <AnimatePresence mode="wait" initial={false}>
        <m.div key={tab} role="tabpanel" id={`copilot-panel-${tab}`} aria-labelledby={`copilot-tab-${tab}`} className="mt-4"
               initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}>
          <Suspense fallback={<TabSkeleton />}><Panel /></Suspense>
        </m.div>
      </AnimatePresence>
    </>
  );
}

function Shell() {
  const t = useCopilotT();
  const { user } = useAuth();
  if (!user) return <Card><p className="text-sm text-fg-muted">{t("shell.signIn")}</p></Card>;
  return (
    <CopilotMotion>
      <div className="space-y-4" data-testid="copilot-page">
        {/* The first-use acknowledgement comes before anything AI-written (P0.8-D). */}
        <FirstUseModal placeholder={<StaticHero />}>
          <CopilotDataProvider>
            <Hero />
            <Tabs />
          </CopilotDataProvider>
        </FirstUseModal>
        <p className="text-[11px] leading-relaxed text-fg-muted">{t("compliance.footer")}</p>
      </div>
    </CopilotMotion>
  );
}

/** The hero before the acknowledgement: no market data, the neutral core. */
function StaticHero() {
  const t = useCopilotT();
  return (
    <section className="copilot-hero relative overflow-hidden rounded-2xl border border-border p-4 md:p-6">
      <div className="flex items-center gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold tracking-tight text-fg md:text-3xl">{t("shell.title")}</h1>
          <p className="mt-1 max-w-xl text-sm text-fg-muted">{t("shell.tagline")}</p>
        </div>
        <AICore tone="neutral" className="h-28 w-28 shrink-0 md:h-44 md:w-44" />
      </div>
    </section>
  );
}

export default function AiCopilotPage() {
  return <I18nextProvider i18n={copilotI18n}><Shell /></I18nextProvider>;
}
