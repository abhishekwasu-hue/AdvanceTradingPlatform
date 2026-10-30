import { ChevronRight, Plug } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { BROKER_PATH } from "../broker/paths";
import AlertChannelsCard from "../components/AlertChannelsCard";
import TelegramConnectV2 from "../alerts/TelegramConnectV2";
import AiProviderCard from "../components/AiProviderCard";
import ApiKeysCard from "../components/ApiKeysCard";
import BillingCard from "../components/BillingCard";
import VisionByokCard from "../components/VisionByokCard";
import WebhookCard from "../components/WebhookCard";
import { Card } from "../components/ui";
import AppearanceCard from "../components/AppearanceCard";
import { PageHeader } from "../components/primitives";
import { SetupLinkV2 } from "../setup/SetupChipV2";
import { GO_LIVE_PATH } from "../setup/getStarted";
import GoLiveChecklistV2 from "../setup/GoLiveChecklistV2";
import ShowGetStartedV2 from "../setup/ShowGetStartedV2";
import { useFeatures } from "../ui/useFeatures";
import KeyGuidesV2 from "../aiKeys/KeyGuidesV2";
import ConnectAiPanelV2 from "../aiKeys/ConnectAiPanelV2";
import TipsButtonsV2 from "../tours/TipsButtonsV2";

/**
 * Settings. P2b: the broker moved to its own page (src/broker/BrokerPage.tsx) - Settings keeps only a link to it. What
 * the old broker area also held moved too: the TradingView webhook to Integrations here, the Vision key to AI here,
 * and the static IP, the Upstox redirect URL and the broker accounts to "Advanced" on the Broker page.
 */

export const BROKER_PAGE_PATH = BROKER_PATH;

function Section({ id, title, tour, children }: { id: string; title: string; tour?: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} data-tour={tour} className="space-y-4 scroll-mt-20">
      <h2 id={`${id}-title`} className="pt-2 text-sm font-semibold text-fg">{title}</h2>
      {children}
    </section>
  );
}

export function BrokerLinkCard() {
  return (
    <Card title="Broker">
      <Link to={BROKER_PAGE_PATH} data-broker-link
        className="-m-1 flex min-h-11 items-center gap-3 rounded-lg p-1 text-sm font-medium text-fg hover:bg-surface-2">
        <Plug size={18} aria-hidden className="shrink-0 text-fg-muted" />
        <span className="flex-1">Connect, renew or remove your broker on the Broker page</span>
        <ChevronRight size={16} aria-hidden className="shrink-0 text-fg-muted" />
      </Link>
    </Card>
  );
}

export default function SettingsPage() {
  const features = useFeatures();
  const v2 = features.on("ui_v2");
  // Settings redesign A: one key for every AI feature - Settings → AI shows only the Connect AI panel.
  const oneKey = v2 && features.on("ai_one_key");
  const { user, loading: authLoading } = useAuth();
  // P3: Settings → Advanced holds the Go-live checklist; "Prepare for LIVE" (/settings#go-live) opens it, and the
  // Get started card's Telegram step leads to /settings#alerts.
  const location = useLocation();
  const [advancedOpen, setAdvancedOpen] = useState(() => ADVANCED_HASHES.has(location.hash));

  useEffect(() => {
    const id = location.hash.slice(1);
    if (!id || authLoading || !user) return;
    if (ADVANCED_HASHES.has(location.hash)) setAdvancedOpen(true);
    // After the shell's own scroll to the top of a new page, and once more when the cards above have loaded.
    const go = () => document.getElementById(id)?.scrollIntoView({ block: "start" });
    const timers = [window.setTimeout(go, 0), window.setTimeout(go, 500)];
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [location.hash, location.key, authLoading, user]);

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <PageHeader title="Settings" />
        <AppearanceCard />
        <Card>
          <p className="text-sm text-fg-muted">Log in from the Account tab to manage your settings.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Settings"
        actions={<Link to={GO_LIVE_PATH} className="inline-flex min-h-11 items-center gap-1 rounded-md text-sm font-medium text-brand hover:underline md:min-h-0">Prepare for LIVE <ChevronRight size={14} aria-hidden /></Link>} />
      {/* UI2-7: Settings → Setup (the wizard), only while ui_v2 is on; off, nothing renders. P3: the go-live checklist is
          under Advanced, below. */}
      {v2 && <SetupLinkV2 />}
      <div data-tour="settings-appearance"><AppearanceCard /></div>
      <BrokerLinkCard />
      <BillingCard />
      <div id="alerts" data-tour="settings-alerts" className="scroll-mt-4 space-y-4">
        {/* UX2 fix 5: with ui_v2, Telegram in one tap first; the bot token and chat id move into the card's "own bot" part. */}
        {v2 && (
          <Card title="Alerts on your phone">
            <TelegramConnectV2 />
          </Card>
        )}
        <AlertChannelsCard ownBotOnly={v2} />
      </div>

      <Section id="integrations" title="Integrations">
        <WebhookCard />
        <ApiKeysCard />
      </Section>

      {/* MO2 part B: the beginner key guides first, then the cards that use a key. */}
      <Section id="ai" title="AI" tour="settings-ai">
        {oneKey ? <ConnectAiPanelV2 /> : (
          <>
            <KeyGuidesV2 />
            <AiProviderCard />
            <VisionByokCard />
          </>
        )}
      </Section>

      {(user.role !== "VIEWER" || v2) && (
        <div data-tour="settings-help">
          <Card title="Help">
            <div className="space-y-4">
              {user.role !== "VIEWER" && (
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm text-fg-muted">Bring back the Get started steps on your Dashboard: broker, trading bot and Telegram alerts.</p>
                  <ShowGetStartedV2 className="self-start sm:self-auto" />
                </div>
              )}
              {/* MO2 part C: the first-time tips (page tours and the "What is this page?" cards) live in the v2 shell. */}
              {v2 && (
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between" data-tips-row>
                  <p className="text-sm text-fg-muted">Tips explain each page on your first visit. See them all again, or turn them off.</p>
                  <TipsButtonsV2 className="self-start sm:self-auto" />
                </div>
              )}
            </div>
          </Card>
        </div>
      )}

      <details id="advanced" open={advancedOpen} onToggle={(e) => setAdvancedOpen(e.currentTarget.open)}
        className="group/advanced scroll-mt-4">
        <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 rounded-md text-sm font-semibold text-fg [&::-webkit-details-marker]:hidden">
          <ChevronRight size={16} aria-hidden className="shrink-0 text-fg-muted transition-transform group-open/advanced:rotate-90" />
          Advanced
        </summary>
        <div id="go-live" className="scroll-mt-4 pt-2">
          {advancedOpen && <GoLiveChecklistV2 />}
        </div>
      </details>
    </div>
  );
}

/** The addresses that open Settings → Advanced (the Go-live checklist). */
const ADVANCED_HASHES: ReadonlySet<string> = new Set(["#go-live", "#advanced"]);
