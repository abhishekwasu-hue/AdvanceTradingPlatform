import { ChevronLeft, ChevronRight } from "lucide-react";
import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { Link, Navigate, useLocation } from "react-router";
import { Details } from "../ui/numbersFirst";
import AlertChannelsCard from "../components/AlertChannelsCard";
import AiProviderCard from "../components/AiProviderCard";
import ApiKeysCard from "../components/ApiKeysCard";
import AppearanceCard from "../components/AppearanceCard";
import VisionByokCard from "../components/VisionByokCard";
import WebhookCard from "../components/WebhookCard";
import { Card } from "../components/ui";
import TelegramConnectV2 from "../alerts/TelegramConnectV2";
import PhonePushV2 from "../alerts/PhonePushV2";
import SignedWebhooksV2 from "../alerts/SignedWebhooksV2";
import { isNativeApp } from "../native/platform";
import RhythmTelegramV2 from "../rhythm/RhythmTelegramV2";
import ConnectAiPanelV2 from "../aiKeys/ConnectAiPanelV2";
import KeyGuidesV2 from "../aiKeys/KeyGuidesV2";
import { useAuth } from "../auth/AuthContext";
import type { PageProps } from "../routes";
import GoLiveChecklistV2 from "../setup/GoLiveChecklistV2";
import ShowGetStartedV2 from "../setup/ShowGetStartedV2";
import TipsButtonsV2 from "../tours/TipsButtonsV2";
import { SIGN_IN_PATH } from "../ui/BrokerGate";
import { useFeatures } from "../ui/useFeatures";
import { SETTINGS_INDEX, settingsSectionAt, type SettingsSectionId } from "./paths";
import { useLiveHidden } from "./useLiveHidden";
import VisionReviewSwitchesV2 from "./VisionReviewSwitchesV2";

/**
 * Settings redesign part B (B1, behind `ui_v2`): one small page per Settings row, at `/settings/<section>`. The cards
 * are today's components, moved here unchanged; `#<id>` in the address (from the Settings search, or an old link)
 * opens the fold that holds it and scrolls to it.
 */

// C12: the Help centre (flag `help_centre`), its own chunk - the page tips and the words load only with it.
const HelpCentreV2 = lazy(() => import("../support/HelpCentreV2"));

const TITLE: Record<SettingsSectionId, string> = {
  ai: "AI", alerts: "Alerts", appearance: "Appearance", help: "Help & tips", advanced: "Advanced", "risk-limits": "Risk limits",
  "trade-settings": "Trade settings",
};

// M64: Settings › Risk limits, its own chunk.
const RiskLimitsV2 = lazy(() => import("./RiskLimitsV2"));
// M68 PR-0: Settings › Trade settings, its own chunk.
const TradeSettingsV2 = lazy(() => import("./TradeSettingsV2"));

/** `#<id>`: open every fold around (and at) the element, then bring it into view - now, and once the cards loaded. */
function useHashTarget() {
  const location = useLocation();
  useEffect(() => {
    const id = decodeURIComponent(location.hash.slice(1));
    if (!id) return;
    const go = () => {
      const el = document.getElementById(id);
      if (!el) return;
      for (let d: HTMLElement | null = el; d; d = d.parentElement) if (d instanceof HTMLDetailsElement) d.open = true;
      el.scrollIntoView({ block: "start" });
    };
    const timers = [window.setTimeout(go, 0), window.setTimeout(go, 500)];
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [location.hash, location.key]);
}

/** A collapsed part of a section page (Advanced, the alert channels). */
export function Fold({ id, title, line, children, onOpen }: { id: string; title: string; line?: string; children: ReactNode; onOpen?: (open: boolean) => void }) {
  return (
    <details id={id} className="group/fold scroll-mt-20 rounded-panel border border-subtle bg-surface-1 shadow-card" onToggle={(e) => onOpen?.(e.currentTarget.open)}>
      <summary className="flex min-h-14 cursor-pointer list-none items-center gap-space-3 rounded-panel px-space-4 py-space-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:px-space-5 [&::-webkit-details-marker]:hidden">
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="text-t14 font-medium text-fg">{title}</span>
          {line && <span className="text-t13 text-fg-muted">{line}</span>}
        </span>
        <ChevronRight size={18} aria-hidden className="shrink-0 text-fg-muted transition-transform group-open/fold:rotate-90" />
      </summary>
      <div className="space-y-space-4 px-space-2 pb-space-4 md:px-space-4">{children}</div>
    </details>
  );
}

/** Settings › AI's body (also the in-place AI key panel, M75 NAV-2). */
export function AiSection() {
  const oneKey = useFeatures().on("ai_one_key");
  return (
    <div id="ai" className="space-y-space-4">
      {/* Part A: one key for every AI feature. Without `ai_one_key`, today's AI cards. */}
      {oneKey ? <ConnectAiPanelV2 /> : (
        <>
          <KeyGuidesV2 />
          <AiProviderCard />
          <VisionByokCard />
        </>
      )}
    </div>
  );
}

function AlertsSection() {
  const rhythm = useFeatures().on("daily_rhythm");
  // B8: inside the Android app, with mobile_push on, the phone's own notifications come first.
  const mobilePush = useFeatures().on("mobile_push");
  const phone = isNativeApp() && mobilePush;
  // AUD-G2.5: the member's own signed webhooks (flag alert_webhooks; this section is ui_v2 only).
  const webhooks = useFeatures().on("alert_webhooks");
  const [telegramChanged, setTelegramChanged] = useState(0);
  return (
    <div id="alerts" className="space-y-space-4">
      {phone && (
        <div id="phone-notifications" className="scroll-mt-20">
          <Card title="Notifications on this phone"><PhonePushV2 /></Card>
        </div>
      )}
      <div id="telegram" className="scroll-mt-20">
        <Card title="Alerts on your phone"><TelegramConnectV2 onChanged={() => setTelegramChanged((n) => n + 1)} /></Card>
      </div>
      {/* MO4: the daily briefs on the user's own Telegram (default off); read again after Telegram is connected above. */}
      {rhythm && (
        <div id="daily-briefs" className="scroll-mt-20">
          <Card title="Daily briefs on Telegram"><RhythmTelegramV2 key={telegramChanged} /></Card>
        </div>
      )}
      <Fold id="channels" title="More ways to get alerts" line="Email, app notifications, SMS and others">
        <AlertChannelsCard ownBotOnly part="channels" />
      </Fold>
      {webhooks && (
        <div id="webhooks" className="scroll-mt-20">
          <Card title="Webhooks"><SignedWebhooksV2 /></Card>
        </div>
      )}
    </div>
  );
}

export function HelpSection() {
  const { user } = useAuth();
  const helpCentre = useFeatures().on("help_centre");
  return (
    <div data-settings-help className="space-y-space-4">
      {helpCentre && <Suspense fallback={null}><HelpCentreV2 /></Suspense>}
      <Card title="Help">
        <div className="space-y-4">
          {user?.role !== "VIEWER" && (
            <div id="get-started" className="flex scroll-mt-20 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm text-fg-muted">Bring back the Get started steps on your Dashboard: broker, trading bot and Telegram alerts.</p>
              <ShowGetStartedV2 className="self-start sm:self-auto" />
            </div>
          )}
          <div id="tips" className="flex scroll-mt-20 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between" data-tips-row>
            {/* UX-11 U1: the tips sentence on demand; the Get started line above stays the one visible line. */}
            <Details id="help.tips" label="About tips"><p>Tips explain each page on your first visit. See them all again, or turn them off.</p></Details>
            <TipsButtonsV2 className="self-start sm:self-auto" />
          </div>
        </div>
      </Card>
    </div>
  );
}

function AdvancedSection() {
  const features = useFeatures();
  const liveHidden = useLiveHidden();
  const location = useLocation();
  // The checklist loads only once opened (as today), or when the address points at it.
  const [goLiveOpen, setGoLiveOpen] = useState(location.hash === "#go-live");
  useEffect(() => { if (location.hash === "#go-live") setGoLiveOpen(true); }, [location.hash]);
  return (
    <div id="advanced" className="space-y-space-3">
      <p className="text-t14 text-fg-muted">For connecting other tools. Most people never need these.</p>
      <Fold id="webhook" title="TradingView webhook" line="Send TradingView alerts here">
        <WebhookCard />
      </Fold>
      {/* B1: personal API keys only while the public API is on. */}
      {features.on("public_api") && (
        <Fold id="api-keys" title="Personal API keys" line="For your own programs">
          <ApiKeysCard />
        </Fold>
      )}
      <Fold id="own-bot" title="Your own Telegram bot" line="Only if your organisation has its own bot">
        <AlertChannelsCard part="own-bot" />
      </Fold>
      {/* REV-1 M2: the mail-server, SMS-gateway (DLT) and webhook set-up - out of Settings → Alerts. */}
      <Fold id="alert-delivery" title="Email, SMS and webhook alerts" line="Your own mail server, SMS provider or app">
        <AlertChannelsCard part="delivery" />
      </Fold>
      {features.on("ai_one_key") && (
        <Fold id="vision" title="Vision background reviews" line="Chart comments in the background with your AI key">
          <VisionReviewSwitchesV2 />
        </Fold>
      )}
      {/* TERM-2 (owner 30 Oct): the Terminal as it was, until the owner approves the rebuild. */}
      {features.on("terminal") && (
        <Fold id="terminal-classic" title="Previous Terminal" line="The Terminal before the rebuild">
          <Link to="/terminal?view=classic" data-terminal-classic className="inline-flex min-h-11 items-center font-semibold text-brand hover:underline md:min-h-9">Open the previous Terminal</Link>
        </Fold>
      )}
      {/* B3: nothing about LIVE while Phase 0 is PAPER only (the admin keeps the platform checklist on /admin). */}
      {!liveHidden && (
        <Fold id="go-live" title="Go-live checklist" line="What is left before real-money trading" onOpen={setGoLiveOpen}>
          {goLiveOpen && <GoLiveChecklistV2 />}
        </Fold>
      )}
    </div>
  );
}

const BODY: Record<SettingsSectionId, () => ReactNode> = {
  ai: () => <AiSection />,
  alerts: () => <AlertsSection />,
  appearance: () => <div id="appearance" className="scroll-mt-20"><AppearanceCard /></div>,
  help: () => <HelpSection />,
  advanced: () => <AdvancedSection />,
  "risk-limits": () => <Suspense fallback={null}><RiskLimitsV2 /></Suspense>,
  "trade-settings": () => <Suspense fallback={null}><TradeSettingsV2 /></Suspense>,
};

export default function SettingsSectionV2(_props: PageProps = {}) {
  const { user, loading } = useAuth();
  const location = useLocation();
  useHashTarget();
  const section = settingsSectionAt(`/${location.pathname.replace(/^\/+|\/+$/g, "")}`);
  if (!loading && !user) return <Navigate to={SIGN_IN_PATH} replace />;
  if (!section) return <Navigate to={SETTINGS_INDEX} replace />;
  return (
    <div className="mx-auto w-full max-w-[760px] space-y-space-4" data-page={`settings-${section.id}-v2`}>
      <div className="space-y-space-1">
        <Link to={SETTINGS_INDEX} data-back
          className="-ml-space-2 inline-flex min-h-11 items-center gap-space-1 rounded-control px-space-2 text-t14 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          <ChevronLeft size={16} aria-hidden /> Settings
        </Link>
        <h1 className="text-t20 font-semibold text-fg">{TITLE[section.id]}</h1>
      </div>
      {BODY[section.id]()}
    </div>
  );
}
